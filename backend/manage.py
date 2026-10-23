"""Administrator commands. Sub-Registrar accounts can only be created here, never by sign-up.

    python manage.py make-registrar 9876543210 --office "Haveli Sub-Registrar Office" --pincodes "411001, 4120*, 411040-411050"
    python manage.py make-registrar someone@example.com --pincodes "*"
    python manage.py set-pincodes   9876543210 "411001, 411002"
    python manage.py make-citizen   9876543210       (take the Sub-Registrar role away again)
    python manage.py list-users

--pincodes is the registrar's service area: only KYC requests from users with a PIN code in this area
are shown to them. Use exact codes (411001), prefixes (4110* = every PIN starting 4110), ranges
(411040-411050), or * for all areas. A registrar with no PIN codes sees no KYC requests.

The person must have registered and verified an OTP once. The change applies immediately.
"""
import argparse
import sys

from app import app
import db as dbm
import services as svc


def find_user(db, ident: str):
    ident = ident.strip().lower()
    if "@" in ident:
        return db.execute("SELECT * FROM users WHERE email=?", (ident,)).fetchone()
    mobile = svc.normalize_mobile(ident)
    return db.execute("SELECT * FROM users WHERE mobile=?", (mobile,)).fetchone() if mobile else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("make-registrar"); r.add_argument("who"); r.add_argument("--office", default="")
    r.add_argument("--pincodes", default="")
    sp = sub.add_parser("set-pincodes"); sp.add_argument("who"); sp.add_argument("pincodes")
    c = sub.add_parser("make-citizen"); c.add_argument("who")
    sub.add_parser("list-users")
    args = ap.parse_args()

    with app.app_context():
        db = dbm.get_db()
        if args.cmd == "list-users":
            for u in db.execute("SELECT id, name, mobile, email, role, office, pin_codes, kyc_status, status FROM users ORDER BY id"):
                print(f"#{u['id']:<3} {u['role']:<9} {u['name']:<22} {u['mobile'] or '-':<10}  {u['email']:<28} "
                      f"kyc={u['kyc_status']:<11} {u['status']}  {u['office'] or ''}  area=[{u['pin_codes'] or ''}]")
            return
        user = find_user(db, args.who)
        if not user:
            sys.exit(f"No account found for '{args.who}'. Ask them to register (and verify the OTP) first.")
        if args.cmd == "set-pincodes":
            try:
                pins = svc.normalize_pin_spec(args.pincodes)
            except ValueError as exc:
                sys.exit(str(exc))
            db.execute("UPDATE users SET pin_codes=? WHERE id=?", (pins, user["id"]))
            print(f"{user['name']}: service area is now [{pins}].")
        elif args.cmd == "make-registrar":
            pins = None
            if args.pincodes.strip():
                try:
                    pins = svc.normalize_pin_spec(args.pincodes)
                except ValueError as exc:
                    sys.exit(str(exc))
            db.execute("UPDATE users SET role='registrar', office=?, pin_codes=COALESCE(?, pin_codes) WHERE id=?",
                       (args.office.strip() or None, pins, user["id"]))
            print(f"{user['name']} ({user['mobile'] or user['email']}) is now a Sub-Registrar.")
            if not (pins or user["pin_codes"]):
                print("WARNING: no PIN codes assigned, so this registrar will see NO KYC requests.\n"
                      '         Run: python manage.py set-pincodes <mobile-or-email> "411001, 4120*"   (or "*" for all areas)')
        else:
            db.execute("UPDATE users SET role='citizen', office=NULL, pin_codes=NULL WHERE id=?", (user["id"],))
            print(f"{user['name']} ({user['mobile'] or user['email']}) is now a regular user.")
        db.commit()


if __name__ == "__main__":
    main()
