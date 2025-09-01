# dashboard_schade.py
import os
import re
import time
import secrets
import smtplib
import ssl
import hashlib
import tempfile
from io import BytesIO
from email.message import EmailMessage
from datetime import datetime

import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

# PDF (ReportLab)
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

# =========================
# .env / mail.env laden
# =========================
def _load_env(path: str = "mail.env") -> None:
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
# SMTP instellingen
# =========================
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER or "").strip()

# =========================
# OTP instellingen
# =========================
OTP_LENGTH = int(os.getenv("OTP_LENGTH", "6"))
OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "600"))   # 10 min
OTP_RESEND_SECONDS = int(os.getenv("OTP_RESEND_SECONDS", "60"))

# ==== OTP mail templates (enkel hier definiëren) ====
OTP_SUBJECT = os.getenv("OTP_SUBJECT", "Je verificatiecode")

OTP_BODY_TEXT = os.getenv(
    "OTP_BODY_TEXT",
    (
        "Beste {name}\n\n"
        "Je verificatiecode om in te loggen in schade is: {code}\n"
        "Je hebt {minutes} min om in te loggen.\n\n"
        "Succes,\n"
        "#OneTeamGent"
    )
)

# Optioneel: HTML-versie. Laat leeg ("") als je enkel tekst wil.
OTP_BODY_HTML = os.getenv("OTP_BODY_HTML", "")

# =========================
# Domeinlogica
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

# =========================
# Helpers
# =========================
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
    if use_ssl:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context()) as server:
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=ssl.create_default_context())
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

# =========================
# Contact mapping
# =========================
def load_contact_map() -> dict[str, dict]:
    """
    Leest contactgegevens uit 'schade met macro.xlsm', tabblad 'contact':
      Kolom A = personeelsnummer
      Kolom B = naam
      Kolom C = mailadres

    Geeft mapping terug:
      { "41092": {"email": "persoon@bedrijf.be", "name": "Voornaam Achternaam"} }
    """
    path = "schade met macro.xlsm"
    if not os.path.exists(path):
        raise RuntimeError("Bestand 'schade met macro.xlsm' niet gevonden in de projectmap.")

    # Zoek het tabblad 'contact' (case-insensitive)
    xls = pd.ExcelFile(path)
    sheet = None
    for sh in xls.sheet_names:
        if str(sh).strip().lower() == "contact":
            sheet = sh
            break
    if sheet is None:
        raise RuntimeError("Tabblad 'contact' niet gevonden in 'schade met macro.xlsm'.")

    # Lees kolommen A, B, C (zonder header)
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
        raise RuntimeError("Geen geldige rijen gevonden in tabblad 'contact'. Controleer of kolom A personeelsnummer bevat en kolom C e-mailadressen.")

    return mapping

def _normalize_name(s: str) -> str:
    # eenvoudige normalisatie voor naamvergelijking
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()

@st.cache_data(show_spinner=False)
def load_contact_name_email_map() -> dict[str, str]:
    """
    Leest 'schade met macro.xlsm' → tabblad 'contact'
    A = personeelsnr, B = naam, C = mailadres
    Returned: { normalized_naam: email }
    """
    path = "schade met macro.xlsm"
    if not os.path.exists(path):
        return {}
    xls = pd.ExcelFile(path)

    # vind exact 'contact' (case-insensitive)
    sheet = None
    for sh in xls.sheet_names:
        if str(sh).strip().lower() == "contact":
            sheet = sh
            break
    if sheet is None:
        return {}

    df = pd.read_excel(xls, sheet_name=sheet, header=None, usecols="A:C")
    # kolommen: 0=PNR, 1=Naam, 2=Email
    mapping: dict[str, str] = {}
    for _, row in df.iterrows():
        naam = str(row[1]).strip() if pd.notna(row[1]) else ""
        email = str(row[2]).strip() if pd.notna(row[2]) else ""
        if naam and email and email.lower() not in {"nan", "none", ""}:
            mapping[_normalize_name(naam)] = email
    return mapping

def get_email_by_name_from_contact(naam: str) -> str | None:
    """
    Haal e-mail op op basis van NAAM uit tabblad 'contact'.
    """
    if not naam:
        return None
    name_map = load_contact_name_email_map()
    return name_map.get(_normalize_name(naam))




# =========================
# Badge helpers / misc
# =========================
def naam_naar_dn(naam: str) -> str | None:
    if pd.isna(naam):
        return None
    s = str(naam).strip()
    m = re.match(r"\s*(\d+)", s)
    return m.group(1) if m else None

def _beoordeling_emoji(rate: str) -> str:
    r = (rate or "").strip().lower()
    if r in {"zeer goed", "goed"}:
        return "🟢 "
    if r in {"voldoende"}:
        return "🟠 "
    if r in {"slecht", "onvoldoende", "zeer slecht"}:
        return "🔴 "
    return ""

def badge_van_chauffeur(naam: str) -> str:
    dn = naam_naar_dn(naam)
    if not dn:
        return ""
    sdn = str(dn).strip()
    info = st.session_state.get("excel_info", {}).get(sdn, {})
    beoordeling = info.get("beoordeling")
    status_excel = info.get("status")
    kleur = _beoordeling_emoji(beoordeling)
    coaching_ids = st.session_state.get("coaching_ids", set())
    lopend = (status_excel == "Coaching") or (sdn in coaching_ids)
    return f"{kleur}{'⚫ ' if lopend else ''}"

# =========================
# Naam resolver voor mail
# =========================
def _resolve_name_for_pnr(pnr: str, contacts: dict) -> str | None:
    v = contacts.get(pnr)
    if isinstance(v, dict):
        nm = str(v.get("name") or "").strip()
        if nm:
            return nm
    return None  # Excel-fallback kun je toevoegen indien gewenst

# =========================
# CSV/PDF helpers
# =========================
@st.cache_data
def df_to_csv_bytes(d: pd.DataFrame) -> bytes:
    return d.to_csv(index=False).encode("utf-8")

def extract_url(x) -> str | None:
    if pd.isna(x):
        return None
    s = str(x).strip()
    if s.startswith(("http://", "https://")):
        return s
    m = re.search(r'HYPERLINK\(\s*"([^"]+)"', s, flags=re.IGNORECASE)
    return m.group(1) if m else None

# ========= Data laden =========
@st.cache_data(show_spinner=False, ttl=3600)
def load_schade_prepared(path="schade met macro.xlsm", sheet="BRON"):
    df_raw = pd.read_excel(path, sheet_name=sheet)
    df_raw.columns = df_raw.columns.str.strip()

    d1 = pd.to_datetime(df_raw["Datum"], errors="coerce", dayfirst=True)
    need_retry = d1.isna()
    if need_retry.any():
        d2 = pd.to_datetime(df_raw.loc[need_retry, "Datum"], errors="coerce", dayfirst=False)
        d1.loc[need_retry] = d2
    df_raw["Datum"] = d1
    df_ok = df_raw[df_raw["Datum"].notna()].copy()

    for col in ("volledige naam", "teamcoach", "Locatie", "Bus/ Tram", "Link"):
        if col in df_ok.columns:
            df_ok[col] = df_ok[col].astype("string").str.strip()

    df_ok["dienstnummer"] = (
        df_ok["volledige naam"].astype(str).str.extract(r"^(\d+)", expand=False).astype("string").str.strip()
    )
    df_ok["KwartaalP"] = df_ok["Datum"].dt.to_period("Q")
    df_ok["Kwartaal"]  = df_ok["KwartaalP"].astype(str)

    def _clean_display_series(s: pd.Series) -> pd.Series:
        s = s.astype("string").str.strip()
        bad = s.isna() | s.eq("") | s.str.lower().isin({"nan", "none", "<na>"})
        return s.mask(bad, "onbekend")

    df_ok["volledige naam_disp"] = _clean_display_series(df_ok["volledige naam"])
    df_ok["teamcoach_disp"]      = _clean_display_series(df_ok["teamcoach"])
    df_ok["Locatie_disp"]        = _clean_display_series(df_ok["Locatie"])
    df_ok["BusTram_disp"]        = _clean_display_series(df_ok["Bus/ Tram"])

    options = {
        "teamcoach": sorted(df_ok["teamcoach_disp"].dropna().unique().tolist()),
        "locatie":   sorted(df_ok["Locatie_disp"].dropna().unique().tolist()),
        "voertuig":  sorted(df_ok["BusTram_disp"].dropna().unique().tolist()),
        "kwartaal":  sorted(df_ok["KwartaalP"].dropna().astype(str).unique().tolist()),
        "min_datum": df_ok["Datum"].min().normalize(),
        "max_datum": df_ok["Datum"].max().normalize(),
    }
    return df_ok, options

# ========= Coachingslijst inlezen =========
@st.cache_data(show_spinner=False)
def lees_coachingslijst(pad="Coachingslijst.xlsx"):
    ids_geel, ids_blauw = set(), set()
    total_geel_rows, total_blauw_rows = 0, 0
    excel_info = {}
    try:
        xls = pd.ExcelFile(pad)
    except Exception as e:
        return ids_geel, ids_blauw, total_geel_rows, total_blauw_rows, excel_info, f"Coachingslijst niet gevonden of onleesbaar: {e}"

    def vind_sheet(xls, naam):
        return next((s for s in xls.sheet_names if s.strip().lower() == naam), None)

    pnr_keys        = ["p-nr", "p_nr", "pnr", "pnummer", "dienstnummer", "p nr"]
    fullname_keys   = ["volledige naam", "chauffeur", "bestuurder", "name"]
    voornaam_keys   = ["voornaam", "firstname", "first name", "given name"]
    achternaam_keys = ["achternaam", "familienaam", "lastname", "last name", "surname", "naam"]
    coach_keys      = ["teamcoach", "coach", "team coach"]
    rating_keys     = ["beoordeling coaching", "beoordeling", "rating", "evaluatie"]

    def lees_sheet(sheetnaam, status_label):
        ids = set()
        total_rows = 0
        try:
            dfc = pd.read_excel(xls, sheet_name=sheetnaam)
        except Exception:
            return ids, total_rows

        dfc.columns = dfc.columns.str.strip().str.lower()
        kol_pnr   = next((k for k in pnr_keys if k in dfc.columns), None)
        kol_full  = next((k for k in fullname_keys if k in dfc.columns), None)
        kol_vn    = next((k for k in voornaam_keys if k in dfc.columns), None)
        kol_an    = next((k for k in achternaam_keys if k in dfc.columns), None)
        kol_coach = next((k for k in coach_keys if k in dfc.columns), None)
        kol_rate  = next((k for k in rating_keys if k in dfc.columns), None)

        if kol_pnr is None:
            return ids, total_rows

        s_pnr = (
            dfc[kol_pnr].astype(str)
            .str.extract(r"(\d+)", expand=False)
            .dropna().str.strip()
        )
        total_rows = int(s_pnr.shape[0])
        ids = set(s_pnr.tolist())

        s_pnr_reset = s_pnr.reset_index(drop=True)
        for i in range(len(s_pnr_reset)):
            pnr = s_pnr_reset.iloc[i]
            if pd.isna(pnr):
                continue
            vn = str(dfc[kol_vn].iloc[i]).strip() if kol_vn else ""
            an = str(dfc[kol_an].iloc[i]).strip() if kol_an else ""
            if not (vn or an):
                full = str(dfc[kol_full].iloc[i]).strip() if kol_full else ""
                naam = full if full.lower() not in {"nan", "none", ""} else None
            else:
                naam = f"{vn} {an}".strip()
                if naam.lower() in {"nan", "none", ""}:
                    naam = None
            tc = str(dfc[kol_coach].iloc[i]).strip() if kol_coach else None
            if tc and tc.lower() in {"nan", "none", ""}:
                tc = None
            info = excel_info.get(pnr, {})
            if naam: info["naam"] = naam
            if tc:   info["teamcoach"] = tc
            info["status"] = status_label
            if kol_rate and status_label == "Voltooid":
                raw_rate = str(dfc[kol_rate].iloc[i]).strip().lower()
                if raw_rate and raw_rate not in {"nan", "none", ""}:
                    mapping = {
                        "zeer goed": "zeer goed",
                        "goed": "goed",
                        "voldoende": "voldoende",
                        "slecht": "slecht",
                        "zeer slecht": "zeer slecht",
                        "zeergoed": "zeer goed",
                        "zeerslecht": "zeer slecht",
                    }
                    info["beoordeling"] = mapping.get(raw_rate, raw_rate)
            excel_info[pnr] = info
        return ids, total_rows

    s_geel  = vind_sheet(xls, "voltooide coachings")
    s_blauw = vind_sheet(xls, "coaching")

    if s_geel:
        ids_geel,  total_geel_rows  = lees_sheet(s_geel,  "Voltooid")
    if s_blauw:
        ids_blauw, total_blauw_rows = lees_sheet(s_blauw, "Coaching")

    return ids_geel, ids_blauw, total_geel_rows, total_blauw_rows, excel_info, None

# =========================
# LOGIN FLOW (enkel deze versie)
# =========================
def login_gate():
    st.title("🔐 Beveiligde toegang")
    st.caption("Log in met je personeelsnummer. Je ontvangt een verificatiecode per e-mail.")

    # Contacten laden (tabblad 'contact' uit 'schade met macro.xlsm')
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
        pnr_input = st.text_input("Personeelsnummer", placeholder="bijv. 41092", value=otp.get("pnr") or "")
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
                    if now - otp.get("last_sent", 0) < OTP_RESEND_SECONDS and otp.get("pnr") == pnr_digits:
                        remaining = int(OTP_RESEND_SECONDS - (now - otp.get("last_sent", 0)))
                        st.warning(f"Wacht {remaining}s voordat je opnieuw een code aanvraagt.")
                    else:
                        try:
                            code = _gen_otp()
                            st.session_state.otp.update({
                                "pnr": pnr_digits,
                                "email": email,
                                "hash": _hash_code(code),
                                "expires": time.time() + OTP_TTL_SECONDS,
                                "last_sent": time.time(),
                                "sent": True,
                            })

                            minutes = OTP_TTL_SECONDS // 60
                            now_str = datetime.now().strftime("%d-%m-%Y %H:%M")
                            naam = (rec.get("name") if isinstance(rec, dict) else None) or "collega"

                            subject = OTP_SUBJECT.format(code=code, minutes=minutes, pnr=pnr_digits, date=now_str, name=naam)
                            body_text = OTP_BODY_TEXT.format(code=code, minutes=minutes, pnr=pnr_digits, date=now_str, name=naam)

                            body_html_raw = (OTP_BODY_HTML or "").strip()
                            body_html = body_html_raw.format(code=code, minutes=minutes, pnr=pnr_digits, date=now_str, name=naam) if body_html_raw else None

                            _send_email(email, subject, body_text, html=body_html)
                            st.success(f"Code verzonden naar {_mask_email(email)}. Vul de code hieronder in.")
                        except Exception as e:
                            st.error(f"Kon geen e-mail verzenden: {e}")

    if otp.get("sent"):
        with st.form("otp_form"):
            code_in = st.text_input("Verificatiecode", max_chars=OTP_LENGTH)
            colf1, colf2, colf3 = st.columns([1,1,2])
            with colf1:
                submit = st.form_submit_button("Inloggen")
            with colf2:
                resend = st.form_submit_button("Opnieuw verzenden")
            with colf3:
                cancel = st.form_submit_button("Annuleren")

        if cancel:
            st.session_state.otp = {
                "pnr": None,
                "email": None,
                "hash": None,
                "expires": 0.0,
                "last_sent": 0.0,
                "sent": False,
            }
            st.rerun()

        if resend:
            st.session_state.otp["sent"] = False
            st.rerun()

        if submit:
            if not code_in or len(code_in.strip()) < 1:
                st.error("Vul de code in.")
            elif time.time() > otp.get("expires", 0):
                st.error("Code is verlopen. Vraag een nieuwe code aan.")
            elif _hash_code(code_in.strip()) != otp.get("hash"):
                st.error("Ongeldige code.")
            else:
                # ======= SUCCES ======= #
                # Haal naam uit contacts (voor we OTP resetten)
                rec = contacts.get(otp.get("pnr"))
                user_name = None
                if isinstance(rec, dict):
                    user_name = (rec.get("name") or "").strip()

                # Sla gegevens op voor de sessie
                st.session_state.authenticated = True
                st.session_state.user_pnr   = otp.get("pnr")
                st.session_state.user_email = otp.get("email")
                st.session_state.user_name  = user_name or otp.get("pnr")  # fallback naar PNR als er geen naam is

                # Wis gevoelige OTP-data
                st.session_state.otp = {
                    "pnr": None,
                    "email": None,
                    "hash": None,
                    "expires": 0.0,
                    "last_sent": 0.0,
                    "sent": False,
                }
                st.rerun()

def _parse_teamcoach_emails_from_env() -> dict[str, str]:
    """
    Lees TEAMCOACH_EMAILS uit .env.
    Formaten die geaccepteerd worden (scheiden met komma of puntkomma):
      - Naam=mail@domein.be
      - "Naam <mail@domein.be>"
      - mail@domein.be  (alleen nuttig als je verder zelf matcht)
    Voorbeeld:
      TEAMCOACH_EMAILS=Bart Van Der Beken=bart.vanderbeken@delijn.be;Ann Peeters <ann.peeters@delijn.be>
    """
    raw = (os.getenv("TEAMCOACH_EMAILS") or "").strip()
    if not raw:
        return {}
    parts = [p.strip() for p in re.split(r"[;,]", raw) if p.strip()]
    out: dict[str, str] = {}
    for p in parts:
        # "Naam <mail>"
        m = re.match(r'^(?P<name>.+?)\s*<(?P<mail>[^>]+)>$', p)
        if m:
            out[m.group("name").strip().lower()] = m.group("mail").strip()
            continue
        # "Naam=mail"
        if "=" in p:
            name, mail = p.split("=", 1)
            out[name.strip().lower()] = mail.strip()
            continue
        # "mail" (zonder naam) -> overslaan (geen mapping mogelijk)
    return out


def _parse_teamcoach_emails_from_excel() -> dict[str, str]:
    """
    Optionele bron: een Excel met teamcoach -> e-mail.
    Ondersteunt:
      - teamcoach_emails.xlsx (kolommen: Teamcoach, Email)
      - in 'schade met macro.xlsm': een tabblad 'teamcoach_emails' of 'coaches'
        met kolommen (Teamcoach/Coach, Email/Mail)
    Niet verplicht; wordt alleen gebruikt als aanwezig.
    """
    out: dict[str, str] = {}

    # 1) Los bestand
    if os.path.exists("teamcoach_emails.xlsx"):
        try:
            dfe = pd.read_excel("teamcoach_emails.xlsx")
            cols = [c.strip().lower() for c in dfe.columns]
            dfe.columns = cols
            col_n = next((c for c in ["teamcoach", "coach", "naam", "name"] if c in cols), None)
            col_e = next((c for c in ["email", "mail", "e-mail", "e-mailadres"] if c in cols), None)
            if col_n and col_e:
                for _, r in dfe.iterrows():
                    nm = str(r[col_n]).strip()
                    em = str(r[col_e]).strip()
                    if nm and em and em.lower() not in {"nan","none",""}:
                        out[nm.lower()] = em
        except Exception:
            pass

    # 2) Tabblad in schadebestand
    if os.path.exists("schade met macro.xlsm"):
        try:
            xls = pd.ExcelFile("schade met macro.xlsm")
            cand = None
            for sh in xls.sheet_names:
                s = str(sh).strip().lower()
                if s in {"teamcoach_emails", "coaches"}:
                    cand = sh; break
            if cand:
                dfe = pd.read_excel(xls, sheet_name=cand)
                cols = [c.strip().lower() for c in dfe.columns]
                dfe.columns = cols
                col_n = next((c for c in ["teamcoach", "coach", "naam", "name"] if c in cols), None)
                col_e = next((c for c in ["email", "mail", "e-mail", "e-mailadres"] if c in cols), None)
                if col_n and col_e:
                    for _, r in dfe.iterrows():
                        nm = str(r[col_n]).strip()
                        em = str(r[col_e]).strip()
                        if nm and em and em.lower() not in {"nan","none",""}:
                            out[nm.lower()] = em
        except Exception:
            pass

    return out


def get_teamcoach_email(teamcoach_name: str) -> str | None:
    """
    Resolve e-mailadres van een teamcoach via:
      1) TEAMCOACH_EMAILS in .env
      2) optionele Excel-bronnen (zie functie hierboven)
    """
    if not teamcoach_name:
        return None
    key = teamcoach_name.strip().lower()
    # .env
    env_map = _parse_teamcoach_emails_from_env()
    if key in env_map:
        return env_map[key]
    # Excel
    xls_map = _parse_teamcoach_emails_from_excel()
    if key in xls_map:
        return xls_map[key]
    return None


def _send_email_with_attachment(
    to_addr: str,
    subject: str,
    body_text: str,
    attachment_bytes: bytes,
    attachment_filename: str,
    html: str | None = None,
) -> None:
    """Stuur e-mail met een PDF-bijlage."""
    if not (SMTP_HOST and SMTP_PORT and EMAIL_FROM):
        raise RuntimeError("SMTP-configuratie ontbreekt in mail.env")

    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body_text)
    if html:
        msg.add_alternative(html, subtype="html")

    msg.add_attachment(
        attachment_bytes,
        maintype="application",
        subtype="pdf",
        filename=attachment_filename,
    )

    use_ssl = (
        str(os.getenv("SMTP_SSL", "")).strip().lower() in {"1", "true", "yes"}
        or int(SMTP_PORT) == 465
    )
    if use_ssl:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context()) as server:
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=ssl.create_default_context())
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)



# ========= Dashboard =========
def run_dashboard():
    # Sidebar header + logout
    with st.sidebar:
        display_name = st.session_state.get("user_name") or st.session_state.get("user_pnr") or "—"
        display_pnr  = st.session_state.get("user_pnr", "—")
        user_email   = st.session_state.get("user_email", "?")

        # Toon enkel naam
        st.success(f"Ingelogd als {display_name}\n{_mask_email(user_email)}")

        # Wil je ook het PNR tonen? Gebruik deze regel i.p.v. hierboven:
        # st.success(f"Ingelogd als {display_name} ({display_pnr})\n{_mask_email(user_email)}")

        if st.button("🚪 Uitloggen"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()



    # Data laden
    df, options = load_schade_prepared()
    gecoachte_ids, coaching_ids, totaal_voltooid_rijen, totaal_lopend_rijen, excel_info, coach_warn = lees_coachingslijst()
    st.session_state["coaching_ids"] = coaching_ids
    st.session_state["excel_info"] = excel_info

    df["gecoacht_geel"]  = df["dienstnummer"].astype(str).isin(gecoachte_ids)
    df["gecoacht_blauw"] = df["dienstnummer"].astype(str).isin(coaching_ids)

    st.title("📊 Schadegevallen Dashboard")
    st.caption("🟢 = goed · 🟠 = voldoende · 🔴 = slecht/zeer slecht · ⚫ = lopende coaching")

    if coach_warn:
        st.sidebar.warning(f"⚠️ {coach_warn}")

    def _clean_list(values, allowed):
        return [v for v in (values or []) if v in allowed]

    teamcoach_options = options["teamcoach"]
    locatie_options   = options["locatie"]
    voertuig_options  = options["voertuig"]
    kwartaal_options  = options["kwartaal"]

    with st.sidebar:
        st.image("logo.png", use_container_width=True)
        st.header("🔍 Filters")

        def multiselect_all(label, options, all_label, key):
            opts_with_all = [all_label] + options
            picked_raw = st.multiselect(label, options=opts_with_all, default=[all_label], key=key)
            picked = options if (all_label in picked_raw or len(picked_raw) == 0) else picked_raw
            return picked

        selected_teamcoaches = multiselect_all("Teamcoach", teamcoach_options, "— Alle teamcoaches —", key="filter_teamcoach")
        selected_locaties = multiselect_all("Locatie", locatie_options, "— Alle locaties —", key="filter_locatie")
        selected_voertuigen = multiselect_all("Voertuigtype", voertuig_options, "— Alle voertuigen —", key="filter_voertuig")
        selected_kwartalen = multiselect_all("Kwartaal", kwartaal_options, "— Alle kwartalen —", key="filter_kwartaal")

        if selected_kwartalen:
            sel_periods_idx = pd.PeriodIndex(selected_kwartalen, freq="Q")
            date_from = sel_periods_idx.start_time.min().normalize()
            date_to   = sel_periods_idx.end_time.max().normalize()
        else:
            date_from = options["min_datum"]
            date_to   = options["max_datum"]

        if st.button("🔄 Reset filters"):
            st.query_params.clear()
            st.rerun()

    apply_quarters = bool(selected_kwartalen)
    sel_periods = pd.PeriodIndex(selected_kwartalen, freq="Q") if apply_quarters else None

    mask = (
        df["teamcoach_disp"].isin(selected_teamcoaches)
        & df["Locatie_disp"].isin(selected_locaties)
        & df["BusTram_disp"].isin(selected_voertuigen)
        & (df["KwartaalP"].isin(sel_periods) if apply_quarters else True)
    )
    df_filtered = df.loc[mask]

    start = pd.to_datetime(date_from)
    end   = pd.to_datetime(date_to) + pd.Timedelta(days=1)
    mask_date = (df_filtered["Datum"] >= start) & (df_filtered["Datum"] < end)
    df_filtered = df_filtered.loc[mask_date]

    if df_filtered.empty:
        st.warning("⚠️ Geen schadegevallen gevonden voor de geselecteerde filters.")
        st.stop()

    st.metric("Totaal aantal schadegevallen", len(df_filtered))
    st.download_button(
        "⬇️ Download gefilterde data (CSV)",
        df_to_csv_bytes(df_filtered),
        file_name=f"schade_filtered_{datetime.today().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        help="Exporteer de huidige selectie inclusief datumfilter."
    )

    chauffeur_tab, voertuig_tab, locatie_tab, opzoeken_tab, coaching_tab = st.tabs(
        ["👤 Chauffeur", "🚌 Voertuig", "📍 Locatie", "🔎 Opzoeken", "🎯 Coaching"]
    )

    # ========= PDF Export (per teamcoach) =========
# ====== PDF export + mail: aparte functie ======
def pdf_export_sidebar(df_filtered: pd.DataFrame, df: pd.DataFrame, teamcoach_col: str = "teamcoach_disp"):
    st.markdown("---")
    st.sidebar.subheader("📄 PDF Export per teamcoach")

    # Kieslijst opbouwen uit gefilterde data
    if teamcoach_col in df_filtered.columns:
        teamcoach_options_local = sorted(df_filtered[teamcoach_col].dropna().unique().tolist())
    else:
        teamcoach_options_local = []

    if not teamcoach_options_local:
        st.sidebar.info("Geen teamcoaches gevonden binnen de huidige selectie.")
        return

    pdf_coach = st.sidebar.selectbox("Kies teamcoach voor export", teamcoach_options_local)

    # E-mailadres automatisch uit tabblad 'contact' (A=PNR, B=Naam, C=Mail)
    auto_mail = get_email_by_name_from_contact(pdf_coach) or ""
    to_email = st.sidebar.text_input("E-mailadres ontvanger", value=auto_mail, placeholder="coach@delijn.be")

    send_and_mail = st.sidebar.button("Genereer en mail")

    if not send_and_mail:
        return

    # ---- dataset voor gekozen coach
    kolommen_pdf = ["Datum", "volledige naam_disp", "Locatie_disp", "BusTram_disp"]
    if "Link" in df.columns:
        kolommen_pdf.append("Link")

    schade_pdf = df_filtered[df_filtered[teamcoach_col] == pdf_coach][kolommen_pdf].copy()
    schade_pdf = schade_pdf.sort_values(by="Datum")

    # ---- PDF bouwen in geheugen
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph(f"Overzicht schadegevallen - Teamcoach: <b>{pdf_coach}</b>", styles["Title"]))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"📅 Rapportdatum: {datetime.today().strftime('%d-%m-%Y')}", styles["Normal"]))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"📌 Totaal aantal schadegevallen: <b>{len(schade_pdf)}</b>", styles["Normal"]))
    elements.append(Spacer(1, 12))

    if not schade_pdf.empty:
        eerste_datum = schade_pdf["Datum"].min().strftime("%d-%m-%Y")
        laatste_datum = schade_pdf["Datum"].max().strftime("%d-%m-%Y")
        elements.append(Paragraph("📊 Samenvatting:", styles["Heading2"]))
        elements.append(Paragraph(f"- Periode: {eerste_datum} t/m {laatste_datum}", styles["Normal"]))
        elements.append(Paragraph(f"- Unieke chauffeurs: {schade_pdf['volledige naam_disp'].nunique()}", styles["Normal"]))
        elements.append(Paragraph(f"- Unieke locaties: {schade_pdf['Locatie_disp'].nunique()}", styles["Normal"]))
        elements.append(Spacer(1, 12))

        # per chauffeur
        aantal_per_chauffeur = schade_pdf["volledige naam_disp"].value_counts()
        elements.append(Paragraph("👤 Aantal schadegevallen per chauffeur:", styles["Heading2"]))
        for nm, count in aantal_per_chauffeur.items():
            elements.append(Paragraph(f"- {nm or 'onbekend'}: {count}", styles["Normal"]))
        elements.append(Spacer(1, 12))

        # per locatie
        aantal_per_locatie = schade_pdf["Locatie_disp"].value_counts()
        elements.append(Paragraph("📍 Aantal schadegevallen per locatie:", styles["Heading2"]))
        for loc, count in aantal_per_locatie.items():
            elements.append(Paragraph(f"- {loc or 'onbekend'}: {count}", styles["Normal"]))
        elements.append(Spacer(1, 12))

    # tabel
    elements.append(Paragraph("📂 Individuele schadegevallen:", styles["Heading2"]))
    elements.append(Spacer(1, 6))
    headers = ["Datum", "Chauffeur", "Voertuig", "Locatie"]
    heeft_link = "Link" in schade_pdf.columns
    if heeft_link:
        headers.append("Link")
    rows = [headers]
    for _, r in schade_pdf.iterrows():
        datum = r["Datum"].strftime("%d-%m-%Y") if pd.notna(r["Datum"]) else "onbekend"
        rij = [datum, r.get("volledige naam_disp","onbekend"), r.get("BusTram_disp","onbekend"), r.get("Locatie_disp","onbekend")]
        if heeft_link:
            rij.append(extract_url(r.get("Link")) or "-")
        rows.append(rij)

    if len(rows) > 1:
        colw = [60, 150, 70, 130] + ([120] if heeft_link else [])
        tbl = Table(rows, repeatRows=1, colWidths=colw)
        tbl.setStyle(TableStyle([
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
            ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
            ("ALIGN", (0,0), (-1,0), "CENTER"),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("FONTSIZE", (0,0), (-1,-1), 8),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
        ]))
        elements.append(tbl)

    doc.build(elements)
    buffer.seek(0)
    pdf_bytes = buffer.read()
    bestandsnaam = f"schade_{pdf_coach.replace(' ', '_')}_{datetime.today().strftime('%Y%m%d')}.pdf"

    # ---- mailen
    to_email_clean = (to_email or "").strip()
    if not to_email_clean:
        st.sidebar.error("Geen e-mailadres ingevuld voor de ontvanger.")
        return
    if not _is_allowed_email(to_email_clean):
        st.sidebar.error(f"E-mailadres niet toegestaan: {to_email_clean}")
        return

    try:
        subject = f"Schadeoverzicht – {pdf_coach}"
        body_text = (
            f"Beste {pdf_coach},\n\n"
            "In de bijlage vind je het PDF-overzicht van de geselecteerde schadegevallen.\n\n"
            "Vriendelijke groet,\nSchade Dashboard"
        )
        _send_email_with_attachment(
            to_addr=to_email_clean,
            subject=subject,
            body_text=body_text,
            html=None,
            attachment_bytes=pdf_bytes,
            attachment_filename=bestandsnaam,
        )
        st.sidebar.success(f"PDF gemaild naar {_mask_email(to_email_clean)}")
    except Exception as e:
        st.sidebar.error(f"Mailen mislukt: {e}")

    # optionele downloadknop
    st.sidebar.download_button(
        "📥 Download PDF",
        data=pdf_bytes,
        file_name=bestandsnaam,
        mime="application/pdf",
        key="dl_pdf_coach_mail_copy",
    )

    # ========= TAB 1: Chauffeur =========
    with chauffeur_tab:
        st.subheader("📂 Schadegevallen per chauffeur")
        grp = (
            df_filtered.groupby("volledige naam").size().sort_values(ascending=False).reset_index(name="aantal").rename(columns={"volledige naam": "chauffeur_raw"})
        )
        if grp.empty:
            st.info("Geen schadegevallen binnen de huidige filters.")
        else:
            totaal_schades = int(grp["aantal"].sum())
            totaal_chauffeurs_auto = int(grp.shape[0])

            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Aantal chauffeurs (met schade)", totaal_chauffeurs_auto)
                man_ch = st.number_input("Handmatig aantal chauffeurs", min_value=1, value=max(1, totaal_chauffeurs_auto), step=1, key="chf_manual_count")
            c2.metric("Gemiddeld aantal schades", round(totaal_schades / man_ch, 2))
            c3.metric("Totaal aantal schades", totaal_schades)

            step = 5
            max_val = int(grp["aantal"].max())
            edges = list(range(0, max_val + step, step))
            if not edges or edges[-1] < max_val:
                edges.append(max_val + step)
            grp["interval"] = pd.cut(grp["aantal"], bins=edges, right=True, include_lowest=True)

            for interval, g in grp.groupby("interval", sort=False):
                if g.empty or pd.isna(interval):
                    continue
                left, right = int(interval.left), int(interval.right)
                low = max(1, left + 1)
                with st.expander(f"{low} t/m {right} schades ({len(g)} chauffeurs)", expanded=False):
                    g = g.sort_values("aantal", ascending=False).reset_index(drop=True)
                    for _, row in g.iterrows():
                        raw = str(row["chauffeur_raw"])
                        disp = df_filtered.loc[df_filtered["volledige naam"] == raw, "volledige naam_disp"].iloc[0]
                        badge = badge_van_chauffeur(raw)
                        st.markdown(f"**{badge}{disp}** — {int(row['aantal'])} schadegevallen")
                        subset_cols = [c for c in ["Datum","BusTram_disp","Locatie_disp","teamcoach_disp","Link"] if c in df_filtered.columns]
                        details = df_filtered.loc[df_filtered["volledige naam"] == raw, subset_cols].sort_values("Datum")
                        for _, r in details.iterrows():
                            datum_str = r["Datum"].strftime("%d-%m-%Y") if pd.notna(r["Datum"]) else "onbekend"
                            voertuig   = r.get("BusTram_disp","onbekend")
                            loc        = r.get("Locatie_disp","onbekend")
                            coach      = r.get("teamcoach_disp","onbekend")
                            link       = extract_url(r.get("Link")) if "Link" in details.columns else None
                            prefix = f"📅 {datum_str} — 🚌 {voertuig} — 📍 {loc} — 🧑‍💼 {coach} — "
                            st.markdown(prefix + (f"[🔗 openen]({link})" if link else "❌ geen link"), unsafe_allow_html=True)

    # ========= TAB 2: Voertuig =========
    with voertuig_tab:
        st.subheader("🚘 Schadegevallen per voertuigtype")
        if "BusTram_disp" not in df_filtered.columns:
            st.info("Kolom voor voertuigtype niet gevonden.")
        else:
            counts = df_filtered["BusTram_disp"].value_counts()
            if counts.empty:
                st.info("Geen schadegevallen binnen de huidige filters.")
            else:
                c1, c2 = st.columns(2)
                c1.metric("Unieke voertuigtypes", int(counts.shape[0]))
                c2.metric("Totaal schadegevallen", int(len(df_filtered)))
                st.markdown("### 📊 Samenvatting per voertuigtype")
                sum_df = counts.rename_axis("Voertuigtype").reset_index(name="Schades")
                st.dataframe(sum_df, use_container_width=True)
                st.markdown("---")
                st.subheader("📂 Details per voertuigtype")
                for voertuig in counts.index.tolist():
                    kol_list = ["Datum", "volledige naam_disp", "Locatie_disp", "teamcoach_disp"]
                    if "Link" in df_filtered.columns:
                        kol_list.append("Link")
                    kol_list = [k for k in kol_list if k in df_filtered.columns]
                    sub = df_filtered.loc[df_filtered["BusTram_disp"] == voertuig, kol_list].sort_values("Datum")
                    with st.expander(f"{voertuig} — {len(sub)} schadegevallen", expanded=False):
                        for _, r in sub.iterrows():
                            datum_str = r["Datum"].strftime("%d-%m-%Y") if pd.notna(r["Datum"]) else "onbekend"
                            chauffeur = r.get("volledige naam_disp","onbekend")
                            coach     = r.get("teamcoach_disp","onbekend")
                            loc       = r.get("Locatie_disp","onbekend")
                            link      = extract_url(r.get("Link")) if "Link" in sub.columns else None
                            prefix = f"📅 {datum_str} — 👤 {chauffeur} — 🧑‍💼 {coach} — 📍 {loc} — "
                            st.markdown(prefix + (f"[🔗 openen]({link})" if link else "❌ geen link"), unsafe_allow_html=True)

    # ========= TAB 3: Locatie =========
    with locatie_tab:
        st.subheader("📍 Schadegevallen per locatie")
        ok = True
        if "Locatie_disp" not in df_filtered.columns:
            st.warning("⚠️ Kolom 'Locatie' niet gevonden in de huidige selectie.")
            ok = False
        if ok:
            loc_options = sorted([x for x in df_filtered["Locatie_disp"].dropna().unique().tolist() if str(x).strip()])
            gekozen_locs = st.multiselect("Zoek locatie(s)", options=loc_options, default=[], placeholder="Type om te zoeken…", key="loc_ms")
            col_top1, col_top2 = st.columns([1, 1])
            with col_top1:
                min_schades = st.number_input("Min. aantal schades", min_value=1, value=1, step=1, key="loc_min")
            with col_top2:
                expand_all = st.checkbox("Alles openklappen", value=False, key="loc_expand_all")
            work = df_filtered.copy()
            work["dienstnummer_s"] = work["dienstnummer"].astype(str)
            if gekozen_locs:
                work = work[work["Locatie_disp"].isin(gekozen_locs)]
            if work.empty:
                st.info("Geen resultaten binnen de huidige filters/keuze.")
                ok = False
        if ok:
            agg = (
                work.groupby("Locatie_disp").agg(Schades=("dienstnummer_s","size"), Unieke_chauffeurs=("dienstnummer_s","nunique")).reset_index().rename(columns={"Locatie_disp":"Locatie"})
            )
            if "BusTram_disp" in work.columns:
                v = work.groupby("Locatie_disp")["BusTram_disp"].nunique().rename("Unieke_voertuigen")
                agg = agg.merge(v, left_on="Locatie", right_index=True, how="left")
            else:
                agg["Unieke_voertuigen"] = 0
            if "teamcoach_disp" in work.columns:
                t = work.groupby("Locatie_disp")["teamcoach_disp"].nunique().rename("Unieke_teamcoaches")
                agg = agg.merge(t, left_on="Locatie", right_index=True, how="left")
            else:
                agg["Unieke_teamcoaches"] = 0
            dmin = work.groupby("Locatie_disp")["Datum"].min().rename("Eerste")
            dmax = work.groupby("Locatie_disp")["Datum"].max().rename("Laatste")
            agg = agg.merge(dmin, left_on="Locatie", right_index=True, how="left")
            agg = agg.merge(dmax, left_on="Locatie", right_index=True, how="left")
            agg = agg[agg["Schades"] >= int(min_schades)]
            if agg.empty:
                st.info("Geen locaties die voldoen aan je filters.")
                ok = False
        if ok:
            c1, c2 = st.columns(2)
            c1.metric("Unieke locaties", int(agg.shape[0]))
            c2.metric("Totaal schadegevallen", int(len(work)))
            st.markdown("---")
            st.subheader("📊 Samenvatting per locatie")
            agg_view = agg.copy()
            agg_view["Periode"] = agg_view.apply(lambda r: f"{r['Eerste']:%d-%m-%Y} – {r['Laatste']:%d-%m-%Y}" if pd.notna(r["Eerste"]) and pd.notna(r["Laatste"]) else "—", axis=1)
            cols_show = ["Locatie","Schades","Unieke_chauffeurs","Unieke_voertuigen","Unieke_teamcoaches","Periode"]
            st.dataframe(agg_view[cols_show].sort_values("Schades", ascending=False).reset_index(drop=True), use_container_width=True)
            st.download_button("⬇️ Download samenvatting (CSV)", agg_view[cols_show].to_csv(index=False).encode("utf-8"), file_name="locaties_samenvatting.csv", mime="text/csv", key="dl_loc_summary")
            st.markdown("---")
            st.subheader("📂 Schadegevallen per locatie")
            for _, r in agg.sort_values("Schades", ascending=False).iterrows():
                locatie = r["Locatie"]
                subset = work.loc[work["Locatie_disp"] == locatie].copy()
                if subset.empty:
                    continue
                kol_list = ["Datum","volledige naam_disp","BusTram_disp"]
                if "Link" in subset.columns:
                    kol_list.append("Link")
                subset = subset[kol_list].sort_values("Datum")
                header = f"{locatie} — {len(subset)} schadegevallen"
                with st.expander(header, expanded=expand_all):
                    for _, row in subset.iterrows():
                        datum_str = row["Datum"].strftime("%d-%m-%Y") if pd.notna(row["Datum"]) else "onbekend"
                        chauffeur = row.get("volledige naam_disp","onbekend")
                        voertuig  = row.get("BusTram_disp","onbekend")
                        link      = extract_url(row.get("Link")) if "Link" in subset.columns else None
                        prefix = f"📅 {datum_str} — 👤 {chauffeur} — 🚌 {voertuig} — "
                        st.markdown(prefix + (f"[🔗 openen]({link})" if link else "❌ geen link"), unsafe_allow_html=True)

    # ========= TAB 4: Opzoeken =========
    with opzoeken_tab:
        st.subheader("🔎 Opzoeken op personeelsnummer")
        zoek = st.text_input("Personeelsnummer (dienstnummer)", placeholder="bv. 41092", key="zoek_pnr_input")
        dn_hits = re.findall(r"\d+", str(zoek).strip())
        pnr = dn_hits[0] if dn_hits else ""
        if not pnr:
            st.info("Geef een personeelsnummer in om resultaten te zien.")
        else:
            res = df_filtered[df_filtered["dienstnummer"].astype(str).str.strip() == pnr].copy()
            res_all = df[df["dienstnummer"].astype(str).str.strip() == pnr].copy()
            if not res.empty:
                naam_disp = res["volledige naam_disp"].iloc[0]
                teamcoach_disp = res["teamcoach_disp"].iloc[0] if "teamcoach_disp" in res.columns else "onbekend"
                naam_raw = res["volledige naam"].iloc[0] if "volledige naam" in res.columns else naam_disp
            elif not res_all.empty:
                naam_disp = res_all["volledige naam_disp"].iloc[0]
                teamcoach_disp = res_all["teamcoach_disp"].iloc[0] if "teamcoach_disp" in res_all.columns else "onbekend"
                naam_raw = res_all["volledige naam"].iloc[0] if "volledige naam" in res_all.columns else naam_disp
            else:
                ex_info = st.session_state.get("excel_info", {})
                naam_disp = (ex_info.get(pnr, {}) or {}).get("naam") or ""
                teamcoach_disp = (ex_info.get(pnr, {}) or {}).get("teamcoach") or "onbekend"
                naam_raw = naam_disp
            try:
                s = str(naam_raw or "").strip()
                naam_clean = re.sub(r"^\s*\d+\s*-\s*", "", s)
            except Exception:
                naam_clean = naam_disp
            chauffeur_label = f"{pnr} {naam_clean}".strip() if naam_clean else str(pnr)
            set_lopend   = set(map(str, st.session_state.get("coaching_ids", set())))
            set_voltooid = set(map(str, st.session_state.get("excel_info", {}).keys()))
            if pnr in set_lopend:
                status_lbl, status_emoji = "Lopend", "⚫"
                status_bron = "bron: Coaching (lopend)"
            elif pnr in set_voltooid:
                beo_raw = (st.session_state.get("excel_info", {}).get(pnr, {}) or {}).get("beoordeling", "")
                b = str(beo_raw or "").strip().lower()
                if b in {"zeer goed", "goed"}:
                    status_lbl, status_emoji = "Goed", "🟢"
                elif b in {"voldoende"}:
                    status_lbl, status_emoji = "Voldoende", "🟠"
                elif b in {"onvoldoende", "slecht", "zeer slecht"}:
                    status_lbl, status_emoji = ("Onvoldoende" if b=="onvoldoende" else "Slecht"), "🔴"
                else:
                    status_lbl, status_emoji = "Voltooid (geen beoordeling)", "🟡"
                status_bron = f"bron: Voltooide coachings (beoordeling: {beo_raw or '—'})"
            else:
                status_lbl, status_emoji = "Niet aangevraagd", "⚪"
                status_bron = "bron: Coachingslijst.xlsx"
            st.markdown(f"**👤 Chauffeur:** {chauffeur_label}")
            st.markdown(f"**🧑‍💼 Teamcoach:** {teamcoach_disp}")
            st.markdown(f"**🎯 Coachingstatus:** {status_emoji} {status_lbl}  \n*{status_bron}*")
            st.markdown("---")
            st.metric("Aantal schadegevallen", len(res))
            if res.empty:
                st.caption("Geen schadegevallen binnen de huidige filters.")
            else:
                res = res.sort_values("Datum", ascending=False).copy()
                heeft_link = "Link" in res.columns
                res["URL"] = res["Link"].apply(extract_url) if heeft_link else None
                kol = ["Datum", "Locatie_disp"] + (["URL"] if heeft_link else [])
                column_config = {
                    "Datum": st.column_config.DateColumn("Datum", format="DD-MM-YYYY"),
                    "Locatie_disp": st.column_config.TextColumn("Locatie")
                }
                if heeft_link:
                    column_config["URL"] = st.column_config.LinkColumn("Link", _text="openen")
                st.dataframe(res[kol], column_config=column_config, use_container_width=True)

    # ========= TAB 5: Coaching =========
    with coaching_tab:
        try:
            st.subheader("🎯 Coaching – vergelijkingen")
            set_lopend_all   = set(map(str, st.session_state.get("coaching_ids", set())))
            set_voltooid_all = set(st.session_state.get("excel_info", {}).keys())
            def _filter_by_tc(pnrs: set[str]) -> set[str]:
                return set(pnrs)
            set_lopend_tc   = _filter_by_tc(set_lopend_all)
            set_voltooid_tc = _filter_by_tc(set_voltooid_all)
            pnrs_schade_sel = set(df_filtered["dienstnummer"].dropna().astype(str))
            c1, c2 = st.columns(2)
            c1.metric("🔵 Lopend (in schadelijst)", len(pnrs_schade_sel & set_lopend_tc))
            c2.metric("🟡 Voltooid (in schadelijst)", len(pnrs_schade_sel & set_voltooid_tc))
            st.markdown("---")
            r1, r2 = st.columns(2)
            r1.metric("🔵 Unieke personen (Coaching, Excel)", len(set_lopend_all))
            r2.metric("🟡 Unieke personen (Voltooid, Excel)", len(set_voltooid_all))
            st.markdown("---")
            st.markdown("## 🔎 Vergelijking schadelijst ↔ Coachingslijst")
            status_keuze = st.radio("Welke status vergelijken?", options=["Lopend","Voltooid","Beide"], index=0, horizontal=True, key="coach_status_select")
            if status_keuze == "Lopend":
                set_coach_sel = set_lopend_tc
            elif status_keuze == "Voltooid":
                set_coach_sel = set_voltooid_tc
            else:
                set_coach_sel = set_lopend_tc | set_voltooid_tc

            coach_niet_in_schade = set_coach_sel - pnrs_schade_sel
            schade_niet_in_coach = pnrs_schade_sel - set_coach_sel

            def _naam(p):
                ex_info = st.session_state.get("excel_info", {})
                nm = (ex_info.get(p, {}) or {}).get("naam")
                if nm and str(nm).strip().lower() not in {"nan","none",""}:
                    return str(nm)
                r = df.loc[df["dienstnummer"].astype(str) == str(p), "volledige naam_disp"]
                return r.iloc[0] if not r.empty else str(p)

            def _status_volledig(p):
                in_l = p in set_lopend_all
                in_v = p in set_voltooid_all
                if in_l and in_v: return "Beide"
                if in_l: return "Lopend"
                if in_v: return "Voltooid"
                return "Niet aangevraagd"

            def _make_table(pnrs_set):
                if not pnrs_set:
                    return pd.DataFrame(columns=["Dienstnr","Naam","Status (coachinglijst)"])
                rows = [{
                    "Dienstnr": p,
                    "Naam": f"{badge_van_chauffeur(f'{p} - {_naam(p)}')}{_naam(p)}",
                    "Status (coachinglijst)": _status_volledig(p)
                } for p in sorted(map(str, pnrs_set))]
                out = pd.DataFrame(rows)
                return out.sort_values(["Naam"]).reset_index(drop=True)

            with st.expander(f"🟦 In Coachinglijst maar niet in schadelijst ({len(coach_niet_in_schade)})", expanded=False):
                df_a = _make_table(coach_niet_in_schade)
                st.dataframe(df_a, use_container_width=True) if not df_a.empty else st.caption("Geen resultaten.")
                if not df_a.empty:
                    st.download_button("⬇️ Download CSV (coaching ∧ ¬schade)", df_a.to_csv(index=False).encode("utf-8"), file_name="coaching_zonder_schade.csv", mime="text/csv", key="dl_coach_not_schade")

            with st.expander(f"🟥 In schadelijst maar niet in Coachinglijst ({len(schade_niet_in_coach)})", expanded=False):
                df_b = _make_table(schade_niet_in_coach)
                st.dataframe(df_b, use_container_width=True) if not df_b.empty else st.caption("Geen resultaten.")
                if not df_b.empty:
                    st.download_button("⬇️ Download CSV (schade ∧ ¬coaching)", df_b.to_csv(index=False).encode("utf-8"), file_name="schade_zonder_coaching.csv", mime="text/csv", key="dl_schade_not_coach")

            st.markdown("---")
            st.markdown("## 🚩 >N schades en niet in *Coaching* of *Voltooid*")
            gebruik_filters_s = st.checkbox("Tel schades binnen huidige filters (uit = volledige dataset)", value=False, key="more_schades_use_filters")
            df_basis_s = df_filtered if gebruik_filters_s else df
            thr = st.number_input("Toon bestuurders met méér dan ... schades", min_value=1, value=2, step=1, key="more_schades_threshold")
            pnr_counts = df_basis_s["dienstnummer"].dropna().astype(str).value_counts()
            pnrs_meer_dan = set(pnr_counts[pnr_counts > thr].index)
            set_coaching_all = set_lopend_all | set_voltooid_all
            result_set = pnrs_meer_dan - set_coaching_all
            rows = []
            for p in sorted(result_set, key=lambda x: (-pnr_counts.get(x,0), x)):
                rows.append({
                    "Dienstnr": p,
                    "Naam": f"{badge_van_chauffeur(f'{p} - {_naam(p)}')}{_naam(p)}",
                    "Schades": int(pnr_counts.get(p,0)),
                    "Status (coachinglijst)": "Niet aangevraagd",
                })
            df_no_coach = pd.DataFrame(rows)
            if not df_no_coach.empty:
                df_no_coach = df_no_coach.sort_values(["Schades","Naam"], ascending=[False,True]).reset_index(drop=True)
            with st.expander(f"🟥 > {thr} schades en niet in coaching/voltooid ({len(result_set)})", expanded=True):
                if df_no_coach.empty:
                    st.caption("Geen resultaten.")
                    st.caption(f"PNR's >{thr} vóór uitsluiting: {len(pnrs_meer_dan)}")
                    st.caption(f"Uitgesloten door coaching/voltooid: {len(pnrs_meer_dan & set_coaching_all)}")
                else:
                    st.dataframe(df_no_coach, use_container_width=True)
                    st.download_button("⬇️ Download CSV", df_no_coach.to_csv(index=False).encode("utf-8"), file_name=f"meerdan_{thr}_schades_niet_in_coaching_voltooid.csv", mime="text/csv", key="dl_more_schades_no_coaching")
        except Exception as e:
            st.error("Er ging iets mis in het Coaching-tab.")
            st.exception(e)

# ========= App entrypoint =========
if not st.session_state.get("authenticated"):
    login_gate()
else:
    run_dashboard()
