"""Authentication & email helpers for the Schade Dashboard.

Exposes:
- load_contact_map()  -> dict[str, dict]
- login_gate()        -> renders login UI and sets session_state on success

Environment variables (from mail.env):
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_SSL
- EMAIL_FROM
- OTP_LENGTH, OTP_TTL_SECONDS, OTP_RESEND_SECONDS
- OTP_SUBJECT, OTP_BODY_TEXT, OTP_BODY_HTML (optional)
- ALLOWED_EMAIL_DOMAINS (comma-separated)
"""

from __future__ import annotations

import os
import re
import time
import secrets
import smtplib
import ssl
import hashlib
from email.message import EmailMessage
from datetime import datetime

import pandas as pd
import streamlit as st

# =========================
# .env / mail.env laden
# =========================

def _load_env(path: str = "mail.env") -> None:
    """Load environment variables from a .env-like file without adding a hard dep.
    Tries python-dotenv; falls back to a tiny parser.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(path)
        return
    except Exception:
        pass
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env("mail.env")

# =========================
# SMTP & OTP instellingen
# =========================
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER or "").strip()

OTP_LENGTH = int(os.getenv("OTP_LENGTH", "6"))
OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "600"))
OTP_RESEND_SECONDS = int(os.getenv("OTP_RESEND_SECONDS", "60"))

OTP_SUBJECT = os.getenv("OTP_SUBJECT", "Je verificatiecode")
OTP_BODY_TEXT = os.getenv(
    "OTP_BODY_TEXT",
    (
        "Beste {name}\n\n"
        "Je verificatiecode om in te loggen in schade is: {code}\n"
        "Je hebt {minutes} min om in te loggen.\n\n"
        "Succes,\n"
        "#OneTeamGent"
    ),
)
OTP_BODY_HTML = os.getenv("OTP_BODY_HTML", "")

# =========================
# Domeinlogica / helpers
# =========================

def _extract_domain(addr: str) -> str:
    try:
        return addr.split("@", 1)[1].lower()
    except Exception:
        return ""


ALLOWED_EMAIL_DOMAINS = [
    d.strip().lower()
    for d in os.getenv("ALLOWED_EMAIL_DOMAINS", "").split(",")
    if d.strip()
]
if not ALLOWED_EMAIL_DOMAINS:
    d = _extract_domain(EMAIL_FROM or SMTP_USER or "")
    ALLOWED_EMAIL_DOMAINS = [d] if d else []


def _is_allowed_email(addr: str) -> bool:
    if not ALLOWED_EMAIL_DOMAINS:
        return True
    a = addr.strip().lower()
    return any(a.endswith("@" + d) for d in ALLOWED_EMAIL_DOMAINS)


def _mask_email(addr: str) -> str:
    try:
        local, dom = addr.split("@", 1)
        if len(local) <= 2:
            masked = local[:1] + "*"
        else:
            masked = local[0] + "*" * (len(local) - 2) + local[-1]
        return f"{masked}@{dom}"
    except Exception:
        return addr


def _gen_otp(n: int | None = None) -> str:
    if n is None:
        n = OTP_LENGTH
    return "".join(secrets.choice("0123456789") for _ in range(n))


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _send_email(to_addr: str, subject: str, body_text: str, html: str | None = None) -> None:
    if not (SMTP_HOST and SMTP_PORT and EMAIL_FROM):
        raise RuntimeError("SMTP-configuratie ontbreekt in mail.env")

    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body_text)
    if html:
        msg.add_alternative(html, subtype="html")

    use_ssl = (
        str(os.getenv("SMTP_SSL", "")).strip().lower() in {"1", "true", "yes"}
        or int(SMTP_PORT) == 465
    )

    ctx = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as server:
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=ctx)
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)


# =========================
# Contact mapping (login)
# =========================

def load_contact_map() -> dict[str, dict]:
    """
    'schade met macro.xlsm' → tab 'contact'
    A = personeelsnr, B = naam, C = e-mail
    Return: { "41092": {"email": "...", "name": "..."} }
    """
    path = "schade met macro.xlsm"
    if not os.path.exists(path):
        raise RuntimeError("Bestand 'schade met macro.xlsm' niet gevonden in de projectmap.")

    xls = pd.ExcelFile(path)
    sheet = next((sh for sh in xls.sheet_names if str(sh).strip().lower() == "contact"), None)
    if sheet is None:
        raise RuntimeError("Tabblad 'contact' niet gevonden in 'schade met macro.xlsm'.")

    df = pd.read_excel(xls, sheet_name=sheet, header=None, usecols="A:C")
    if df.empty or df.shape[1] < 3:
        raise RuntimeError("Tabblad 'contact' bevat geen gegevens in kolommen A:C.")

    mapping: dict[str, dict] = {}
    for _, row in df.iterrows():
        pnr = str(row[0]).strip() if pd.notna(row[0]) else ""
        if not pnr:
            continue
        name = str(row[1]).strip() if pd.notna(row[1]) else ""
        email = str(row[2]).strip() if pd.notna(row[2]) else ""
        if not email or email.lower() in {"nan", "none", ""}:
            continue
        mapping[pnr] = {"email": email, "name": name}

    if not mapping:
        raise RuntimeError("Geen geldige rijen in tabblad 'contact'.")
    return mapping


# =========================
# LOGIN FLOW (Streamlit)
# =========================

def login_gate():
    """Render de login-UI met personeelsnummer + OTP, en zet sessiestatus bij succes.

    Zet o.a. deze keys in st.session_state bij succesvolle login:
      - authenticated: True
      - user_pnr, user_email, user_name
    """
    st.title("🔐 Beveiligde toegang")
    st.caption("Log in met je personeelsnummer. Je ontvangt een verificatiecode per e-mail.")
    try:
        contacts = load_contact_map()
    except Exception as e:
        st.error(str(e))
        st.stop()

    if "otp" not in st.session_state:
        st.session_state.otp = {
            "pnr": None,
            "email": None,
            "hash": None,
            "expires": 0.0,
            "last_sent": 0.0,
            "sent": False,
        }
    otp = st.session_state.otp

    col1, col2 = st.columns([3, 2])
    with col1:
        pnr_input = st.text_input(
            "Personeelsnummer", placeholder="bijv. 41092", value=otp.get("pnr") or ""
        )
    with col2:
        want_code = st.button("📨 Verstuur code")

    pnr_digits = "".join(re.findall(r"\d", pnr_input or ""))

    if want_code:
        if not pnr_digits:
            st.error("Vul een geldig personeelsnummer in.")
        else:
            rec = contacts.get(pnr_digits)
            if not rec:
                st.error("Onbekend personeelsnummer.")
            else:
                email = rec["email"] if isinstance(rec, dict) else str(rec)
                if not _is_allowed_email(email):
                    st.error(f"E-mailadres {email} is niet toegestaan.")
                else:
                    now = time.time()
                    if (
                        now - otp.get("last_sent", 0) < OTP_RESEND_SECONDS
                        and otp.get("pnr") == pnr_digits
                    ):
                        remaining = int(
                            OTP_RESEND_SECONDS - (now - otp.get("last_sent", 0))
                        )
                        st.warning(
                            f"Wacht {remaining}s voordat je opnieuw een code aanvraagt."
                        )
                    else:
                        try:
                            code = _gen_otp()
                            st.session_state.otp.update(
                                {
                                    "pnr": pnr_digits,
                                    "email": email,
                                    "hash": _hash_code(code),
                                    "expires": time.time() + OTP_TTL_SECONDS,
                                    "last_sent": time.time(),
                                    "sent": True,
                                }
                            )
                            minutes = OTP_TTL_SECONDS // 60
                            now_str = datetime.now().strftime("%d-%m-%Y %H:%M")
                            naam = (
                                rec.get("name") if isinstance(rec, dict) else None
                            ) or "collega"
                            subject = OTP_SUBJECT.format(
                                code=code,
                                minutes=minutes,
                                pnr=pnr_digits,
                                date=now_str,
                                name=naam,
                            )
                            body_text = OTP_BODY_TEXT.format(
                                code=code,
                                minutes=minutes,
                                pnr=pnr_digits,
                                date=now_str,
                                name=naam,
                            )
                            body_html_raw = (OTP_BODY_HTML or "").strip()
                            body_html = (
                                body_html_raw.format(
                                    code=code,
                                    minutes=minutes,
                                    pnr=pnr_digits,
                                    date=now_str,
                                    name=naam,
                                )
                                if body_html_raw
                                else None
                            )
                            _send_email(email, subject, body_text, html=body_html)
                            st.success(
                                f"Code verzonden naar {_mask_email(email)}. Vul de code hieronder in."
                            )
                        except Exception as e:
                            st.error(f"Kon geen e-mail verzenden: {e}")

    if otp.get("sent"):
        with st.form("otp_form"):
            code_in = st.text_input("Verificatiecode", max_chars=OTP_LENGTH)
            submit = st.form_submit_button("Inloggen")
        if submit:
            if not code_in or len(code_in.strip()) < 1:
                st.error("Vul de code in.")
            elif time.time() > otp.get("expires", 0):
                st.error("Code is verlopen. Vraag een nieuwe code aan.")
            elif _hash_code(code_in.strip()) != otp.get("hash"):
                st.error("Ongeldige code.")
            else:
                st.session_state.authenticated = True
                st.session_state.user_pnr = otp.get("pnr")
                st.session_state.user_email = otp.get("email")
                # Gebruik reeds geladen contacts
                st.session_state.user_name = (
                    (contacts.get(otp.get("pnr"), {}) or {}).get("name")
                    or otp.get("pnr")
                )
                st.session_state.otp = {
                    "pnr": None,
                    "email": None,
                    "hash": None,
                    "expires": 0.0,
                    "last_sent": 0.0,
                    "sent": False,
                }
                st.rerun()


__all__ = [
    "load_contact_map",
    "login_gate",
]
