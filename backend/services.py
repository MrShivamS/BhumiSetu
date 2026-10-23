"""OTP delivery (email / SMS / WhatsApp), Aadhaar validation and the KYC provider.

Free option : Email OTP through Gmail SMTP (needs an App Password).
Paid/limited: SMS (DLT registration in India) and WhatsApp (Meta charges per OTP).
              Both are wired through Twilio and stay disabled until configured.
Dev mode    : if a channel is not configured and OTP_DEV_MODE=1, the code is
              printed in the server console and returned to the browser so the
              demo works with zero setup. Turn it OFF (OTP_DEV_MODE=0) in production.
"""
import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import smtplib
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

log = logging.getLogger("bhumisetu")

CHANNELS = ("email", "sms", "whatsapp")
OTP_TTL = 300  # seconds


def dev_mode() -> bool:
    return os.environ.get("OTP_DEV_MODE", "1") == "1"


# --------------------------------------------------------------- Google sign-in
def google_client_id():
    """The *Web application* OAuth client ID from Google Cloud Console. Empty = feature off."""
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip() or None


def verify_google_id_token(token: str, client_id: str) -> dict:
    """Checks the signature (against Google's public keys), expiry, issuer and audience of an
    ID token and returns its claims. Raises ValueError if the token is not valid."""
    from google.oauth2 import id_token                      # pip install google-auth requests
    from google.auth import exceptions as g_exc
    from google.auth.transport import requests as g_requests
    try:
        return id_token.verify_oauth2_token(token, g_requests.Request(), client_id, clock_skew_in_seconds=10)
    except g_exc.TransportError:
        raise                                                # Google unreachable: caller reports a temporary error
    except g_exc.GoogleAuthError as exc:                     # e.g. wrong issuer: the token itself is bad
        raise ValueError(str(exc)) from exc


# ----------------------------------------------------------------- validation
def normalize_mobile(raw) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}", value or "")) and len(value) <= 254


def mask_mobile(m: str) -> str:
    return f"{m[:2]}XXXXX{m[-3:]}" if m and len(m) == 10 else ""


def mask_tail(value, keep: int = 4) -> str:
    """'170010000001' -> 'XXXXXXXX0001' (shows only the last digits)."""
    value = str(value or "")
    return "X" * max(len(value) - keep, 0) + value[-keep:] if value else ""


def mask_email(e: str) -> str:
    if not e or "@" not in e:
        return ""
    local, domain = e.split("@", 1)
    return f"{local[:2]}{'*' * max(len(local) - 2, 1)}@{domain}"


# ------------------------------------------------------------------ OTP codes
def generate_otp() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_otp(secret: str, user_id: int, purpose: str, code: str) -> str:
    msg = f"{user_id}:{purpose}:{code}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


# ------------------------------------------------------------- channel status
def _have(*names) -> bool:
    return all(os.environ.get(n) for n in names)


def channel_status() -> dict:
    live = {
        "email": _have("GMAIL_USER", "GMAIL_APP_PASSWORD"),
        "sms": _have("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_SMS_FROM"),
        "whatsapp": _have("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM"),
    }
    return {
        c: {"live": live[c], "usable": live[c] or dev_mode()} for c in CHANNELS
    }


# ----------------------------------------------------------------- delivery
def _message(code: str) -> str:
    return (f"{code} is your BhumiSetu verification code. It expires in "
            f"{OTP_TTL // 60} minutes. Never share it with anyone.")


def _send_email(to: str, code: str):
    msg = EmailMessage()
    msg["Subject"] = "Your BhumiSetu verification code"
    msg["From"] = f"BhumiSetu <{os.environ['GMAIL_USER']}>"
    msg["To"] = to
    msg.set_content(_message(code))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
        smtp.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
        smtp.send_message(msg)


def _twilio(to: str, from_: str, body: str):
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urllib.parse.urlencode({"To": to, "From": from_, "Body": body}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode())
    with urllib.request.urlopen(req, timeout=15) as resp:
        if not 200 <= resp.status < 300:
            raise RuntimeError(f"Twilio returned {resp.status}")


def send_otp(channel: str, destination: str, code: str) -> dict:
    """Returns {ok, dev}. dev=True means nothing was delivered; the code was
    printed to the console instead (demo mode)."""
    status = channel_status().get(channel)
    if not status:
        return {"ok": False, "dev": False}
    if not status["live"]:
        if dev_mode():
            print(f"\n[BhumiSetu DEV] {channel} OTP for {destination}: {code}\n", flush=True)
            return {"ok": True, "dev": True}
        return {"ok": False, "dev": False}
    try:
        if channel == "email":
            _send_email(destination, code)
        elif channel == "sms":
            _twilio(f"+91{destination}", os.environ["TWILIO_SMS_FROM"], _message(code))
        else:
            _twilio(f"whatsapp:+91{destination}", os.environ["TWILIO_WHATSAPP_FROM"], _message(code))
        return {"ok": True, "dev": False}
    except (smtplib.SMTPException, urllib.error.URLError, RuntimeError, OSError) as exc:
        log.error("OTP delivery failed on %s: %s", channel, exc)
        return {"ok": False, "dev": False}


# ----------------------------------------------------- pin-code service areas
def parse_pin_spec(spec: str):
    """'411001, 4120*, 411040-411050, *' -> (all_areas, exact_codes, prefixes, ranges). ValueError if malformed."""
    tokens = [t for t in re.split(r"[,;\s]+", str(spec or "")) if t]
    if not tokens:
        raise ValueError("No PIN codes given.")
    all_, exact, prefixes, ranges = False, [], [], []
    for t in tokens:
        if t == "*":
            all_ = True
        elif re.fullmatch(r"[1-9]\d{5}", t):
            exact.append(t)
        elif re.fullmatch(r"[1-9]\d{0,4}\*", t):
            prefixes.append(t[:-1])
        elif re.fullmatch(r"[1-9]\d{5}-[1-9]\d{5}", t):
            lo, hi = t.split("-")
            if lo > hi:
                raise ValueError(f"Range {t} runs backwards.")
            ranges.append((lo, hi))
        else:
            raise ValueError(f"'{t}' isn't a PIN code (use 411001, 4110*, 411001-411050 or *).")
    return all_, exact, prefixes, ranges


def normalize_pin_spec(spec: str) -> str:
    all_, exact, prefixes, ranges = parse_pin_spec(spec)
    if all_:
        return "*"
    return ", ".join(sorted(set(exact)) + [p + "*" for p in sorted(set(prefixes))] + [f"{a}-{b}" for a, b in sorted(set(ranges))])


def pin_sql(spec, column: str = "pincode"):
    """SQL condition + params for rows whose PIN code is inside a service area. An empty or invalid
    area matches nothing, so a registrar without an area sees no requests."""
    try:
        all_, exact, prefixes, ranges = parse_pin_spec(spec)
    except ValueError:
        return "0", []
    if all_:
        return "1", []
    parts, params = [], []
    if exact:
        parts.append(f"{column} IN ({','.join('?' * len(exact))})"); params += exact
    for pre in prefixes:
        parts.append(f"{column} LIKE ?"); params.append(pre + "%")
    for lo, hi in ranges:
        parts.append(f"({column} BETWEEN ? AND ?)"); params += [lo, hi]
    return "(" + " OR ".join(parts) + ")", params


def pin_matches(spec, pin: str) -> bool:
    try:
        all_, exact, prefixes, ranges = parse_pin_spec(spec)
    except ValueError:
        return False
    return all_ or pin in exact or any(pin.startswith(p) for p in prefixes) or any(lo <= pin <= hi for lo, hi in ranges)


# ------------------------------------------------------------------- images
MAX_IMAGE_PIXELS = 50_000_000


def sanitize_image(stream) -> bytes:
    """Accepts only real JPEG/PNG images and returns a clean re-encoded JPEG (max 2000 px): this drops
    EXIF data such as GPS location and anything hidden inside the file. ValueError if not an image."""
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        probe = Image.open(stream)
        if probe.format not in ("JPEG", "PNG"):
            raise ValueError("Only JPG or PNG images are accepted.")
        if probe.width * probe.height > MAX_IMAGE_PIXELS:       # Pillow only warns until 2x this size
            raise ValueError("That image is too large. Use a normal phone photo.")
        probe.verify()
        stream.seek(0)
        img = ImageOps.exif_transpose(Image.open(stream)).convert("RGB")
        img.thumbnail((2000, 2000))
        import io
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue()
    except ValueError:
        raise
    except Exception as exc:                      # truncated file, decompression bomb, not an image...
        raise ValueError("That file isn't a valid image.") from exc


# ------------------------------------------------------------------- Aadhaar
_D = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],
      [4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],
      [8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]
_P = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],
      [9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def normalize_aadhaar(raw) -> str | None:
    """Format check only (12 digits, first digit 2-9, Verhoeff checksum).
    This proves the number is well-formed, NOT that it belongs to the user."""
    digits = re.sub(r"[\s-]", "", str(raw or ""))
    if re.fullmatch(r"[2-9]\d{11}", digits) and verhoeff_valid(digits):
        return digits
    return None


def aadhaar_ref(secret: str, aadhaar: str) -> str:
    """Keyed one-way reference for an Aadhaar number. Used for 'one Aadhaar per account'
    and for searching land records by owner Aadhaar without storing the number."""
    return hmac.new(secret.encode(), b"aadhaar:" + aadhaar.encode(), hashlib.sha256).hexdigest()
