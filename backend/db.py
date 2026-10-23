"""SQLite helpers + schema + sample land records."""
import os
import sqlite3
import time

from flask import g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "instance", "bhumisetu.db"))

USERS_DDL = """
CREATE TABLE IF NOT EXISTS {name} (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    mobile           TEXT UNIQUE,                          -- NULL for accounts created with Google
    email            TEXT NOT NULL UNIQUE,
    mobile_verified  INTEGER NOT NULL DEFAULT 0,
    email_verified   INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'pending',      -- pending | active
    created_at       INTEGER NOT NULL,
    last_login_at    INTEGER,
    kyc_status       TEXT NOT NULL DEFAULT 'not_started',  -- not_started | pending | verified | failed
    kyc_name         TEXT,
    aadhaar_last4    TEXT,
    aadhaar_ref      TEXT,                                 -- keyed hash, never the Aadhaar number
    kyc_verified_at  INTEGER,
    role             TEXT NOT NULL DEFAULT 'citizen',      -- citizen | registrar (set only via manage.py)
    office           TEXT,                                 -- registrar's office
    google_sub       TEXT,                                 -- Google account id (the stable 'sub' claim)
    pin_codes        TEXT                                  -- registrar's service area, e.g. '411001, 4120*, 411040-411050'
);
"""

SCHEMA = USERS_DDL.format(name="users") + """
CREATE TABLE IF NOT EXISTS otps (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    purpose      TEXT NOT NULL,          -- register | login
    channel      TEXT NOT NULL,          -- email | sms | whatsapp
    destination  TEXT NOT NULL,
    code_hash    TEXT NOT NULL,
    expires_at   INTEGER NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    consumed     INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS land_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_no   TEXT NOT NULL,
    owner_name  TEXT NOT NULL,
    village     TEXT NOT NULL,
    district    TEXT NOT NULL,
    area        TEXT NOT NULL,
    doc_type    TEXT NOT NULL DEFAULT 'Sale Deed',
    doc_no      TEXT,
    doc_date    TEXT,
    status      TEXT NOT NULL DEFAULT 'registered',   -- registered | pending_verification
    source      TEXT NOT NULL DEFAULT 'sample',       -- sample | user_upload
    created_by  INTEGER,
    created_at  INTEGER NOT NULL,
    property_id          TEXT,    -- e.g. PID-PUN-0001
    electricity_bill_no  TEXT,    -- consumer / bill number
    property_tax_no      TEXT,
    owner_aadhaar_ref    TEXT,    -- keyed hash of the owner's Aadhaar, never the number
    owner_aadhaar_last4  TEXT,
    seller_name          TEXT,
    sale_amount          TEXT,
    property_address     TEXT,
    doc_file             TEXT,    -- stored scan (random name inside instance/documents)
    doc_name             TEXT     -- original file name, for display only
);

CREATE TABLE IF NOT EXISTS kyc_requests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',     -- pending | approved | rejected
    full_name      TEXT NOT NULL,                       -- as printed on the Aadhaar card
    dob            TEXT NOT NULL,
    gender         TEXT,
    aadhaar_ref    TEXT NOT NULL,                       -- keyed hash, never the number
    aadhaar_last4  TEXT NOT NULL,
    address        TEXT NOT NULL,
    city           TEXT NOT NULL,
    district       TEXT NOT NULL,
    state          TEXT NOT NULL,
    pincode        TEXT NOT NULL,
    front_image    TEXT,                                -- stored scans (random names in instance/kyc)
    back_image     TEXT,
    submitted_at   INTEGER NOT NULL,
    reviewed_by    INTEGER,
    reviewed_at    INTEGER,
    review_note    TEXT
);

CREATE TABLE IF NOT EXISTS kyc_view_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    registrar_id INTEGER NOT NULL,
    request_id   INTEGER NOT NULL,
    what         TEXT NOT NULL,                         -- front | back | details
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    token_hash    TEXT NOT NULL UNIQUE,                  -- sha-256 of the bearer token; the token itself is never stored
    created_at    INTEGER NOT NULL,
    expires_at    INTEGER NOT NULL,
    last_used_at  INTEGER NOT NULL,
    user_agent    TEXT
);

CREATE TABLE IF NOT EXISTS google_nonces (
    nonce_hash  TEXT PRIMARY KEY,
    expires_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS search_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    query          TEXT NOT NULL,
    results_count  INTEGER NOT NULL,
    created_at     INTEGER NOT NULL
);
"""

INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_aadhaar_ref
    ON users(aadhaar_ref) WHERE aadhaar_ref IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sessions_user ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_kyc_user ON kyc_requests(user_id, id);
CREATE INDEX IF NOT EXISTS idx_kyc_scope ON kyc_requests(status, pincode);
CREATE INDEX IF NOT EXISTS idx_otps_lookup ON otps(user_id, purpose, id);
CREATE INDEX IF NOT EXISTS idx_otps_dest ON otps(destination, id);
CREATE INDEX IF NOT EXISTS idx_rec_aadhaar ON land_records(owner_aadhaar_ref);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rec_pid_unique ON land_records(property_id) WHERE property_id IS NOT NULL;
"""

# Fictional demo data. Replace with real records later.
SAMPLE_RECORDS = [
    ("123/4", "Rahul Sharma",      "Rampur",      "Pune", "2.5 Acres",     "PUN-2021-004512", "2021-03-14"),
    ("87/2B", "Sunita Deshmukh",   "Karve Nagar", "Pune", "1.2 Hectares",  "PUN-2020-017730", "2020-11-02"),
    ("45/1",  "Vikram Patil",      "Hinjewadi",   "Pune", "0.8 Acres",     "PUN-2022-002981", "2022-06-21"),
    ("210/7", "Anita Kulkarni",    "Rampur",      "Pune", "3.1 Acres",     "PUN-2019-009144", "2019-09-05"),
    ("66/3A", "Mohammed Shaikh",   "Wagholi",     "Pune", "1.0 Acres",     "PUN-2023-001207", "2023-01-18"),
    ("12/9",  "Priya Joshi",       "Lonavala",    "Pune", "0.5 Hectares",  "PUN-2021-012266", "2021-12-09"),
    ("301/5", "Ganesh Bhosale",    "Saswad",      "Pune", "4.0 Acres",     "PUN-2018-006540", "2018-04-27"),
    ("78/6",  "Meera Nair",        "Karve Nagar", "Pune", "0.3 Acres",     "PUN-2022-015873", "2022-08-30"),
]

# Identifiers for the demo records, keyed by doc_no. All fictional (obviously patterned) values.
# (doc_no, property_id, electricity_bill_no, property_tax_no, owner_aadhaar)
SAMPLE_IDS = [
    ("PUN-2021-004512", "PID-PUN-0001", "170010000001", "PMC-PT-10001", "222222222227"),
    ("PUN-2020-017730", "PID-PUN-0002", "170010000002", "PMC-PT-10002", "333333333333"),
    ("PUN-2022-002981", "PID-PUN-0003", "170010000003", "PMC-PT-10003", "444444444445"),
    ("PUN-2019-009144", "PID-PUN-0004", "170010000004", "PMC-PT-10004", "555555555551"),
    ("PUN-2023-001207", "PID-PUN-0005", "170010000005", "PMC-PT-10005", "666666666666"),
    ("PUN-2021-012266", "PID-PUN-0006", "170010000006", "PMC-PT-10006", "777777777775"),
    ("PUN-2018-006540", "PID-PUN-0007", "170010000007", "PMC-PT-10007", "888888888887"),
    ("PUN-2022-015873", "PID-PUN-0008", "170010000008", "PMC-PT-10008", "999999999999"),
]


def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _migrate(db):
    """CREATE TABLE IF NOT EXISTS never alters an existing table, so a database made by an
    older version of this app would be missing newer columns. Compare against the current
    schema and add whatever is missing."""
    ref = sqlite3.connect(":memory:")
    ref.executescript(SCHEMA)
    for (table,) in ref.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for _cid, name, ctype, notnull, default, pk in ref.execute(f"PRAGMA table_info({table})"):
            if name in have:
                continue
            if notnull and default is None and not pk:
                raise RuntimeError(
                    f"The existing '{table}' table in {DB_PATH} is from an incompatible older version "
                    f"(missing required column '{name}'). It only holds dev data: stop the app, "
                    f"delete that file and start again.")
            clause = f" DEFAULT {default}" if default is not None else ""
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ctype}{' NOT NULL' if notnull else ''}{clause}")
            print(f"[BhumiSetu] migrated: added {table}.{name}")
    ref.close()


def _make_mobile_optional(db):
    """Older databases have `mobile TEXT NOT NULL`. SQLite can't relax a column in place, so
    copy the table into the new definition (all rows and ids are kept)."""
    mobile = next((r for r in db.execute("PRAGMA table_info(users)") if r[1] == "mobile"), None)
    if mobile is None or not mobile[3]:          # not found, or already nullable
        return
    new_cols = [r[1] for r in db.execute("PRAGMA table_info(users)")]
    cols = ", ".join(new_cols)
    db.executescript(
        "BEGIN;\nDROP TABLE IF EXISTS users_new;\n" + USERS_DDL.format(name="users_new") +
        f"INSERT INTO users_new ({cols}) SELECT {cols} FROM users;\n"
        "DROP TABLE users;\nALTER TABLE users_new RENAME TO users;\nCOMMIT;")
    print("[BhumiSetu] migrated: users.mobile is now optional")


def _reconcile_legacy_kyc(db):
    """KYC used to be a simulated OTP check that proved nothing. Results of that check are cleared so
    nobody keeps access to land records without a Sub-Registrar's approval."""
    n_pending = db.execute("""UPDATE users SET kyc_status='not_started' WHERE kyc_status IN ('pending','failed')
        AND NOT EXISTS (SELECT 1 FROM kyc_requests k WHERE k.user_id=users.id AND k.status IN ('pending','rejected'))""").rowcount
    n_verified = db.execute("""UPDATE users SET kyc_status='not_started', kyc_name=NULL, aadhaar_last4=NULL,
        aadhaar_ref=NULL, kyc_verified_at=NULL WHERE kyc_status='verified' AND role='citizen'
        AND NOT EXISTS (SELECT 1 FROM kyc_requests k WHERE k.user_id=users.id AND k.status='approved')""").rowcount
    if n_pending or n_verified:
        print(f"[BhumiSetu] KYC now needs a Sub-Registrar's approval: reset {n_verified} simulated 'verified' "
              f"and {n_pending} unfinished KYC status(es). Those users must submit the new KYC form.")


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    _migrate(db)
    _make_mobile_optional(db)
    db.executescript(INDEXES)
    _reconcile_legacy_kyc(db)
    if db.execute("SELECT COUNT(*) FROM land_records").fetchone()[0] == 0:
        now = int(time.time())
        db.executemany(
            """INSERT INTO land_records
               (survey_no, owner_name, village, district, area, doc_no, doc_date, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            [(*r, now) for r in SAMPLE_RECORDS],
        )
    db.commit()
