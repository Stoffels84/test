import os
import smtplib
import ssl
import time
import secrets
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
import pandas as pd

# --------------------------------------------------
# 1. Config laden uit mail.env
# --------------------------------------------------
load_dotenv("mail.env")

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)

ALLOWED_EMAIL_DOMAINS = [
    d.strip()
    for d in os.getenv("ALLOWED_EMAIL_DOMAINS", "").split(",")
    if d.strip()
]

OTP_LENGTH = int(os.getenv("OTP_LENGTH", "6"))
OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "600"))
OTP_RESEND_SECONDS = int(os.getenv("OTP_RESEND_SECONDS", "60"))

OTP_SUBJECT = os.getenv("OTP_SUBJECT", "Je verificatiecode")
OTP_BODY_TEXT = os.getenv("OTP_BODY_TEXT", "Je code is {code}")
OTP_BODY_HTML = os.getenv("OTP_BODY_HTML", "<p>Je code is <b>{code}</b></p>")

CONTACTS_FILE = os.getenv("CONTACTS_FILE", "schade met macro.xlsm")
CONTACTS_SHEET = os.getenv("CONTACTS_SHEET", "contact")
FALLBACK_EMAIL_DOMAIN = os.getenv("FALLBACK_EMAIL_DOMAIN", "").strip() or None

# --------------------------------------------------
# 2. Contacten inladen (PNR -> {name, email})
# --------------------------------------------------
def load_contacts():
    print(f"Loading contacts from {CONTACTS_FILE} / sheet {CONTACTS_SHEET}")
    df = pd.read_excel(CONTACTS_FILE, sheet_name=CONTACTS_SHEET)

    # Zoek kolomnamen flexibel
    cols = {c.lower().strip(): c for c in df.columns}

    def find_col(possible):
        lower_map = {c.lower().strip(): c for c in df.columns}
        for p in possible:
            if p.lower() in lower_map:
                return lower_map[p.lower()]
        return None

    col_pnr = find_col(["p-nr", "pnr", "personeelsnr", "personeelsnummer", "p nr"])
    col_name = find_col(["naam", "name", "volledige naam", "full name"])
    col_email = find_col(["e-mail", "email", "mail", "mailadres"])

    if col_pnr is None:
        raise RuntimeError("Geen P-nr kolom gevonden in contacts-sheet.")

    contacts = {}

    for _, row in df.iterrows():
        pnr_val = row.get(col_pnr)
        if pd.isna(pnr_val):
            continue
        pnr = str(int(pnr_val)) if isinstance(pnr_val, (int, float)) else str(pnr_val).strip()
        if not pnr:
            continue

        name = str(row.get(col_name)).strip() if col_name else ""
        email = str(row.get(col_email)).strip() if col_email else ""

        # Indien geen e-mail in sheet maar FALLBACK_EMAIL_DOMAIN ingesteld:
        if (not email or email == "nan") and FALLBACK_EMAIL_DOMAIN:
            email = f"{pnr}@{FALLBACK_EMAIL_DOMAIN}"

        if not email or "@" not in email:
            continue

        # Domeinfilter
        domain = email.split("@")[-1].lower()
        if ALLOWED_EMAIL_DOMAINS and domain not in [d.lower() for d in ALLOWED_EMAIL_DOMAINS]:
            continue

        contacts[pnr] = {"name": name or pnr, "email": email}

    print(f"Loaded {len(contacts)} contacts.")
    return contacts


CONTACTS = load_contacts()

# --------------------------------------------------
# 3. OTP opslag (in-memory)
#    otp_store[pnr] = {"code": "...", "expires": ts, "last_sent": ts, "email": "...", "name": "..."}
# --------------------------------------------------
otp_store = {}

def generate_otp(length=OTP_LENGTH):
    return "".join(secrets.choice("0123456789") for _ in range(length))

def send_otp_email(name, email, code, pnr):
    minutes = OTP_TTL_SECONDS // 60
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    text = OTP_BODY_TEXT.format(name=name, code=code, minutes=minutes, date=now_str, pnr=pnr)
    html = OTP_BODY_HTML.format(name=name, code=code, minutes=minutes, date=now_str, pnr=pnr)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = OTP_SUBJECT
    msg["From"] = EMAIL_FROM
    msg["To"] = email

    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    use_ssl = os.getenv("SMTP_SSL", "false").lower() == "true"

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, [email], msg.as_string())
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, [email], msg.as_string())

    print(f"OTP mail sent to {email} for PNR {pnr}")


# --------------------------------------------------
# 4. Flask app
# --------------------------------------------------
app = Flask(__name__, static_folder="static", static_url_path="/")

# ---- API: OTP aanvragen ----
@app.post("/api/request-otp")
def api_request_otp():
    data = request.get_json(force=True, silent=True) or {}
    pnr = str(data.get("pnr", "")).strip()

    if not pnr:
        return jsonify({"ok": False, "error": "PNR ontbreekt."}), 400

    contact = CONTACTS.get(pnr)
    if not contact:
        return jsonify({"ok": False, "error": "Onbekend personeelsnummer."}), 404

    now = time.time()
    current = otp_store.get(pnr)
    if current and now - current["last_sent"] < OTP_RESEND_SECONDS:
        remaining = int(OTP_RESEND_SECONDS - (now - current["last_sent"]))
        return jsonify({
            "ok": False,
            "error": f"Code pas opnieuw aanvragen over {remaining} seconden."
        }), 429

    code = generate_otp()
    expires = now + OTP_TTL_SECONDS

    otp_store[pnr] = {
        "code": code,
        "expires": expires,
        "last_sent": now,
        "email": contact["email"],
        "name": contact["name"],
    }

    try:
        send_otp_email(contact["name"], contact["email"], code, pnr)
    except Exception as e:
        print("Error sending email:", e)
        return jsonify({"ok": False, "error": "Kon e-mail niet verzenden."}), 500

    return jsonify({"ok": True, "message": "Code verzonden."})


# ---- API: OTP verifiëren ----
@app.post("/api/verify-otp")
def api_verify_otp():
    data = request.get_json(force=True, silent=True) or {}
    pnr = str(data.get("pnr", "")).strip()
    code = str(data.get("code", "")).strip()

    if not pnr or not code:
        return jsonify({"ok": False, "error": "PNR of code ontbreekt."}), 400

    entry = otp_store.get(pnr)
    now = time.time()

    if not entry:
        return jsonify({"ok": False, "error": "Geen actieve code gevonden, vraag een nieuwe aan."}), 400

    if now > entry["expires"]:
        otp_store.pop(pnr, None)
        return jsonify({"ok": False, "error": "Code is verlopen, vraag een nieuwe aan."}), 400

    if code != entry["code"]:
        return jsonify({"ok": False, "error": "Code is ongeldig."}), 400

    # Succes: eventueel token genereren, maar voor nu alleen ok teruggeven
    # Je zou hier ook een session / JWT kunnen maken.
    otp_store.pop(pnr, None)

    return jsonify({"ok": True, "message": "Inloggen geslaagd."})


# ---- (optioneel) index.html serveren ----
@app.get("/")
def index():
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    app.run(debug=True)
