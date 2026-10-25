# BhumiSetu

Land-record search with Aadhaar KYC checked by a Sub-Registrar.

```
docs/      the website (plain HTML + JS, no build step)   -> GitHub Pages
backend/   the API (Flask + SQLite)                        -> Railway
android/   WebView starter for the Citizen and Registrar apps
```
The website calls the API with `fetch`. Logging in gives the website a random **token** that it sends as
`Authorization: Bearer ...`. There are **no cookies**: a page on `github.io` and an API on `railway.app` are different sites, and
browsers and Android WebView block cookies between different sites, so cookie logins would break. The server stores only a hash of each
token and can revoke it (logout).

## 1. Try it on your computer
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env              # leave OTP_DEV_MODE=1 for local testing
python app.py                     # API on http://127.0.0.1:5000

# second terminal
cd docs
python -m http.server 8000        # website on http://127.0.0.1:8000
```
Open http://127.0.0.1:8000. `docs/js/config.js` already points at `http://127.0.0.1:5000`. In demo mode (`OTP_DEV_MODE=1`) nothing is emailed: each one-time code is printed in the API's terminal as `[BhumiSetu OTP] email -> you@example.com : 123456`. It is never sent to the browser.

## 2. Put the API on Railway
1. Push this project to a GitHub repository.
2. Railway > New Project > Deploy from GitHub repo. In the service **Settings** set **Root Directory** to `/backend`
   (Railway then installs `requirements.txt` and starts the app with the `Procfile`: `gunicorn app:app`).
3. **Variables** tab. Set at least:
   ```
   ALLOWED_ORIGINS=https://YOURNAME.github.io     # your website's address, no path, no trailing slash
   SECRET_KEY=<a long random string>
   OTP_DEV_MODE=0                                 # 1 = demo: codes are not emailed, they appear in Railway's Logs tab (see below)
   GMAIL_USER=...   GMAIL_APP_PASSWORD=...        # without a way to send codes nobody can log in
   DATABASE_PATH=/data/bhumisetu.db
   DOC_DIR=/data/documents
   KYC_DIR=/data/kyc
   ```
   Optional: `GEMINI_API_KEY` (reads sale deeds), `GOOGLE_CLIENT_ID` (Google sign-in), `FAST2SMS_API_KEY` (SMS codes).
4. **Volume**: add a Volume to the service and mount it at `/data`. The database, sale-deed scans and Aadhaar photos live there;
   without a volume they are lost on every deploy.
5. **Networking** > generate a public domain. You get `https://something.up.railway.app`. Open `/healthz`: it should say `ok`.
6. Create your first Sub-Registrar. Easiest, no shell needed: add a Variable
   `REGISTRAR_BOOTSTRAP=officer@example.com|Haveli Sub-Registrar Office|411001-411010`
   (several people: separate them with `;`; PIN codes can also be `4110*` or `*`). That person signs up on the website and verifies a code,
   and is promoted automatically. The listed contact must be the one they verify (so use their email, or a mobile they confirm by SMS code),
   otherwise someone else could sign up with that address and become a registrar. With a shell you can use `python manage.py make-registrar ...` instead.

Demo without Gmail: set `OTP_DEV_MODE=1` and read each code in the service's **Logs** (search for `BhumiSetu OTP`). Only people with access to your Railway project can see them, so keep it for testing and switch to Gmail + `OTP_DEV_MODE=0` before real users arrive.

(Railway's menus change now and then; if a name above differs, look for the same idea in their docs.) The `Procfile` runs one worker with 4 threads, which suits SQLite.

## 3. Put the website on GitHub Pages
1. Edit `docs/js/config.js`:  `window.BHUMISETU_API = "https://something.up.railway.app";`  (no trailing slash). Commit and push.
2. GitHub repo > Settings > Pages > Deploy from a branch > `main` / `/docs`.
3. After a minute the site is at `https://YOURNAME.github.io/YOUR-REPO/`. Put `https://YOURNAME.github.io` in `ALLOWED_ORIGINS` on Railway.

The pages use relative links, so they work from a sub-path like `/YOUR-REPO/`. The pages also carry a strict Content-Security-Policy
(no inline scripts). If you move the API to a different kind of address, check the browser console for CSP messages.

## Roles
| | Citizen | Sub-Registrar |
|---|---|---|
| Created by | Sign-up page | An administrator with `manage.py`, never by sign-up |
| Can do | Submit KYC, then search records once approved | Approve/reject KYC requests from their PIN-code area, upload sale deeds and add them to the database, view stored documents, search without KYC |

```bash
python manage.py make-registrar 9876543210 --office "Haveli SRO" --pincodes "411001, 4120*, 411040-411050"
python manage.py set-pincodes   9876543210 "411001-411010"      # or "*" for every area
python manage.py make-citizen   9876543210                      # take the role away again
python manage.py list-users
```
On Railway you can use the `REGISTRAR_BOOTSTRAP` variable instead of `manage.py` (see above). If you later demote someone with `manage.py`, remove them from that variable, or their next login promotes them again.

## KYC: a Sub-Registrar checks every request
1. A citizen opens **KYC**, fills in the details as printed on the Aadhaar card (including the **PIN code**) and uploads the front and back photos.
2. The request goes to the Sub-Registrars whose **service area** covers that PIN code.
3. A Sub-Registrar compares the details with the photos and clicks **Approve** or **Reject** (a reason is required; the user sees it and can resubmit).
   Only approved users can search land records.

Service areas use exact PIN codes (`411001`), prefixes (`4110*`), ranges (`411040-411050`) or `*`. A registrar with no PIN codes sees **no** requests.
The queue, details, photos and the decision buttons are limited to the registrar's own area **on the server**.

What is stored
- The Aadhaar number the user types is **not** stored: only the last 4 digits and a keyed one-way reference (so one Aadhaar can't be verified on two accounts).
- The photos are re-encoded as clean JPEGs (location and camera data removed). They are shown only to registrars of the matching area through
  **signed links that expire after 5 minutes** and are re-checked each time. Every view is written to `kyc_view_log`.
- Photos of a rejected request are deleted when the user resubmits. Photos of approved requests stay, for audit.

## Search (KYC-verified users and registrars)
By Aadhaar number, property ID, electricity bill number, property tax number, owner, survey number, village or district. ID searches are exact-match only,
results mask Aadhaar/bill/tax numbers, logs never store full numbers, and searches are rate-limited. Demo values: Aadhaar `2222 2222 2227`, property ID `PID-PUN-0003`,
electricity `170010000002`, tax `PMC-PT-10007`.

## Google sign-in (citizens only)
Set `GOOGLE_CLIENT_ID` on Railway (a **Web application** OAuth client; add your GitHub Pages address, `https://YOURNAME.github.io`, under *Authorized JavaScript origins*).
The server checks Google's signed token itself (signature, expiry, issuer, audience) and ties it to a one-time nonce. It never links to an account whose email was typed
but never verified, and it never works for Sub-Registrar accounts. Google's page does not work inside an Android WebView; the app uses the native account picker (see `android/`).

## Security notes
- Tokens: random 256-bit, only the SHA-256 is stored, 7-day sliding expiry (30 days at most), max 10 sessions per user, revoked on logout.
- CORS: only the origins in `ALLOWED_ORIGINS` (plus localhost origins while `OTP_DEV_MODE=1`). No credentials mode.
- One-time codes: 6 digits, stored hashed, 5-minute expiry, 5 attempts, 30 s resend cooldown, 6 per hour per address; unknown accounts get the same answers as real ones.
- All page text is inserted with `textContent`; there are no inline scripts.
- Before real use: decide how long to keep Aadhaar photos, use encrypted storage, add IP-based rate limiting (e.g. Flask-Limiter), use department SSO for Sub-Registrars,
  and talk to a lawyer about collecting Aadhaar images under the Aadhaar Act.

## Android apps
See `android/README-android.md`: one WebView project, built as two APKs (Citizen and Registrar).
