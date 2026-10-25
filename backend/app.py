import functools
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta

from flask import Flask, abort, g, jsonify, request, send_from_directory
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.utils import secure_filename
from pydantic import BaseModel

try:  # optional: load .env if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import db as dbm
import services as svc

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bhumisetu")

app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=20 * 1024 * 1024)   # whole request; each file type has its own limit


def _load_secret() -> str:
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(BASE_DIR, "instance", "secret_key")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(secrets.token_hex(32))
        os.chmod(path, 0o600)
    with open(path) as fh:
        return fh.read().strip()


SECRET = _load_secret()
app.teardown_appcontext(dbm.close_db)
AADHAAR_OWN_ONLY = os.environ.get("AADHAAR_SEARCH_OWN_ONLY", "0") == "1"


def backfill_sample_identifiers():
    """Give the demo records their property id / bill / tax numbers and a hashed owner Aadhaar.
    Runs on every start, so it also upgrades a database created by an older version."""
    db = dbm.get_db()
    for doc_no, pid, elec, tax, aadhaar in dbm.SAMPLE_IDS:
        db.execute(
            """UPDATE land_records SET property_id=?, electricity_bill_no=?, property_tax_no=?,
               owner_aadhaar_ref=?, owner_aadhaar_last4=? WHERE doc_no=? AND source='sample'""",
            (pid, elec, tax, svc.aadhaar_ref(SECRET, aadhaar), aadhaar[-4:], doc_no))
    db.commit()


with app.app_context():
    dbm.init_db()
    backfill_sample_identifiers()

OTP_MAX_ATTEMPTS = 5
RESEND_COOLDOWN = 30
MAX_SENDS_PER_HOUR = 6
GENERIC_LOGIN_MSG = "If an account exists for that email or mobile number, we've sent a code."


# --------------------------------------------------------------------- helpers
def err(message, status=400, **extra):
    return jsonify(error=message, **extra), status


# ------------------------------------------------------------------------ CORS
# The website lives on another domain (GitHub Pages), so the browser only lets it call this API
# if the API says so. List the website's address(es) in ALLOWED_ORIGINS, e.g.
#   ALLOWED_ORIGINS=https://yourname.github.io
# (no path, no trailing slash). Local development origins are allowed only while OTP_DEV_MODE=1.
DEV_ORIGINS = {"http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5500",
               "http://127.0.0.1:5500", "http://localhost:3000", "http://127.0.0.1:3000"}


def allowed_origins():
    listed = {o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()}
    return listed | (DEV_ORIGINS if svc.dev_mode() else set())


@app.before_request
def cors_preflight():
    if request.method == "OPTIONS":
        return "", 204


# ------------------------------------------------------- login tokens (no cookies)
# Cookies set by this server would be "third-party" cookies for a page on github.io, and many browsers and
# Android WebView block those. So a login is a random token the website keeps and sends in an
# "Authorization: Bearer ..." header. Only its SHA-256 hash is stored here, and it can be revoked (logout).
SESSION_DAYS, SESSION_MAX_DAYS, MAX_SESSIONS_PER_USER = 7, 30, 10
CHALLENGE_TTL, FILE_LINK_TTL, NONCE_TTL = 600, 300, 600


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def signer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(SECRET, salt=salt)


def start_session(user_id):
    db, now = dbm.get_db(), int(time.time())
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now,))
    db.execute("""INSERT INTO auth_sessions (user_id, token_hash, created_at, expires_at, last_used_at, user_agent)
                  VALUES (?,?,?,?,?,?)""",
               (user_id, hash_token(token), now, now + SESSION_DAYS * 86400, now,
                request.headers.get("User-Agent", "")[:200]))
    db.execute("""DELETE FROM auth_sessions WHERE user_id=? AND id NOT IN
                  (SELECT id FROM auth_sessions WHERE user_id=? ORDER BY id DESC LIMIT ?)""",
               (user_id, user_id, MAX_SESSIONS_PER_USER))
    db.commit()
    return token


def user_from_token(token):
    """Returns the active user row for a bearer token, extending the session while it is in use."""
    if not token or len(token) > 128:
        return None, None
    db, now = dbm.get_db(), int(time.time())
    row = db.execute(
        """SELECT u.*, s.id AS sid, s.created_at AS s_created, s.expires_at AS s_expires, s.last_used_at AS s_used
           FROM auth_sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash=? AND u.status='active'""", (hash_token(token),)).fetchone()
    if not row or row["s_expires"] < now:
        return None, None
    if now - row["s_used"] > 3600:
        db.execute("UPDATE auth_sessions SET last_used_at=?, expires_at=? WHERE id=?",
                   (now, min(now + SESSION_DAYS * 86400, row["s_created"] + SESSION_MAX_DAYS * 86400), row["sid"]))
        db.commit()
    return row, row["sid"]


@app.before_request
def load_user():
    g.user, g.session_id = None, None
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        g.user, g.session_id = user_from_token(header[7:].strip())


@app.after_request
def add_headers(resp):
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin and origin in allowed_origins():
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Max-Age"] = "600"
    resp.headers.add("Vary", "Origin")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if not request.path.startswith("/files/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.errorhandler(404)
def not_found(_e):
    return err("Not found.", 404)


@app.errorhandler(405)
def bad_method(_e):
    return err("Method not allowed.", 405)


@app.errorhandler(413)
def too_large(_e):
    return err("The upload is too large. Use smaller photos.", 413)


@app.errorhandler(403)
def forbidden(_e):
    return err("You don't have access to this.", 403)


@app.errorhandler(500)
def server_error(_e):
    return err("Something went wrong on our side. Please try again.", 500)


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not g.user:
            return err("Please log in to continue.", 401, code="login_required")
        return fn(*a, **kw)
    return wrapper


def kyc_required(fn):
    """Server-side gate: hiding buttons in the UI is not access control."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if g.user["role"] != "registrar" and g.user["kyc_status"] != "verified":
            return err("Complete Aadhaar KYC to search land records.", 403, code="kyc_required")
        return fn(*a, **kw)
    return wrapper


def registrar_required(fn):
    """Sub-Registrar accounts are created by an administrator (manage.py), never by sign-up."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if g.user["role"] != "registrar":
            return err("This action is only for Sub-Registrars.", 403, code="registrar_required")
        return fn(*a, **kw)
    return wrapper


# ------------------------------------------------------------------ OTP engine
def issue_otp(user, purpose, channel, destination):
    db, now = dbm.get_db(), int(time.time())
    if not destination:
        return {"ok": False, "status": 400, "error": "This account has no mobile number. Choose Email."}
    # Limits are keyed on the destination, so re-registering or switching accounts
    # can't be used to spam one email address / phone number with codes.
    last = db.execute(
        "SELECT created_at FROM otps WHERE destination=? AND channel=? ORDER BY id DESC LIMIT 1",
        (destination, channel)).fetchone()
    if last and now - last["created_at"] < RESEND_COOLDOWN:
        wait = RESEND_COOLDOWN - (now - last["created_at"])
        return {"ok": False, "error": f"Please wait {wait}s before requesting another code.",
                "status": 429, "retry_after": wait}
    sent = db.execute(
        "SELECT COUNT(*) FROM otps WHERE destination=? AND created_at>?", (destination, now - 3600)).fetchone()[0]
    if sent >= MAX_SENDS_PER_HOUR:
        return {"ok": False, "error": "Too many codes requested. Try again in an hour.", "status": 429}

    code = svc.generate_otp()
    db.execute("UPDATE otps SET consumed=1 WHERE user_id=? AND purpose=? AND consumed=0",
               (user["id"], purpose))
    cur = db.execute(
        """INSERT INTO otps (user_id, purpose, channel, destination, code_hash, expires_at, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (user["id"], purpose, channel, destination,
         svc.hash_otp(SECRET, user["id"], purpose, code), now + svc.OTP_TTL, now))
    db.commit()

    result = svc.send_otp(channel, destination, code)
    if not result["ok"]:
        db.execute("DELETE FROM otps WHERE id=?", (cur.lastrowid,))
        db.commit()
        return {"ok": False, "status": 502,
                "error": f"We couldn't send the code by {channel}. Try another option."}
    out = {"ok": True}
    # In demo mode the code is printed to the server log (see services.send_otp), never sent to the browser:
    # anyone who could read it from the response could log in as anybody. OTP_RETURN_IN_RESPONSE=1 exists only
    # for automated tests and local scripts, and is ignored unless OTP_DEV_MODE=1.
    if result["dev"] and svc.dev_mode() and os.environ.get("OTP_RETURN_IN_RESPONSE") == "1":
        out["dev_otp"] = code
    return out


def check_otp(user_id, purpose, code):
    db, now = dbm.get_db(), int(time.time())
    row = db.execute(
        "SELECT * FROM otps WHERE user_id=? AND purpose=? AND consumed=0 ORDER BY id DESC LIMIT 1",
        (user_id, purpose)).fetchone()
    if not row:
        return {"ok": False, "error": "No active code. Request a new one."}
    if row["expires_at"] < now:
        db.execute("UPDATE otps SET consumed=1 WHERE id=?", (row["id"],)); db.commit()
        return {"ok": False, "error": "That code has expired. Request a new one."}
    given = svc.hash_otp(SECRET, user_id, purpose, code)
    if not hmac.compare_digest(row["code_hash"], given):
        attempts = row["attempts"] + 1
        dead = attempts >= OTP_MAX_ATTEMPTS
        db.execute("UPDATE otps SET attempts=?, consumed=? WHERE id=?", (attempts, int(dead), row["id"]))
        db.commit()
        if dead:
            return {"ok": False, "error": "Too many wrong codes. Request a new one."}
        return {"ok": False, "error": f"Incorrect code. {OTP_MAX_ATTEMPTS - attempts} attempt(s) left."}
    db.execute("UPDATE otps SET consumed=1 WHERE id=?", (row["id"],)); db.commit()
    return {"ok": True, "channel": row["channel"]}


def make_challenge(uid, purpose, channel):
    """Stands in for the old cookie: a signed, expiring note saying 'a code was sent for this account'.
    The browser hands it back together with the 6-digit code. It proves nothing by itself."""
    return signer("otp-challenge").dumps({"u": uid, "p": purpose, "c": channel})


def read_challenge(token):
    try:
        return signer("otp-challenge").loads(str(token or ""), max_age=CHALLENGE_TTL)
    except BadSignature:                    # tampered or expired (SignatureExpired is a BadSignature)
        return None


def destination_for(user, channel):
    return user["email"] if channel == "email" else user["mobile"]


# -------------------------------------------------------- public info + "who am I"
@app.get("/")
def root():
    return jsonify(service="BhumiSetu API", ok=True)


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/api/config")
def api_config():
    """Everything the website needs before anyone is logged in."""
    return jsonify(ok=True, app_flavor=app_flavor(), dev_mode=svc.dev_mode(), channels=svc.channel_status(),
                   google_client_id=svc.google_client_id(), kyc_reasons=KYC_REASONS)


def user_json(u):
    return {"id": u["id"], "name": u["name"], "email": u["email"], "mobile": u["mobile"], "role": u["role"],
            "office": u["office"], "pin_codes": u["pin_codes"], "kyc_status": u["kyc_status"],
            "kyc_name": u["kyc_name"], "aadhaar_last4": u["aadhaar_last4"], "kyc_verified_at": u["kyc_verified_at"],
            "mobile_verified": bool(u["mobile_verified"]), "email_verified": bool(u["email_verified"]),
            "google_linked": bool(u["google_sub"]), "created_at": u["created_at"], "last_login_at": u["last_login_at"]}


@app.get("/api/me")
@login_required
def api_me():
    u = g.user
    kyc = None
    if u["role"] != "registrar":
        k = latest_kyc(u["id"])
        if k:
            kyc = {"status": k["status"], "full_name": k["full_name"], "dob": k["dob"], "gender": k["gender"],
                   "address": k["address"], "city": k["city"], "district": k["district"], "state": k["state"],
                   "pincode": k["pincode"], "aadhaar_last4": k["aadhaar_last4"], "submitted_at": k["submitted_at"],
                   "review_note": k["review_note"],
                   "offices": offices_for_pin(k["pincode"]) if k["status"] == "pending" else []}
    return jsonify(ok=True, user=user_json(u), kyc=kyc,
                   can_search=u["role"] == "registrar" or u["kyc_status"] == "verified",
                   pending_kyc=kyc_pending_count())


@app.get("/api/me/searches")
@login_required
def api_my_searches():
    rows = dbm.get_db().execute(
        "SELECT query, results_count, created_at FROM search_log WHERE user_id=? ORDER BY id DESC LIMIT 5",
        (g.user["id"],)).fetchall()
    return jsonify(ok=True, results=[dict(r) for r in rows])


# ------------------------------------------------------------- auth: register
@app.post("/api/register")
def api_register():
    if app_flavor() == "registrar":
        return err("Sub-Registrar accounts are issued by the department.", 403)
    d = request.get_json(silent=True) or {}
    name = " ".join(str(d.get("name", "")).split())
    mobile = svc.normalize_mobile(d.get("mobile"))
    email = str(d.get("email", "")).strip().lower()
    channel = d.get("channel")

    fields = {}
    if not 2 <= len(name) <= 80 or not re.fullmatch(r"[A-Za-z\u0900-\u097F .'\-]+", name):
        fields["name"] = "Enter your full name (letters only)."
    if not mobile:
        fields["mobile"] = "Enter a valid 10-digit Indian mobile number."
    if not svc.valid_email(email):
        fields["email"] = "Enter a valid email address."
    if channel not in svc.CHANNELS:
        fields["channel"] = "Choose where to receive the code."
    if not d.get("consent"):
        fields["consent"] = "Please accept to continue."
    if fields:
        return err("Please fix the highlighted fields.", 400, fields=fields)
    if not svc.channel_status()[channel]["usable"]:
        return err(f"{channel.upper()} codes aren't available yet. Choose another option.", 400,
                   fields={"channel": "This option isn't set up."})

    db = dbm.get_db()
    rows = db.execute("SELECT * FROM users WHERE mobile=? OR email=?", (mobile, email)).fetchall()
    for r in rows:
        if r["status"] == "active":
            which = "mobile" if r["mobile"] == mobile else "email"
            return err("That account already exists. Log in instead.", 409,
                       fields={which: f"This {which} is already registered."})
    # Re-submitting the same details (double click, retry) reuses the pending row so the
    # session and OTP rate limits stay attached to it. Other stale sign-ups are dropped.
    reuse = next((r for r in rows if r["mobile"] == mobile and r["email"] == email), None)
    for r in rows:
        if reuse is None or r["id"] != reuse["id"]:
            db.execute("DELETE FROM users WHERE id=?", (r["id"],))
    if reuse:
        db.execute("UPDATE users SET name=? WHERE id=?", (name, reuse["id"]))
        uid = reuse["id"]
    else:
        uid = db.execute("INSERT INTO users (name, mobile, email, created_at) VALUES (?,?,?,?)",
                         (name, mobile, email, int(time.time()))).lastrowid
    db.commit()
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    res = issue_otp(user, "register", channel, destination_for(user, channel))
    if not res["ok"]:
        return err(res["error"], res.get("status", 400))
    hint = svc.mask_email(email) if channel == "email" else f"+91 {svc.mask_mobile(mobile)}"
    return jsonify(ok=True, sent_to=hint, channel=channel, challenge=make_challenge(user["id"], "register", channel),
                   **({"dev_otp": res["dev_otp"]} if "dev_otp" in res else {}))


# ---------------------------------------------------------------- auth: login
@app.post("/api/login/start")
def api_login_start():
    d = request.get_json(silent=True) or {}
    ident = str(d.get("identifier", "")).strip().lower()
    channel = d.get("channel")
    if channel not in svc.CHANNELS:
        return err("Choose where to receive the code.", 400)
    if not svc.channel_status()[channel]["usable"]:
        return err(f"{channel.upper()} codes aren't available yet. Choose another option.", 400)

    db = dbm.get_db()
    if "@" in ident:
        user = db.execute("SELECT * FROM users WHERE email=?", (ident,)).fetchone()
    else:
        mob = svc.normalize_mobile(ident)
        user = db.execute("SELECT * FROM users WHERE mobile=?", (mob,)).fetchone() if mob else None
    if not ident or ("@" not in ident and not svc.normalize_mobile(ident)):
        return err("Enter your registered email or 10-digit mobile number.", 400)

    payload = {"ok": True, "message": GENERIC_LOGIN_MSG, "channel": channel}
    if not user:  # same response either way, so accounts can't be probed
        payload["challenge"] = make_challenge(0, "login", channel)
        return jsonify(payload)
    if channel in ("sms", "whatsapp") and not user["mobile"]:
        return err("This account has no mobile number. Choose Email.", 400)

    res = issue_otp(user, "login", channel, destination_for(user, channel))
    if not res["ok"]:
        return err(res["error"], res.get("status", 400))
    payload["challenge"] = make_challenge(user["id"], "login", channel)
    if "dev_otp" in res:
        payload["dev_otp"] = res["dev_otp"]
    return jsonify(payload)


@app.post("/api/otp/resend")
def api_otp_resend():
    pend = read_challenge((request.get_json(silent=True) or {}).get("challenge"))
    if not pend:
        return err("Start again to get a new code.", 400, code="no_pending")
    if pend["u"] == 0:                       # unknown account on login: answer like a real one
        return jsonify(ok=True)
    user = dbm.get_db().execute("SELECT * FROM users WHERE id=?", (pend["u"],)).fetchone()
    if not user:
        return err("Start again to get a new code.", 400, code="no_pending")
    res = issue_otp(user, pend["p"], pend["c"], destination_for(user, pend["c"]))
    if not res["ok"]:
        return err(res["error"], res.get("status", 400), retry_after=res.get("retry_after"))
    return jsonify(ok=True, **({"dev_otp": res["dev_otp"]} if "dev_otp" in res else {}))


@app.post("/api/otp/verify")
def api_otp_verify():
    d = request.get_json(silent=True) or {}
    pend = read_challenge(d.get("challenge"))
    if not pend or pend["u"] == 0:
        return err("That code isn't active. Request a new one.", 400, code="no_pending")
    code = str(d.get("otp", "")).strip()
    if not re.fullmatch(r"\d{6}", code):
        return err("Enter the 6-digit code.", 400)
    apply_registrar_bootstrap(pend["u"])
    who = dbm.get_db().execute("SELECT role FROM users WHERE id=?", (pend["u"],)).fetchone()
    wrong_app = flavor_mismatch(who["role"]) if who else None
    if wrong_app:
        return err(wrong_app, 403, code="wrong_app")
    res = check_otp(pend["u"], pend["p"], code)
    if not res["ok"]:
        return err(res["error"], 400)

    db = dbm.get_db()
    col = "email_verified" if res["channel"] == "email" else "mobile_verified"
    db.execute(f"UPDATE users SET {col}=1, status='active', last_login_at=? WHERE id=?",
               (int(time.time()), pend["u"]))
    db.commit()
    apply_registrar_bootstrap(pend["u"])       # a first-time sign-up becomes eligible only now, after the contact is verified
    user = db.execute("SELECT * FROM users WHERE id=?", (pend["u"],)).fetchone()
    return jsonify(ok=True, token=start_session(user["id"]), role=user["role"], user=user_json(user))


@app.post("/api/logout")
def api_logout():
    if g.session_id:
        dbm.get_db().execute("DELETE FROM auth_sessions WHERE id=?", (g.session_id,))
        dbm.get_db().commit()
    return jsonify(ok=True)


# --------------------------------------------------------------- Google sign-in
@app.get("/api/auth/google/nonce")
def api_google_nonce():
    """One-time value the Google token must carry back, so a token stolen elsewhere can't be replayed here."""
    db, now = dbm.get_db(), int(time.time())
    nonce = secrets.token_urlsafe(24)
    db.execute("DELETE FROM google_nonces WHERE expires_at < ?", (now,))
    db.execute("INSERT INTO google_nonces (nonce_hash, expires_at) VALUES (?,?)", (hash_token(nonce), now + NONCE_TTL))
    db.commit()
    return jsonify(ok=True, nonce=nonce)


@app.post("/api/auth/google")
def api_auth_google():
    if app_flavor() == "registrar":
        return err("Google sign-in isn't available in this app. Use your one-time code.", 403)
    client_id = svc.google_client_id()
    if not client_id:
        return err("Google sign-in isn't set up on this server.", 503)
    d = request.get_json(silent=True) or {}
    token = str(d.get("credential", ""))
    if not token or len(token) > 4096:
        return err("Google sign-in failed. Please try again.", 400)
    try:
        claims = svc.verify_google_id_token(token, client_id)
    except ValueError:
        return err("Google sign-in failed. Please try again.", 401)
    except ImportError:
        return err("Google sign-in needs the 'google-auth' package installed on the server.", 503)
    except Exception:
        log.exception("Google token check failed")
        return err("Couldn't reach Google to verify your sign-in. Try again.", 502)

    nonce = str(claims.get("nonce", ""))
    db0, now0 = dbm.get_db(), int(time.time())
    used = db0.execute("DELETE FROM google_nonces WHERE nonce_hash=? AND expires_at>=?",
                       (hash_token(nonce), now0)).rowcount if nonce else 0
    db0.commit()
    if used != 1:
        return err("That sign-in expired. Refresh the page and try again.", 400)

    sub, email = str(claims.get("sub", "")), str(claims.get("email", "")).strip().lower()
    if not sub or not svc.valid_email(email) or claims.get("email_verified") not in (True, "true"):
        return err("Your Google account's email address isn't verified.", 400)

    db, now = dbm.get_db(), int(time.time())
    created = False
    user = db.execute("SELECT * FROM users WHERE google_sub=?", (sub,)).fetchone()
    if user is None:
        same_email = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if same_email is not None and same_email["status"] == "pending":
            # sign-up that nobody ever proved ownership of: safe to discard
            db.execute("DELETE FROM users WHERE id=?", (same_email["id"],))
            same_email = None
        if same_email is not None:
            if same_email["role"] != "citizen":
                return err("Official accounts sign in with a one-time code. Use the Log in page.", 403)
            if not same_email["email_verified"]:
                # Someone may have typed this address without owning it. Don't hand them over.
                return err("An account with this email exists, but the email was never verified. "
                           "Log in with your mobile OTP and verify the email first.", 409)
            if same_email["google_sub"] and same_email["google_sub"] != sub:
                return err("This email is already linked to a different Google account.", 409)
            db.execute("UPDATE users SET google_sub=? WHERE id=?", (sub, same_email["id"]))   # safe link
            user = same_email
        else:
            raw = re.sub(r"[\x00-\x1f\x7f]", " ", str(claims.get("name") or ""))
            name = " ".join(raw.split())[:80] or email.split("@")[0][:80]
            try:
                cur = db.execute(
                    """INSERT INTO users (name, mobile, email, mobile_verified, email_verified, status,
                       created_at, google_sub) VALUES (?,?,?,?,?,?,?,?)""",
                    (name, None, email, 0, 1, "active", now, sub))
            except sqlite3.IntegrityError:
                db.rollback()
                return err("Couldn't create the account. Try again.", 409)
            user = db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
            created = True
    elif user["role"] != "citizen":
        return err("Official accounts sign in with a one-time code. Use the Log in page.", 403)

    wrong_app = flavor_mismatch(user["role"])
    if wrong_app:
        db.rollback()
        return err(wrong_app, 403, code="wrong_app")
    db.execute("UPDATE users SET last_login_at=?, status='active' WHERE id=?", (now, user["id"]))
    db.commit()
    return jsonify(ok=True, new_user=created, token=start_session(user["id"]), role=user["role"], user=user_json(user))


# ------------------------------------------------- KYC: user submits, Sub-Registrar decides
KYC_DIR = os.environ.get("KYC_DIR", os.path.join(BASE_DIR, "instance", "kyc"))
KYC_IMG_RE = re.compile(r"^[0-9a-f]{32}\.jpg$")
MAX_KYC_IMAGE = 8 * 1024 * 1024
KYC_SUBMISSIONS_PER_DAY = 5
KYC_REASONS = {
    "unclear": "The image is unclear or cut off",
    "name": "The name doesn't match the card",
    "number": "The Aadhaar number doesn't match the card",
    "dob": "The date of birth doesn't match the card",
    "not_aadhaar": "This isn't an Aadhaar card",
    "other": "Other",
}
NAME_RE = re.compile(r"[A-Za-z\u0900-\u097F .'\-]+")


def latest_kyc(user_id):
    return dbm.get_db().execute(
        "SELECT * FROM kyc_requests WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()


def offices_for_pin(pin):
    """Names of the Sub-Registrar offices whose service area covers a PIN code."""
    rows = dbm.get_db().execute(
        "SELECT office, name, pin_codes FROM users WHERE role='registrar' AND pin_codes IS NOT NULL").fetchall()
    return [r["office"] or r["name"] for r in rows if svc.pin_matches(r["pin_codes"], pin)]


APP_UA_RE = re.compile(r"BhumiSetuApp/\S+ role/(citizen|registrar)")


def app_flavor():
    """'citizen' / 'registrar' when the request comes from the matching Android app, else None.
    The app announces itself in its user agent. This only keeps each app tidy for its audience;
    it is NOT a security boundary (a user agent can be faked), roles are always checked separately."""
    m = APP_UA_RE.search(request.headers.get("User-Agent", ""))
    return m.group(1) if m else None


def flavor_mismatch(role):
    """Message to show when an account is used in the wrong app, else None."""
    f = app_flavor()
    if f == "registrar" and role != "registrar":
        return "This app is for Sub-Registrars. Citizens: please use the BhumiSetu app for citizens."
    if f == "citizen" and role == "registrar":
        return "Sub-Registrar accounts sign in with the BhumiSetu Registrar app."
    return None


def kyc_pending_count():
    if not g.get("user") or g.user["role"] != "registrar":
        return 0
    cond, args = svc.pin_sql(g.user["pin_codes"], "pincode")
    return dbm.get_db().execute(
        f"SELECT COUNT(*) FROM kyc_requests WHERE status='pending' AND {cond}", args).fetchone()[0]


def _delete_kyc_files(*names):
    for name in names:
        if name and KYC_IMG_RE.fullmatch(name):
            try:
                os.remove(os.path.join(KYC_DIR, name))
            except OSError:
                pass


@app.post("/api/kyc/submit")
@login_required
def api_kyc_submit():
    if g.user["role"] == "registrar":
        return err("Sub-Registrar accounts don't need KYC.", 403)
    if g.user["kyc_status"] == "verified":
        return err("Your KYC is already verified.", 400)
    db, now = dbm.get_db(), int(time.time())
    if db.execute("SELECT 1 FROM kyc_requests WHERE user_id=? AND status='pending'", (g.user["id"],)).fetchone():
        return err("Your KYC is already under review.", 409)
    if db.execute("SELECT COUNT(*) FROM kyc_requests WHERE user_id=? AND submitted_at>?",
                  (g.user["id"], now - 86400)).fetchone()[0] >= KYC_SUBMISSIONS_PER_DAY:
        return err("Too many submissions today. Try again tomorrow.", 429)

    f = request.form
    clean = lambda k, n=120: " ".join(str(f.get(k, "")).split())[:n]
    v = {k: clean(k) for k in ("full_name", "gender", "city", "district", "state")}
    v["address"] = clean("address", 200)
    v["pincode"] = re.sub(r"\D", "", f.get("pincode", ""))
    errors = {}
    if not 2 <= len(v["full_name"]) <= 80 or not NAME_RE.fullmatch(v["full_name"]):
        errors["full_name"] = "Enter your name exactly as printed on the Aadhaar card."
    try:
        dob = datetime.strptime(f.get("dob", ""), "%Y-%m-%d").date()
        age = (datetime.now().date() - dob).days / 365.25
        if not 18 <= age <= 120:
            errors["dob"] = "You must be 18 or older."
    except ValueError:
        errors["dob"] = "Enter your date of birth."
    if v["gender"] not in ("Male", "Female", "Other"):
        errors["gender"] = "Choose one."
    aadhaar = svc.normalize_aadhaar(f.get("aadhaar", ""))
    if not aadhaar:
        errors["aadhaar"] = "That isn't a valid Aadhaar number. Check the 12 digits."
    if len(v["address"]) < 5:
        errors["address"] = "Enter the address printed on the card."
    for key, label in (("city", "village or city"), ("district", "district"), ("state", "state")):
        if len(v[key]) < 2:
            errors[key] = f"Enter your {label}."
    if not re.fullmatch(r"[1-9]\d{5}", v["pincode"]):
        errors["pincode"] = "Enter the 6-digit PIN code."
    if f.get("consent") not in ("true", "on", "1"):
        errors["consent"] = "Please give consent to continue."

    images = {}
    for side in ("front", "back"):
        up = request.files.get(side)
        if not up or not up.filename:
            errors[side] = f"Upload the {side} of your Aadhaar card."
            continue
        up.stream.seek(0, 2); size = up.stream.tell(); up.stream.seek(0)
        if size > MAX_KYC_IMAGE:
            errors[side] = "This image is too large (max 8 MB)."
            continue
        try:
            images[side] = svc.sanitize_image(up.stream)
        except ValueError as exc:
            errors[side] = str(exc)
    if errors:
        return err("Please fix the highlighted fields.", 400, fields=errors)

    ref = svc.aadhaar_ref(SECRET, aadhaar)
    if db.execute("SELECT 1 FROM users WHERE aadhaar_ref=? AND id<>? AND kyc_status='verified'",
                  (ref, g.user["id"])).fetchone():
        return err("This Aadhaar is already verified on another account.", 409,
                   fields={"aadhaar": "Already linked to another account."})

    os.makedirs(KYC_DIR, exist_ok=True)
    names = {side: f"{secrets.token_hex(16)}.jpg" for side in images}
    try:
        for side, data in images.items():
            with open(os.path.join(KYC_DIR, names[side]), "wb") as fh:
                fh.write(data)
        old = db.execute("SELECT id, front_image, back_image FROM kyc_requests WHERE user_id=? AND status='rejected'",
                         (g.user["id"],)).fetchall()
        cur = db.execute(
            """INSERT INTO kyc_requests (user_id, full_name, dob, gender, aadhaar_ref, aadhaar_last4, address,
               city, district, state, pincode, front_image, back_image, submitted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (g.user["id"], v["full_name"], dob.isoformat(), v["gender"], ref, aadhaar[-4:], v["address"],
             v["city"], v["district"], v["state"], v["pincode"], names["front"], names["back"], now))
        for o in old:            # a resubmission replaces the scans of earlier rejected attempts
            db.execute("UPDATE kyc_requests SET front_image=NULL, back_image=NULL WHERE id=?", (o["id"],))
        db.execute("UPDATE users SET kyc_status='pending' WHERE id=?", (g.user["id"],))
        db.commit()
    except Exception:
        db.rollback()
        _delete_kyc_files(*names.values())
        log.exception("KYC submit failed")
        return err("We couldn't save your KYC request. Please try again.", 500)
    for o in old:
        _delete_kyc_files(o["front_image"], o["back_image"])
    return jsonify(ok=True, id=cur.lastrowid, offices=offices_for_pin(v["pincode"]))


# ---- Sub-Registrar side. Every query below is limited to the registrar's own PIN-code service area.
def _kyc_in_scope(request_id):
    cond, args = svc.pin_sql(g.user["pin_codes"], "k.pincode")
    return dbm.get_db().execute(
        f"""SELECT k.*, u.name AS account_name, u.email AS account_email, u.mobile AS account_mobile,
                   u.created_at AS account_created,
                   (SELECT COUNT(*) FROM users o WHERE o.aadhaar_ref=k.aadhaar_ref AND o.id<>k.user_id
                    AND o.kyc_status='verified') AS duplicate_of_verified,
                   rv.name AS reviewer_name
            FROM kyc_requests k JOIN users u ON u.id=k.user_id LEFT JOIN users rv ON rv.id=k.reviewed_by
            WHERE k.id=? AND {cond}""", [request_id, *args]).fetchone()


@app.get("/api/registrar/kyc")
@login_required
@registrar_required
def api_kyc_list():
    status = request.args.get("status", "pending")
    pin = request.args.get("pin", "").strip()
    name = request.args.get("q", "").strip()[:60]
    if status not in ("pending", "approved", "rejected", "all"):
        return err("Unknown status.", 400)
    if pin and not re.fullmatch(r"\d{1,6}", pin):
        return err("PIN code filter: enter 1 to 6 digits.", 400)
    cond, args = svc.pin_sql(g.user["pin_codes"], "k.pincode")
    db = dbm.get_db()
    counts = {r["status"]: r["n"] for r in db.execute(
        f"SELECT k.status, COUNT(*) AS n FROM kyc_requests k WHERE {cond} GROUP BY k.status", args)}
    where, params = [cond], list(args)
    if status != "all":
        where.append("k.status=?"); params.append(status)
    if pin:
        where.append("k.pincode LIKE ?"); params.append(pin + "%")
    if len(name) >= 2:
        where.append("k.full_name LIKE ? ESCAPE '\\'"); params.append(_like(name))
    rows = db.execute(
        f"""SELECT k.id, k.full_name, k.pincode, k.district, k.state, k.status, k.submitted_at, k.reviewed_at,
                   k.aadhaar_last4, rv.name AS reviewer_name,
                   (SELECT COUNT(*) FROM users o WHERE o.aadhaar_ref=k.aadhaar_ref AND o.id<>k.user_id
                    AND o.kyc_status='verified') AS duplicate_of_verified
            FROM kyc_requests k LEFT JOIN users rv ON rv.id=k.reviewed_by
            WHERE {' AND '.join(where)}
            ORDER BY CASE WHEN k.status='pending' THEN 0 ELSE 1 END,
                     CASE WHEN k.status='pending' THEN k.submitted_at END ASC, k.submitted_at DESC
            LIMIT 100""", params).fetchall()
    return jsonify(ok=True, area=g.user["pin_codes"] or "", counts={s: counts.get(s, 0) for s in ("pending", "approved", "rejected")},
                   results=[dict(r) for r in rows])


@app.get("/api/registrar/kyc/<int:request_id>")
@login_required
@registrar_required
def api_kyc_detail(request_id):
    k = _kyc_in_scope(request_id)
    if not k:
        return err("Request not found in your area.", 404)
    db = dbm.get_db()
    db.execute("INSERT INTO kyc_view_log (registrar_id, request_id, what, created_at) VALUES (?,?,?,?)",
               (g.user["id"], request_id, "details", int(time.time())))
    db.commit()
    return jsonify(ok=True, request={
        "id": k["id"], "status": k["status"], "full_name": k["full_name"], "dob": k["dob"], "gender": k["gender"],
        "aadhaar": f"XXXX XXXX {k['aadhaar_last4']}", "address": k["address"], "city": k["city"],
        "district": k["district"], "state": k["state"], "pincode": k["pincode"], "submitted_at": k["submitted_at"],
        "account": {"name": k["account_name"], "email": k["account_email"], "mobile": k["account_mobile"],
                    "created_at": k["account_created"]},
        "name_matches_account": " ".join(k["full_name"].lower().split()) == " ".join(k["account_name"].lower().split()),
        "duplicate_of_verified": k["duplicate_of_verified"] > 0,
        "has_images": bool(k["front_image"] and k["back_image"]),
        "images": ({"front": file_link("kyc", request_id, "front"), "back": file_link("kyc", request_id, "back")}
                   if k["front_image"] and k["back_image"] else None),
        "reviewed_at": k["reviewed_at"], "reviewer": k["reviewer_name"], "review_note": k["review_note"]})


# ---- Files (Aadhaar photos, sale-deed scans). A browser can't attach an Authorization header to <img src>
# or a link, so the API hands out short-lived signed links instead. A link is re-checked when it is used.
def file_link(kind, rid, side=""):
    token = signer("file-link").dumps({"u": g.user["id"], "k": kind, "i": rid, "s": side})
    return f"/files/{kind}/{rid}" + (f"/{side}" if side else "") + f"?t={token}"


def user_from_file_token(token, kind, rid, side=""):
    try:
        d = signer("file-link").loads(str(token or ""), max_age=FILE_LINK_TTL)
    except BadSignature:
        return None
    if d.get("k") != kind or d.get("i") != rid or d.get("s") != side:
        return None
    u = dbm.get_db().execute("SELECT * FROM users WHERE id=? AND status='active'", (d.get("u"),)).fetchone()
    return u if u and u["role"] == "registrar" else None


def _file_response(path_dir, name, mimetype, download_name=None):
    resp = send_from_directory(path_dir, name, mimetype=mimetype, download_name=download_name)
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.get("/files/kyc/<int:request_id>/<side>")
def kyc_image_file(request_id, side):
    if side not in ("front", "back"):
        abort(404)
    user = user_from_file_token(request.args.get("t"), "kyc", request_id, side)
    if not user:
        abort(403)
    cond, args = svc.pin_sql(user["pin_codes"], "k.pincode")          # still limited to the registrar's area
    k = dbm.get_db().execute(f"SELECT k.front_image, k.back_image FROM kyc_requests k WHERE k.id=? AND {cond}",
                             [request_id, *args]).fetchone()
    name = k[f"{side}_image"] if k else None
    if not name or not KYC_IMG_RE.fullmatch(name):
        abort(404)
    db = dbm.get_db()
    db.execute("INSERT INTO kyc_view_log (registrar_id, request_id, what, created_at) VALUES (?,?,?,?)",
               (user["id"], request_id, side, int(time.time())))
    db.commit()
    return _file_response(KYC_DIR, name, "image/jpeg")


@app.post("/api/registrar/kyc/<int:request_id>/approve")
@login_required
@registrar_required
def api_kyc_approve(request_id):
    if not (request.get_json(silent=True) or {}).get("confirm"):
        return err("Confirm that you checked the details against both images.", 400)
    k = _kyc_in_scope(request_id)
    if not k:
        return err("Request not found in your area.", 404)
    if not (k["front_image"] and k["back_image"]):
        return err("The card images are missing. Reject this request so the user can resubmit.", 409)
    db, now = dbm.get_db(), int(time.time())
    try:
        done = db.execute("UPDATE kyc_requests SET status='approved', reviewed_by=?, reviewed_at=? "
                          "WHERE id=? AND status='pending'", (g.user["id"], now, request_id)).rowcount
        if done != 1:
            db.rollback()
            return err("This request was already decided.", 409)
        db.execute("""UPDATE users SET kyc_status='verified', kyc_name=?, aadhaar_last4=?, aadhaar_ref=?,
                      kyc_verified_at=? WHERE id=?""",
                   (k["full_name"], k["aadhaar_last4"], k["aadhaar_ref"], now, k["user_id"]))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return err("Another account is already verified with this Aadhaar. Reject this request.", 409)
    return jsonify(ok=True)


@app.post("/api/registrar/kyc/<int:request_id>/reject")
@login_required
@registrar_required
def api_kyc_reject(request_id):
    d = request.get_json(silent=True) or {}
    reason, note = str(d.get("reason", "")), " ".join(str(d.get("note", "")).split())[:300]
    if reason not in KYC_REASONS:
        return err("Choose a reason for rejecting.", 400)
    if reason == "other" and len(note) < 5:
        return err("Please describe the reason.", 400)
    k = _kyc_in_scope(request_id)
    if not k:
        return err("Request not found in your area.", 404)
    text = KYC_REASONS[reason] + (f". {note}" if note else "")
    db, now = dbm.get_db(), int(time.time())
    done = db.execute("UPDATE kyc_requests SET status='rejected', reviewed_by=?, reviewed_at=?, review_note=? "
                      "WHERE id=? AND status='pending'", (g.user["id"], now, text, request_id)).rowcount
    if done != 1:
        db.rollback()
        return err("This request was already decided.", 409)
    db.execute("UPDATE users SET kyc_status='failed' WHERE id=?", (k["user_id"],))
    db.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------------- search
def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


ID_COLUMNS = {"property_id": "property_id", "electricity": "electricity_bill_no", "tax": "property_tax_no"}
SEARCH_TYPES = {"auto", "general", "aadhaar"} | set(ID_COLUMNS)
SEARCH_LIMIT_PER_10_MIN = 30


def _norm_id(value: str) -> str:
    return re.sub(r"[\s\-]", "", value or "").upper()


def _id_sql(col: str) -> str:
    return f"UPPER(REPLACE(REPLACE({col}, '-', ''), ' ', ''))"


@app.get("/api/search")
@login_required
@kyc_required
def api_search():
    q = request.args.get("q", "").strip()[:80]
    stype = request.args.get("type", "auto")
    village = request.args.get("village", "").strip()[:60]
    district = request.args.get("district", "").strip()[:60]
    if stype not in SEARCH_TYPES:
        return err("Unknown search type.", 400)

    db, now = dbm.get_db(), int(time.time())
    recent = db.execute("SELECT COUNT(*) FROM search_log WHERE user_id=? AND created_at>?",
                        (g.user["id"], now - 600)).fetchone()[0]
    if recent >= SEARCH_LIMIT_PER_10_MIN:   # slows down guessing of ids / Aadhaar numbers
        return err("Too many searches. Please wait a few minutes.", 429)

    norm = _norm_id(q)
    aad = svc.normalize_aadhaar(q) if stype in ("auto", "aadhaar") else None
    clauses, args, label = [], [], q

    if stype == "aadhaar" and not aad:
        return err("Enter a valid 12-digit Aadhaar number.", 400)
    if aad:
        ref = svc.aadhaar_ref(SECRET, aad)
        if AADHAAR_OWN_ONLY and g.user["role"] != "registrar" and ref != g.user["aadhaar_ref"]:
            return err("For privacy, you can search by your own Aadhaar number only.", 403)
        clauses.append("owner_aadhaar_ref = ?"); args.append(ref)
        label = "Aadhaar XXXX XXXX " + aad[-4:]            # never log the full number
    elif stype in ID_COLUMNS:
        if len(norm) < 6:
            return err("Enter the full number (at least 6 characters).", 400)
        clauses.append(f"{_id_sql(ID_COLUMNS[stype])} = ?"); args.append(norm)   # exact match only
        label = f"{stype.replace('_', ' ')} ID ending {norm[-4:]}"
    else:  # "auto" or "general"
        if stype == "auto" and len(norm) >= 6:
            for col in ID_COLUMNS.values():
                clauses.append(f"{_id_sql(col)} = ?"); args.append(norm)
            if re.search(r"\d", norm):
                label = f"ID ending {norm[-4:]}"
        if len(q) >= 2:
            clauses.append("(owner_name LIKE ? ESCAPE '\\' OR survey_no LIKE ? ESCAPE '\\' "
                           "OR village LIKE ? ESCAPE '\\' OR district LIKE ? ESCAPE '\\' "
                           "OR doc_no LIKE ? ESCAPE '\\')")
            args += [_like(q)] * 5

    where = [f"({' OR '.join(clauses)})"] if clauses else []
    if village:
        where.append("village LIKE ? ESCAPE '\\'"); args.append(_like(village))
    if district:
        where.append("district LIKE ? ESCAPE '\\'"); args.append(_like(district))
    if not where:
        return err("Enter a name, survey number, property/bill/tax number, Aadhaar, village or district.", 400)

    rows = db.execute(
        f"""SELECT id, survey_no, owner_name, village, district, area, doc_type, doc_no, doc_date, status,
                   property_id, electricity_bill_no, property_tax_no, owner_aadhaar_last4,
                   doc_file
            FROM land_records WHERE {' AND '.join(where)} ORDER BY village, survey_no LIMIT 50""", args).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        d["electricity_bill_no"] = svc.mask_tail(d["electricity_bill_no"])
        d["property_tax_no"] = svc.mask_tail(d["property_tax_no"])
        d["has_document"] = bool(d.pop("doc_file"))
        last4 = d.pop("owner_aadhaar_last4")
        d["aadhaar"] = f"XXXX XXXX {last4}" if last4 else ""
        results.append(d)

    label = " · ".join(x for x in (label, village, district) if x)
    db.execute("INSERT INTO search_log (user_id, query, results_count, created_at) VALUES (?,?,?,?)",
               (g.user["id"], label, len(results), now))
    db.commit()
    return jsonify(ok=True, count=len(results), results=results)


# ----------------------------------------- Sub-Registrar: upload documents, add records
class SaleDeedData(BaseModel):
    Buyer_name: str
    Seller_name: str
    Property_address: str
    Sale_amount: str
    Date_of_agreement: str
    Survey_number: str
    Village: str
    District: str
    Area: str


DOC_DIR = os.environ.get("DOC_DIR", os.path.join(BASE_DIR, "instance", "documents"))
DOC_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(pdf|png|jpg)$")
DOC_MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg"}


def sniff_ext(head: bytes):
    """Decide the type from the file's own bytes, not from the name the client sent."""
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return None


def cleanup_orphan_docs(max_age=24 * 3600):
    """Uploaded-but-never-saved scans are removed after a day."""
    try:
        used = {r[0] for r in dbm.get_db().execute("SELECT doc_file FROM land_records WHERE doc_file IS NOT NULL")}
        for name in os.listdir(DOC_DIR):
            path = os.path.join(DOC_DIR, name)
            if name not in used and DOC_NAME_RE.match(name) and time.time() - os.path.getmtime(path) > max_age:
                os.remove(path)
    except OSError:
        pass


def extract_with_gemini(path: str) -> dict:
    from google import genai          # imported lazily so the rest of the app runs without it
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    uploaded = None
    try:
        uploaded = client.files.upload(file=path)
        response = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
            contents=[uploaded, "Analyze this sale deed and extract the key details. "
                                "If a field is not present in the document, return 'Not found'."],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=SaleDeedData, temperature=0.1),
        )
        return json.loads(response.text)
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


@app.post("/api/registrar/upload")
@login_required
@registrar_required
def api_registrar_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return err("No file uploaded.", 400)
    f.stream.seek(0, 2); size = f.stream.tell(); f.stream.seek(0)
    if size > 10 * 1024 * 1024:
        return err("File is too large. The limit is 10 MB.", 413)
    ext = sniff_ext(f.stream.read(16))
    f.stream.seek(0)
    if not ext:
        return err("Upload a PDF, PNG or JPG file.", 400)
    os.makedirs(DOC_DIR, exist_ok=True)
    cleanup_orphan_docs()
    token = f"{secrets.token_hex(16)}.{ext}"
    path = os.path.join(DOC_DIR, token)
    f.save(path)

    data, note = None, None
    if not os.environ.get("GEMINI_API_KEY"):
        note = "Automatic reading isn't set up on this server (no GEMINI_API_KEY). Enter the details by hand."
    else:
        try:
            data = extract_with_gemini(path)
        except Exception:
            log.exception("Extraction failed")
            note = "We couldn't read this document automatically. Enter the details by hand."
    name = secure_filename(f.filename) or f"document.{ext}"
    return jsonify(ok=True, doc=token, original_name=name[:120], data=data, note=note)


def _record_fields(d):
    """Validate the registrar's form. Returns (clean_values, field_errors)."""
    def clean(key, n=120):
        return " ".join(str(d.get(key, "")).split())[:n]
    v = {k: clean(k) for k in ("owner", "seller", "survey", "village", "district", "area",
                                "doc_no", "doc_date", "sale_amount", "address")}
    v["property_id"] = clean("property_id", 40).upper()
    v["electricity"] = clean("electricity_bill_no", 30)
    v["tax"] = clean("property_tax_no", 30).upper()
    errors = {}
    for key, label in (("owner", "owner name"), ("survey", "survey number"), ("village", "village"),
                       ("district", "district"), ("area", "area")):
        if len(v[key]) < 1 or v[key].lower() == "not found":
            errors[key] = f"Enter the {label}."
    if v["property_id"] and not re.fullmatch(r"[A-Z0-9][A-Z0-9\-/]{3,39}", v["property_id"]):
        errors["property_id"] = "Use letters, numbers and dashes (4-40 characters)."
    if v["electricity"] and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\- ]{5,29}", v["electricity"]):
        errors["electricity_bill_no"] = "Enter the full bill / consumer number (6+ characters)."
    if v["tax"] and not re.fullmatch(r"[A-Z0-9][A-Z0-9\- /]{5,29}", v["tax"]):
        errors["property_tax_no"] = "Enter the full property tax number (6+ characters)."
    raw_aadhaar = str(d.get("owner_aadhaar", "")).strip()
    v["aadhaar"] = svc.normalize_aadhaar(raw_aadhaar) if raw_aadhaar else None
    if raw_aadhaar and not v["aadhaar"]:
        errors["owner_aadhaar"] = "That isn't a valid Aadhaar number."
    return v, errors


@app.post("/api/registrar/records")
@login_required
@registrar_required
def api_registrar_add_record():
    d = request.get_json(silent=True) or {}
    v, errors = _record_fields(d)

    doc = str(d.get("doc", "")).strip()
    doc_name = None
    if doc:
        if not DOC_NAME_RE.fullmatch(doc) or not os.path.isfile(os.path.join(DOC_DIR, doc)):
            return err("The uploaded document wasn't found. Upload it again.", 400, fields={"doc": "Upload again."})
        doc_name = secure_filename(str(d.get("doc_name", "")))[:120] or None
    if errors:
        return err("Please fix the highlighted fields.", 400, fields=errors)

    db = dbm.get_db()
    if v["property_id"] and db.execute("SELECT 1 FROM land_records WHERE REPLACE(property_id,'-','')=?",
                                       (v["property_id"].replace("-", ""),)).fetchone():
        return err("A record with this property ID already exists.", 409, fields={"property_id": "Already in use."})
    if doc and db.execute("SELECT 1 FROM land_records WHERE doc_file=?", (doc,)).fetchone():
        return err("This document is already attached to another record.", 409)

    try:
        cur = db.execute(
            """INSERT INTO land_records
               (survey_no, owner_name, village, district, area, doc_type, doc_no, doc_date, status, source,
                created_by, created_at, property_id, electricity_bill_no, property_tax_no,
                owner_aadhaar_ref, owner_aadhaar_last4, seller_name, sale_amount, property_address,
                doc_file, doc_name)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (v["survey"], v["owner"], v["village"], v["district"], v["area"], "Sale Deed",
             v["doc_no"] or None, v["doc_date"] or None, "registered", "registrar", g.user["id"], int(time.time()),
             v["property_id"] or None, v["electricity"] or None, v["tax"] or None,
             svc.aadhaar_ref(SECRET, v["aadhaar"]) if v["aadhaar"] else None,
             v["aadhaar"][-4:] if v["aadhaar"] else None,
             v["seller"] or None, v["sale_amount"] or None, v["address"] or None, doc or None, doc_name))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return err("A record with this property ID already exists.", 409, fields={"property_id": "Already in use."})
    return jsonify(ok=True, id=cur.lastrowid)


@app.get("/api/registrar/records")
@login_required
@registrar_required
def api_registrar_list():
    rows = dbm.get_db().execute(
        """SELECT id, survey_no, owner_name, village, district, area, property_id, doc_file, doc_name, created_at
           FROM land_records WHERE created_by=? AND source='registrar' ORDER BY id DESC LIMIT 20""",
        (g.user["id"],)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["has_document"] = bool(d.pop("doc_file"))
        out.append(d)
    return jsonify(ok=True, results=out)


@app.post("/api/registrar/doc/<int:record_id>/link")
@login_required
@registrar_required
def api_registrar_doc_link(record_id):
    row = dbm.get_db().execute("SELECT doc_file FROM land_records WHERE id=?", (record_id,)).fetchone()
    if not row or not row["doc_file"]:
        return err("This record has no document.", 404)
    return jsonify(ok=True, url=file_link("doc", record_id))


@app.get("/files/doc/<int:record_id>")
def doc_file(record_id):
    if not user_from_file_token(request.args.get("t"), "doc", record_id):
        abort(403)
    row = dbm.get_db().execute("SELECT doc_file, doc_name FROM land_records WHERE id=?", (record_id,)).fetchone()
    if not row or not row["doc_file"] or not DOC_NAME_RE.fullmatch(row["doc_file"]):
        abort(404)
    ext = row["doc_file"].rsplit(".", 1)[1]
    return _file_response(DOC_DIR, row["doc_file"], DOC_MIME[ext], row["doc_name"] or f"document-{record_id}.{ext}")


def apply_registrar_bootstrap(only_user_id=None):
    """Lets the operator name Sub-Registrars in the REGISTRAR_BOOTSTRAP setting instead of running manage.py
    (useful on hosts without a shell). Format, entries separated by ';':
        9876543210|Haveli Sub-Registrar Office|411001-411010 ; officer@example.com|Pune SRO|*
    A person is promoted once they have signed up AND proved the very contact that is listed (email or mobile),
    otherwise anybody could sign up with a listed number or address and become a registrar.
    Only people who can edit the server's settings can add to this list. Remove an entry after use if you later
    demote that person with manage.py, or the next login would promote them again."""
    raw = os.environ.get("REGISTRAR_BOOTSTRAP", "").strip()
    if not raw:
        return
    db = dbm.get_db()
    for entry in raw.split(";"):
        parts = [x.strip() for x in entry.split("|")]
        who = parts[0].lower() if parts and parts[0] else ""
        if not who:
            continue
        office = parts[1] if len(parts) > 1 else ""
        try:
            pins = svc.normalize_pin_spec(parts[2]) if len(parts) > 2 and parts[2] else None
        except ValueError as exc:
            log.warning("REGISTRAR_BOOTSTRAP entry '%s' ignored: %s", who, exc)
            continue
        if "@" in who:
            user = db.execute("SELECT * FROM users WHERE email=? AND email_verified=1 AND status='active'", (who,)).fetchone()
        else:
            mobile = svc.normalize_mobile(who)
            user = db.execute("SELECT * FROM users WHERE mobile=? AND mobile_verified=1 AND status='active'", (mobile,)).fetchone() if mobile else None
        if not user or (only_user_id is not None and user["id"] != only_user_id):
            continue
        if user["role"] == "registrar" and (not pins or user["pin_codes"] == pins) and (not office or user["office"] == office):
            continue
        db.execute("UPDATE users SET role='registrar', office=COALESCE(NULLIF(?, ''), office), pin_codes=COALESCE(?, pin_codes) WHERE id=?",
                   (office, pins, user["id"]))
        log.info("REGISTRAR_BOOTSTRAP: %s is now a Sub-Registrar", who)
    db.commit()


def _startup_checks():
    if not svc.dev_mode():
        if not os.environ.get("ALLOWED_ORIGINS"):
            log.warning("ALLOWED_ORIGINS is empty: no website will be allowed to call this API. "
                        "Set it to your site's address, e.g. https://yourname.github.io")
        if not os.environ.get("SECRET_KEY"):
            log.warning("SECRET_KEY is not set: it is read from instance/secret_key, which is lost when the disk is wiped. "
                        "Login links and sign-in challenges would stop working after a redeploy.")
        if not any(os.environ.get(k) for k in ("GMAIL_USER", "TWILIO_ACCOUNT_SID")):
            log.warning("No way to send one-time codes is configured (GMAIL_USER / GMAIL_APP_PASSWORD), so nobody can log in.")


_startup_checks()
with app.app_context():
    apply_registrar_bootstrap()


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")     # use HOST=0.0.0.0 so a phone on your Wi-Fi can connect
    port = int(os.environ.get("PORT", "5000"))
    if svc.dev_mode():
        print("\n*** OTP_DEV_MODE=1: unconfigured OTP channels print codes to this console and "
              "show them in the browser. Set OTP_DEV_MODE=0 for production. ***")
    print(f"\n>>> BhumiSetu is starting on http://{host}:{port}/  (keep this window open)\n", flush=True)
    app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
