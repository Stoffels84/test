# dashboard_schade.py
import os
import re
import time
import secrets
import smtplib
import ssl
import hashlib
from email.message import EmailMessage
from datetime import datetime
import streamlit as st
import pandas as pd

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
1000167035
