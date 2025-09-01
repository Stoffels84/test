# dashboard_schade.py
from __future__ import annotations

# ============== Imports ==============
import os
import re
import time
import secrets
import smtplib
import ssl
import hashlib
from email.message import EmailMessage
from datetime import datetime
from pathlib import Path
import streamlit as st
import pandas as pd

# ====================================
# Globale opties / performance hints
# ====================================
try:
    # Maakt veel dataframe-bewerkingen lichter (Pandas 2+)
    pd.options.mode.copy_on_write = True  # type: ignore[attr-defined]
except Exception:
    pass

# ====================================
# .env / mail.env laden (robust fallback)
# ====================================
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

# ====================================
# SMTP instellingen
# ====================================
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER or "").strip()

# ====================================
# OTP instellingen en templates
# ====================================
OTP_LENGTH = int(os.getenv("OTP_LENGTH", "6"))
OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "600"))   # 10 min
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
    )
)
OTP_BODY_HTML = os.getenv("OTP_BODY_HTML", "")  # leeg laten indien enkel tekst

# ====================================
# Regex & helpers
# ====================================
PNR_RE   = re.compile(r"(\d+)")
HYPER_RE = re.compile(r'HYPERLINK\(\s*"([^"]+)"', re.IGNORECASE)

def file_mtime_key(path: str) -> float:
    p = Path(path)
    return p.stat().st_mtime if p.exists() else -1.0

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

def mask_email(addr: str) -> str:
    try:
        local, dom = addr.split("@", 1)
        if len(local) <= 2:
            masked = local[:1] + "*"
        else:
            masked = local[0] + "*" * (len(local) - 2) + local[-1]
        return f"{masked}@{dom}"
    except Exception:
        return addr

def gen_otp(n: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(n))

def hash_code(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def extract_url(x) -> str | None:
    if pd.isna(x):
        return None
    s = str(x).strip()
    if s.startswith(("http://", "https://")):
        return s
    m = HYPER_RE.search(s)
    return m.group(1) if m else None

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

# ====================================
# Excel loading (snel & gecachet)
# ====================================
SCHADENAME = "schade met macro.xlsm"

@st.cache_resource(show_spinner=False)
def _excel_resource(path: str = SCHADENAME):
    # Cache de ExcelFile reader zelf: 1x openen → overal hergebruiken
    return pd.ExcelFile(path)

@st.cache_data(show_spinner=False)
def load_contacts(path: str = SCHADENAME, mkey: float = 0.0) -> pd.DataFrame:
    # mkey bust cache wanneer bestand wijzigt
    xls = _excel_resource(path)
    # zoek 'contact' case-insensitive
    sheet = next((s for s in xls.sheet_names if str(s).strip().lower() == "contact"), None)
    if not sheet:
        raise RuntimeError("Tabblad 'contact' niet gevonden.")
    df = pd.read_excel(xls, sheet_name=sheet, header=None, usecols="A:C")
    df.columns = ["pnr", "naam", "email"]
    df["pnr"]   = df["pnr"].astype(str).str.extract(PNR_RE, expand=False)
    df["naam"]  = df["naam"].astype("string").str.strip()
    df["email"] = df["email"].astype("string").str.strip()
    df = df.dropna(subset=["pnr", "email"])
    return df

def _clean_series_display(s: pd.Series) -> pd.Series:
    s = s.astype("string").str.strip()
    bad = s.isna() | s.eq("") | s.str.lower().isin({"nan","none","<na>"})
    return s.mask(bad, "onbekend")

@st.cache_data(show_spinner=False, ttl=3600)
def load_schade_prepared_fast(path: str = SCHADENAME, mkey: float = 0.0):
    xls = _excel_resource(path)
    df = pd.read_excel(xls, sheet_name="BRON")
    df.columns = df.columns.str.strip()

    # Datum parsing (Belgische notatie)
    d = pd.to_datetime(df["Datum"], errors="coerce", dayfirst=True)
    df = df.loc[d.notna()].copy()
    df["Datum"] = d[d.notna()]

    # Display kolommen
    for col, new in [
        ("volledige naam", "volledige naam_disp"),
        ("teamcoach",      "teamcoach_disp"),
        ("Locatie",        "Locatie_disp"),
        ("Bus/ Tram",      "BusTram_disp"),
    ]:
        if col in df.columns:
            df[new] = _clean_series_display(df[col])
        else:
            df[new] = "onbekend"

    # dienstnummer en kwartalen
    df["dienstnummer"] = df["volledige naam"].astype(str).str.extract(PNR_RE, expand=False).astype("string")
    per = df["Datum"].dt.to_period("Q")
    df["KwartaalP"] = per.astype("category")
    df["Kwartaal"]  = per.astype(str).astype("category")

    # Categorical voor snelle .isin
    for c in ["teamcoach_disp","Locatie_disp","BusTram_disp"]:
        df[c] = df[c].astype("category")

    options = {
        "teamcoach": sorted(pd.Index(df["teamcoach_disp"].dropna().unique(), dtype="object").tolist()),
        "locatie":   sorted(pd.Index(df["Locatie_disp"].dropna().unique(),   dtype="object").tolist()),
        "voertuig":  sorted(pd.Index(df["BusTram_disp"].dropna().unique(),   dtype="object").tolist()),
        "kwartaal":  sorted(pd.Index(df["KwartaalP"].dropna().astype(str).unique(), dtype="object").tolist()),
        "min_datum": df["Datum"].min().normalize(),
        "max_datum": df["Datum"].max().normalize(),
    }
    return df, options

# ====================================
# Contact mapping (vectorized)
# ====================================
def get_contact_map(df_contacts: pd.DataFrame) -> dict[str, dict]:
    return dict(
        zip(
            df_contacts["pnr"],
            [{"email": e, "name": n} for n, e in zip(df_contacts["naam"], df_contacts["email"])]
        )
    )

def _normalize_name(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()

@st.cache_data(show_spinner=False)
def name_to_email_map(df_contacts: pd.DataFrame) -> dict[str, str]:
    s = df_contacts["naam"].astype("string").fillna("").map(_normalize_name)
    return dict(zip(s, df_contacts["email"].astype("string")))

def get_email_by_name_from_contact(df_contacts: pd.DataFrame, naam: str) -> str | None:
    if not naam:
        return None
    return name_to_email_map(df_contacts).get(_normalize_name(naam))

# ====================================
# Coachingslijst (snelle parser)
# ====================================
@st.cache_data(show_spinner=False)
def lees_coachingslijst_fast(pad: str = "Coachingslijst.xlsx", mkey: float = 0.0):
    ids_geel, ids_blauw = set(), set()
    total_geel_rows, total_blauw_rows = 0, 0
    excel_info: dict[str, dict] = {}
    try:
        xls = pd.ExcelFile(pad)
    except Exception as e:
        return ids_geel, ids_blauw, total_geel_rows, total_blauw_rows, excel_info, f"Coachingslijst niet gevonden of onleesbaar: {e}"

    def _first_in(df: pd.DataFrame, cands: list[str]) -> str | None:
        cols = set(df.columns)
        return next((c for c in cands if c in cols), None)

    def parse_sheet(sheetnaam: str, status_label: str):
        nonlocal excel_info
        try:
            dfc = pd.read_excel(xls, sheet_name=sheetnaam)
        except Exception:
            return set(), 0
        dfc.columns = dfc.columns.str.strip().str.lower()
        kol_pnr   = _first_in(dfc, ["p-nr","p_nr","pnr","pnummer","dienstnummer","p nr"])
        if not kol_pnr:
            return set(), 0
        pnr = dfc[kol_pnr].astype(str).str.extract(PNR_RE, expand=False).dropna().str.strip()
        ids = set(pnr.tolist())

        fullname = _first_in(dfc, ["volledige naam","chauffeur","bestuurder","name"])
        vn = _first_in(dfc, ["voornaam","firstname","first name","given name"])
        an = _first_in(dfc, ["achternaam","familienaam","lastname","last name","surname","naam"])
        tc = _first_in(dfc, ["teamcoach","coach","team coach"])
        rate = _first_in(dfc, ["beoordeling coaching","beoordeling","rating","evaluatie"])

        dfc["_pnr"] = dfc[kol_pnr].astype(str).str.extract(PNR_RE, expand=False)
        if vn or an:
            dfc["_naam"] = ((dfc[vn].astype(str) if vn else "") + " " + (dfc[an].astype(str) if an else "")).str.strip()
        elif fullname:
            dfc["_naam"] = dfc[fullname].astype(str).str.strip()
        else:
            dfc["_naam"] = ""

        if tc:
            dfc["_tc"] = dfc[tc].astype(str).str.strip()
        else:
            dfc["_tc"] = ""

        if rate and status_label == "Voltooid":
            dfc["_rate"] = (
                dfc[rate].astype(str).str.strip().str.lower()
                .replace({"zeergoed": "zeer goed", "zeerslecht": "zeer slecht"})
            )
        else:
            dfc["_rate"] = ""

        for _, r in dfc[["_pnr","_naam","_tc","_rate"]].fillna("").iterrows():
            p = str(r["_pnr"]).strip()
            if not p:
                continue
            info = excel_info.get(p, {})
            if r["_naam"]:
                info["naam"] = r["_naam"]
            if r["_tc"]:
                info["teamcoach"] = r["_tc"]
            info["status"] = status_label
            if r["_rate"] and status_label == "Voltooid":
                info["beoordeling"] = r["_rate"]
            excel_info[p] = info

        return ids, int(len(pnr))

    s_geel  = next((s for s in xls.sheet_names if s.strip().lower() == "voltooide coachings"), None)
    s_blauw = next((s for s in xls.sheet_names if s.strip().lower() == "coaching"), None)

    if s_geel:
        ids_geel,  total_geel_rows  = parse_sheet(s_geel,  "Voltooid")
    if s_blauw:
        ids_blauw, total_blauw_rows = parse_sheet(s_blauw, "Coaching")

    return ids_geel, ids_blauw, total_geel_rows, total_blauw_rows, excel_info, None

# ====================================
# Teamcoach e-mails: env + optionele Excelbronnen
# ====================================
@st.cache_data(show_spinner=False)
def get_teamcoach_email_map(schade_path: str = SCHADENAME, mkey: float = 0.0) -> dict[str, str]:
    out: dict[str, str] = {}
    raw = (os.getenv("TEAMCOACH_EMAILS") or "").strip()
    if raw:
        for p in re.split(r"[;,]", raw):
            p = p.strip()
            if not p:
                continue
            m = re.match(r'^(?P<name>.+?)\s*<(?P<mail>[^>]+)>$', p)
            if m:
                out[m.group("name").strip().lower()] = m.group("mail").strip()
            elif "=" in p:
                n, e = p.split("=", 1)
                out[n.strip().lower()] = e.strip()

    def add_from_df(dfe: pd.DataFrame):
        cols = [c.strip().lower() for c in dfe.columns]
        dfe.columns = cols
        col_n = next((c for c in ["teamcoach","coach","naam","name"] if c in cols), None)
        col_e = next((c for c in ["email","mail","e-mail","e-mailadres"] if c in cols), None)
        if col_n and col_e:
            for n, e in zip(dfe[col_n], dfe[col_e]):
                n, e = str(n).strip(), str(e).strip()
                if n and e and e.lower() not in {"nan","none",""}:
                    out[n.lower()] = e

    if Path("teamcoach_emails.xlsx").exists():
        try:
            add_from_df(pd.read_excel("teamcoach_emails.xlsx"))
        except Exception:
            pass

    if Path(schade_path).exists():
        try:
            xls = _excel_resource(schade_path)
            cand = next((s for s in xls.sheet_names if s.strip().lower() in {"teamcoach_emails","coaches"}), None)
            if cand:
                add_from_df(pd.read_excel(xls, sheet_name=cand))
        except Exception:
            pass
    return out

def get_teamcoach_email(teamcoach_name: str) -> str | None:
    if not teamcoach_name:
        return None
    return get_teamcoach_email_map().get(teamcoach_name.strip().lower())

# ====================================
# Badge helpers (optioneel, snel)
# ====================================
def _beoordeling_emoji(rate: str) -> str:
    r = (rate or "").strip().lower()
    if r in {"zeer goed", "goed"}:
        return "🟢 "
    if r in {"voldoende"}:
        return "🟠 "
    if r in {"slecht", "onvoldoende", "zeer slecht"}:
        return "🔴 "
    return ""

def naam_naar_dn(naam: str) -> str | None:
    if pd.isna(naam):
        return None
    s = str(naam).strip()
    m = PNR_RE.search(s)
    return m.group(1) if m else None

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

# ====================================
# LOGIN FLOW
# ====================================
def login_gate():
    st.title("🔐 Beveiligde toegang")
    st.caption("Log in met je personeelsnummer. Je ontvangt een verificatiecode per e-mail.")

    # Contacten laden
    try:
        mkey_s = file_mtime_key(SCHADENAME)
        df_contacts = load_contacts(SCHADENAME, mkey_s)
        contacts = get_contact_map(df_contacts)
    except Exception as e:
        st.error(str(e))
        st.stop()

    if "otp" not in st.session_state:
        st.session_state.otp = {"pnr": None, "email": None, "hash": None, "exp": 0.0, "t_last": 0.0, "sent": False}

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
                    now = time.monotonic()
                    if now - otp.get("t_last", 0.0) < OTP_RESEND_SECONDS and otp.get("pnr") == pnr_digits:
                        remaining = int(OTP_RESEND_SECONDS - (now - otp.get("t_last", 0.0)))
                        st.warning(f"Wacht {remaining}s voordat je opnieuw een code aanvraagt.")
                    else:
                        try:
                            code = gen_otp(OTP_LENGTH)
                            minutes = OTP_TTL_SECONDS // 60
                            now_str = datetime.now().strftime("%d-%m-%Y %H:%M")
                            naam = (rec.get("name") if isinstance(rec, dict) else None) or "collega"

                            subject = OTP_SUBJECT.format(code=code, minutes=minutes, pnr=pnr_digits, date=now_str, name=naam)
                            body_text = OTP_BODY_TEXT.format(code=code, minutes=minutes, pnr=pnr_digits, date=now_str, name=naam)
                            body_html_raw = (OTP_BODY_HTML or "").strip()
                            body_html = body_html_raw.format(code=code, minutes=minutes, pnr=pnr_digits, date=now_str, name=naam) if body_html_raw else None

                            _send_email(email, subject, body_text, html=body_html)

                            otp.update({
                                "pnr": pnr_digits,
                                "email": email,
                                "hash": hash_code(code),
                                "exp": now + OTP_TTL_SECONDS,
                                "t_last": now,
                                "sent": True,
                            })
                            st.success(f"Code verzonden naar {mask_email(email)}. Vul de code hieronder in.")
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
            st.session_state.otp = {"pnr": None, "email": None, "hash": None, "exp": 0.0, "t_last": 0.0, "sent": False}
            st.rerun()

        if resend:
            st.session_state.otp["sent"] = False
            st.rerun()

        if submit:
            if not code_in or len(code_in.strip()) < 1:
                st.error("Vul de code in.")
            elif time.monotonic() > otp.get("exp", 0.0):
                st.error("Code is verlopen. Vraag een nieuwe code aan.")
            elif hash_code(code_in.strip()) != otp.get("hash"):
                st.error("Ongeldige code.")
            else:
                rec = contacts.get(otp.get("pnr"))
                user_name = None
                if isinstance(rec, dict):
                    user_name = (rec.get("name") or "").strip()

                st.session_state.authenticated = True
                st.session_state.user_pnr   = otp.get("pnr")
                st.session_state.user_email = otp.get("email")
                st.session_state.user_name  = user_name or otp.get("pnr")

                st.session_state.otp = {"pnr": None, "email": None, "hash": None, "exp": 0.0, "t_last": 0.0, "sent": False}
                st.rerun()

# ====================================
# Dashboard
# ====================================
def run_dashboard():
    # Sidebar: user-info + logout
    with st.sidebar:
        display_name = (
            st.session_state.get("user_name")
            or st.session_state.get("user_pnr")
            or "—"
        )
        st.success(f"Ingelogd als {display_name}")
        if st.button("🚪 Uitloggen"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

    # Data laden (1 I/O set, gecachet)
    mkey_s = file_mtime_key(SCHADENAME)
    df, options = load_schade_prepared_fast(SCHADENAME, mkey_s)

    mkey_c = file_mtime_key("Coachingslijst.xlsx")
    gecoachte_ids, coaching_ids, total_geel, total_blauw, excel_info, coach_warn = lees_coachingslijst_fast("Coachingslijst.xlsx", mkey_c)
    st.session_state["coaching_ids"] = coaching_ids
    st.session_state["excel_info"]   = excel_info

    # extra kolommen (vectorized)
    dn = df["dienstnummer"].astype(str)
    df["gecoacht_geel"]  = dn.isin(gecoachte_ids)
    df["gecoacht_blauw"] = dn.isin(coaching_ids)

    # Titel
    st.title("📊 Schadegevallen Dashboard")
    st.caption("🟢 goed · 🟠 voldoende · 🔴 slecht/zeer slecht · ⚫ lopende coaching")
    if coach_warn:
        st.sidebar.warning(f"⚠️ {coach_warn}")

    # Sidebar-filters
    def _ms_all(label, options, all_label, key):
        opts = [all_label] + options
        picked = st.sidebar.multiselect(label, opts, default=[all_label], key=key)
        return options if (all_label in picked or not picked) else picked

    with st.sidebar:
        if Path("logo.png").exists():
            st.image("logo.png", use_container_width=True)
        st.header("🔍 Filters")

        teamcoach_options = options["teamcoach"]
        locatie_options   = options["locatie"]
        voertuig_options  = options["voertuig"]
        kwartaal_options  = options["kwartaal"]

        selected_teamcoaches = _ms_all("Teamcoach", teamcoach_options, "— Alle teamcoaches —", "flt_tc")
        selected_locaties    = _ms_all("Locatie",   locatie_options,   "— Alle locaties —",   "flt_loc")
        selected_voertuigen  = _ms_all("Voertuig",  voertuig_options,  "— Alle voertuigen —", "flt_vt")
        selected_kwartalen   = _ms_all("Kwartaal",  kwartaal_options,  "— Alle kwartalen —",  "flt_kw")

        if selected_kwartalen:
            per_idx   = pd.PeriodIndex(selected_kwartalen, freq="Q")
            date_from = per_idx.start_time.min().normalize()
            date_to   = per_idx.end_time.max().normalize()
        else:
            date_from = options["min_datum"]
            date_to   = options["max_datum"]

        if st.button("🔄 Reset filters"):
            st.query_params.clear()
            st.rerun()

    # Filter toepassen (categoricals → snelle isin)
    apply_quarters = bool(selected_kwartalen)
    if apply_quarters:
        sel_periods = pd.PeriodIndex(selected_kwartalen, freq="Q")
        mask_q = df["KwartaalP"].isin(sel_periods)
    else:
        mask_q = True

    mask = (
        df["teamcoach_disp"].isin(selected_teamcoaches)
        & df["Locatie_disp"].isin(selected_locaties)
        & df["BusTram_disp"].isin(selected_voertuigen)
        & mask_q
    )
    df_filtered = df.loc[mask]
    start = pd.to_datetime(date_from)
    end   = pd.to_datetime(date_to) + pd.Timedelta(days=1)
    df_filtered = df_filtered[(df_filtered["Datum"] >= start) & (df_filtered["Datum"] < end)]

    if df_filtered.empty:
        st.warning("⚠️ Geen schadegevallen gevonden voor de geselecteerde filters.")
        st.stop()

    # KPI + CSV export
    st.metric("Totaal aantal schadegevallen", len(df_filtered))
    @st.cache_data
    def df_to_csv_bytes(d: pd.DataFrame) -> bytes:
        return d.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download gefilterde data (CSV)",
        df_to_csv_bytes(df_filtered),
        file_name=f"schade_filtered_{datetime.today().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        help="Exporteer de huidige selectie inclusief datumfilter."
    )

    # ========= TABS (nu BINNEN run_dashboard) =========

    # Tabs aanmaken
    chauffeur_tab, voertuig_tab, locatie_tab, opzoeken_tab, coaching_tab = st.tabs(
        ["👤 Chauffeur", "🚌 Voertuig", "📍 Locatie", "🔎 Opzoeken", "🎯 Coaching"]
    )

    # Precomputes
    _name_disp_map = (
        df_filtered[["volledige naam", "volledige naam_disp"]]
        .dropna()
        .drop_duplicates(subset=["volledige naam"])
        .set_index("volledige naam")["volledige naam_disp"]
        .to_dict()
    )
    _detail_cols = [c for c in ["Datum", "BusTram_disp", "Locatie_disp", "teamcoach_disp", "Link"] if c in df_filtered.columns]

    # ===== Tab 1: Chauffeur =====
    with chauffeur_tab:
        st.subheader("📂 Schadegevallen per chauffeur")
        grp = (
            df_filtered.groupby("volledige naam", as_index=False)
                       .size()
                       .rename(columns={"size": "aantal", "volledige naam": "chauffeur_raw"})
                       .sort_values("aantal", ascending=False)
                       .reset_index(drop=True)
        )
        if grp.empty:
            st.info("Geen schadegevallen binnen de huidige filters.")
        else:
            totaal_schades = int(grp["aantal"].sum())
            aantal_ch = int(grp.shape[0])

            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Aantal chauffeurs (met schade)", aantal_ch)
                man_ch = st.number_input("Handmatig aantal chauffeurs", min_value=1, value=max(1, aantal_ch), step=1, key="chf_manual_count")
            c2.metric("Gemiddeld aantal schades", round(totaal_schades / man_ch, 2))
            c3.metric("Totaal aantal schades", totaal_schades)

            step = 5
            max_val = int(grp["aantal"].max())
            edges = list(range(0, max_val + step, step))
            if edges[-1] < max_val:
                edges.append(max_val + step)
            grp["interval"] = pd.cut(grp["aantal"], bins=edges, right=True, include_lowest=True)

            _idx_by_name = (
                df_filtered.reset_index()[["index", "volledige naam"]]
                .groupby("volledige naam")["index"]
                .apply(list)
                .to_dict()
            )

            for interval, g in grp.groupby("interval", sort=False):
                if g.empty or pd.isna(interval):
                    continue
                left, right = int(interval.left), int(interval.right)
                low = max(1, left + 1)
                with st.expander(f"{low} t/m {right} schades ({len(g)} chauffeurs)", expanded=False):
                    g = g.sort_values("aantal", ascending=False).reset_index(drop=True)

                    out_lines = []
                    for raw, aantal in zip(g["chauffeur_raw"].tolist(), g["aantal"].tolist()):
                        disp = _name_disp_map.get(raw, raw)
                        badge = badge_van_chauffeur(raw)
                        out_lines.append(f"**{badge}{disp}** — {int(aantal)} schadegevallen")

                        idxs = _idx_by_name.get(raw, [])
                        if not idxs:
                            continue
                        details = df_filtered.iloc[idxs][_detail_cols].sort_values("Datum")
                        det_lines = []
                        _has_link = "Link" in details.columns
                        for r in details.itertuples(index=False):
                            _datum = getattr(r, "Datum", pd.NaT)
                            datum_str = _datum.strftime("%d-%m-%Y") if pd.notna(_datum) else "onbekend"
                            voertuig   = getattr(r, "BusTram_disp", "onbekend")
                            loc        = getattr(r, "Locatie_disp", "onbekend")
                            coach      = getattr(r, "teamcoach_disp", "onbekend")
                            link_val   = getattr(r, "Link", None) if _has_link else None
                            link_url   = extract_url(link_val) if _has_link else None
                            prefix = f"📅 {datum_str} — 🚌 {voertuig} — 📍 {loc} — 🧑‍💼 {coach} — "
                            det_lines.append(prefix + (f"[🔗 openen]({link_url})" if link_url else "❌ geen link"))
                        if det_lines:
                            st.markdown("\n\n".join(det_lines), unsafe_allow_html=True)
                    if out_lines:
                        st.markdown("\n\n".join(out_lines))

    # ===== Tab 2: Voertuig =====
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

                _idx_by_voertuig = (
                    df_filtered.reset_index()[["index", "BusTram_disp"]]
                    .groupby("BusTram_disp")["index"]
                    .apply(list)
                    .to_dict()
                )
                _has_link_global = "Link" in df_filtered.columns

                for voertuig in counts.index.tolist():
                    kol = ["Datum", "volledige naam_disp", "Locatie_disp", "teamcoach_disp"]
                    if _has_link_global: kol.append("Link")
                    idxs = _idx_by_voertuig.get(voertuig, [])
                    sub = df_filtered.iloc[idxs][kol].sort_values("Datum")

                    with st.expander(f"{voertuig} — {len(sub)} schadegevallen", expanded=False):
                        if sub.empty:
                            continue
                        lines = []
                        _has_link = "Link" in sub.columns
                        for r in sub.itertuples(index=False):
                            _datum = getattr(r, "Datum", pd.NaT)
                            datum_str = _datum.strftime("%d-%m-%Y") if pd.notna(_datum) else "onbekend"
                            chauffeur = getattr(r, "volledige_naam_disp", getattr(r, "volledige naam_disp", "onbekend"))
                            coach     = getattr(r, "teamcoach_disp", "onbekend")
                            loc       = getattr(r, "Locatie_disp", "onbekend")
                            link_val  = getattr(r, "Link", None) if _has_link else None
                            link_url  = extract_url(link_val) if _has_link else None
                            prefix = f"📅 {datum_str} — 👤 {chauffeur} — 🧑‍💼 {coach} — 📍 {loc} — "
                            lines.append(prefix + (f"[🔗 openen]({link_url})" if link_url else "❌ geen link"))
                        if lines:
                            st.markdown("\n\n".join(lines), unsafe_allow_html=True)

    # ===== Tab 3: Locatie =====
    with locatie_tab:
        st.subheader("📍 Schadegevallen per locatie")
        if "Locatie_disp" not in df_filtered.columns:
            st.warning("⚠️ Kolom 'Locatie' niet gevonden in de huidige selectie.")
        else:
            loc_options = sorted([x for x in df_filtered["Locatie_disp"].dropna().unique().tolist() if str(x).strip()])
            gekozen_locs = st.multiselect("Zoek locatie(s)", options=loc_options, default=[], placeholder="Type om te zoeken…", key="loc_ms")

            work = df_filtered if not gekozen_locs else df_filtered[df_filtered["Locatie_disp"].isin(gekozen_locs)]
            if work.empty:
                st.info("Geen resultaten binnen de huidige filters/keuze.")
            else:
                col_top1, col_top2 = st.columns(2)
                with col_top1:
                    min_schades = st.number_input("Min. aantal schades", min_value=1, value=1, step=1, key="loc_min")
                with col_top2:
                    expand_all = st.checkbox("Alles openklappen", value=False, key="loc_expand_all")

                agg = (
                    work.groupby("Locatie_disp", as_index=False)
                        .agg(Schades=("dienstnummer", "size"),
                             Unieke_chauffeurs=("dienstnummer", lambda s: s.astype(str).nunique()))
                )
                if "BusTram_disp" in work.columns:
                    v = work.groupby("Locatie_disp")["BusTram_disp"].nunique().rename("Unieke_voertuigen")
                    agg = agg.merge(v, left_on="Locatie_disp", right_index=True, how="left")
                else:
                    agg["Unieke_voertuigen"] = 0
                if "teamcoach_disp" in work.columns:
                    t = work.groupby("Locatie_disp")["teamcoach_disp"].nunique().rename("Unieke_teamcoaches")
                    agg = agg.merge(t, left_on="Locatie_disp", right_index=True, how="left")
                else:
                    agg["Unieke_teamcoaches"] = 0

                dmin = work.groupby("Locatie_disp")["Datum"].min().rename("Eerste")
                dmax = work.groupby("Locatie_disp")["Datum"].max().rename("Laatste")
                agg = (agg.merge(dmin, left_on="Locatie_disp", right_index=True, how="left")
                          .merge(dmax, left_on="Locatie_disp", right_index=True, how="left"))
                agg = agg.rename(columns={"Locatie_disp": "Locatie"})
                agg = agg[agg["Schades"] >= int(min_schades)]

                if agg.empty:
                    st.info("Geen locaties die voldoen aan je filters.")
                else:
                    c1, c2 = st.columns(2)
                    c1.metric("Unieke locaties", int(agg.shape[0]))
                    c2.metric("Totaal schadegevallen", int(len(work)))
                    st.markdown("---")
                    st.subheader("📊 Samenvatting per locatie")
                    agg_view = agg.copy()
                    _d_ok = agg_view["Eerste"].notna() & agg_view["Laatste"].notna()
                    agg_view["Periode"] = "—"
                    agg_view.loc[_d_ok, "Periode"] = agg_view.loc[_d_ok, "Eerste"].dt.strftime("%d-%m-%Y") + " – " + agg_view.loc[_d_ok, "Laatste"].dt.strftime("%d-%m-%Y")

                    cols_show = ["Locatie","Schades","Unieke_chauffeurs","Unieke_voertuigen","Unieke_teamcoaches","Periode"]
                    st.dataframe(agg_view[cols_show].sort_values("Schades", ascending=False).reset_index(drop=True), use_container_width=True)

                    st.download_button(
                        "⬇️ Download samenvatting (CSV)",
                        agg_view[cols_show].to_csv(index=False).encode("utf-8"),
                        file_name="locaties_samenvatting.csv",
                        mime="text/csv",
                        key="dl_loc_summary"
                    )

                    st.markdown("---")
                    st.subheader("📂 Schadegevallen per locatie")

                    _idx_by_loc = (
                        work.reset_index()[["index", "Locatie_disp"]]
                        .groupby("Locatie_disp")["index"]
                        .apply(list)
                        .to_dict()
                    )
                    _has_link_global = "Link" in work.columns

                    for _, r in agg.sort_values("Schades", ascending=False).iterrows():
                        locatie = r["Locatie"]
                        idxs = _idx_by_loc.get(locatie, [])
                        if not idxs:
                            continue
                        subset = work.iloc[idxs].copy()
                        kol_list = ["Datum","volledige naam_disp","BusTram_disp"]
                        if _has_link_global: kol_list.append("Link")
                        subset = subset[kol_list].sort_values("Datum")

                        with st.expander(f"{locatie} — {len(subset)} schadegevallen", expanded=expand_all):
                            lines = []
                            _has_link = "Link" in subset.columns
                            for rr in subset.itertuples(index=False):
                                _datum = getattr(rr, "Datum", pd.NaT)
                                datum_str = _datum.strftime("%d-%m-%Y") if pd.notna(_datum) else "onbekend"
                                chauffeur = getattr(rr, "volledige_naam_disp", getattr(rr, "volledige naam_disp", "onbekend"))
                                voertuig  = getattr(rr, "BusTram_disp", "onbekend")
                                link_val  = getattr(rr, "Link", None) if _has_link else None
                                link_url  = extract_url(link_val) if _has_link else None
                                prefix = f"📅 {datum_str} — 👤 {chauffeur} — 🚌 {voertuig} — "
                                lines.append(prefix + (f"[🔗 openen]({link_url})" if link_url else "❌ geen link"))
                            if lines:
                                st.markdown("\n\n".join(lines), unsafe_allow_html=True)

    # ===== Tab 4: Opzoeken =====
    with opzoeken_tab:
        st.subheader("🔎 Opzoeken op personeelsnummer")
        zoek = st.text_input("Personeelsnummer (dienstnummer)", placeholder="bv. 41092", key="zoek_pnr_input")
        m = re.findall(r"\d+", str(zoek or "").strip())
        pnr = m[0] if m else ""

        if not pnr:
            st.info("Geef een personeelsnummer in om resultaten te zien.")
        else:
            _pnr_str = str(pnr)
            res = df_filtered[df_filtered["dienstnummer"].astype(str).str.strip() == _pnr_str].copy()
            res_all = df[df["dienstnummer"].astype(str).str.strip() == _pnr_str].copy()

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
                naam_disp = (ex_info.get(_pnr_str, {}) or {}).get("naam") or ""
                teamcoach_disp = (ex_info.get(_pnr_str, {}) or {}).get("teamcoach") or "onbekend"
                naam_raw = naam_disp

            try:
                s = str(naam_raw or "").strip()
                naam_clean = re.sub(r"^\s*\d+\s*-\s*", "", s)
            except Exception:
                naam_clean = naam_disp

            chauffeur_label = f"{_pnr_str} {naam_clean}".strip() if naam_clean else str(_pnr_str)

            set_lopend   = set(map(str, st.session_state.get("coaching_ids", set())))
            set_voltooid = set(map(str, st.session_state.get("excel_info", {}).keys()))
            if _pnr_str in set_lopend:
                status_lbl, status_emoji = "Lopend", "⚫"
                status_bron = "bron: Coaching (lopend)"
            elif _pnr_str in set_voltooid:
                beo_raw = (st.session_state.get("excel_info", {}).get(_pnr_str, {}) or {}).get("beoordeling", "")
                b = str(beo_raw or "").strip().lower()
                if b in {"zeer goed", "goed"}:
                    status_lbl, status_emoji = "Goed", "🟢"
                elif b == "voldoende":
                    status_lbl, status_emoji = "Voldoende", "🟠"
                elif b in {"onvoldoende", "slecht", "zeer slecht"}:
                    status_lbl, status_emoji = ("Onvoldoende" if b == "onvoldoende" else "Slecht"), "🔴"
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
            st.metric("Aantal schadegevallen", int(len(res)))

            if res.empty:
                st.caption("Geen schadegevallen binnen de huidige filters.")
            else:
                res = res.sort_values("Datum", ascending=False).copy()
                heeft_link = "Link" in res.columns
                if heeft_link:
                    res["URL"] = res["Link"].apply(extract_url)

                kol = ["Datum", "Locatie_disp"] + (["URL"] if heeft_link else [])
                column_config = {
                    "Datum": st.column_config.DateColumn("Datum", format="DD-MM-YYYY"),
                    "Locatie_disp": st.column_config.TextColumn("Locatie"),
                }
                if heeft_link:
                    column_config["URL"] = st.column_config.LinkColumn("Link", display_text="openen")

                st.dataframe(res[kol], column_config=column_config, use_container_width=True)

    # ===== Tab 5: Coaching =====
    with coaching_tab:
        try:
            st.subheader("🎯 Coaching – vergelijkingen")

            set_lopend_all   = set(map(str, st.session_state.get("coaching_ids", set())))
            set_voltooid_all = set(map(str, st.session_state.get("excel_info", {}).keys()))

            r1, r2 = st.columns(2)
            r1.metric("🧾 Lopend – ruwe rijen (coachingslijst)",   total_blauw)
            r2.metric("🧾 Voltooid – ruwe rijen (coachingslijst)", total_geel)

            pnrs_schade_sel = set(df_filtered["dienstnummer"].dropna().astype(str))
            s1, s2 = st.columns(2)
            s1.metric("🔵 Lopend (in schadelijst)",   len(pnrs_schade_sel & set_lopend_all))
            s2.metric("🟡 Voltooid (in schadelijst)", len(pnrs_schade_sel & set_voltooid_all))

            st.markdown("---")
            st.markdown("## 🔎 Vergelijking schadelijst ↔ Coachingslijst")

            status_keuze = st.radio(
                "Welke status vergelijken?",
                options=["Lopend","Voltooid","Beide"],
                index=0,
                horizontal=True,
                key="coach_status_select"
            )
            if status_keuze == "Lopend":
                set_coach_sel = set_lopend_all
            elif status_keuze == "Voltooid":
                set_coach_sel = set_voltooid_all
            else:
                set_coach_sel = set_lopend_all | set_voltooid_all

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
                pnrs_sorted = sorted(map(str, pnrs_set))
                _names = [_naam(p) for p in pnrs_sorted]
                _badges = [badge_van_chauffeur(f"{p} - {n}") for p, n in zip(pnrs_sorted, _names)]
                _status = [_status_volledig(p) for p in pnrs_sorted]
                df_out = pd.DataFrame({
                    "Dienstnr": pnrs_sorted,
                    "Naam": [f"{b}{n}" for b, n in zip(_badges, _names)],
                    "Status (coachinglijst)": _status
                })
                return df_out.sort_values(["Naam"]).reset_index(drop=True)

            with st.expander(f"🟦 In Coachinglijst maar niet in schadelijst ({len(coach_niet_in_schade)})", expanded=False):
                df_a = _make_table(coach_niet_in_schade)
                st.dataframe(df_a, use_container_width=True) if not df_a.empty else st.caption("Geen resultaten.")
                if not df_a.empty:
                    st.download_button(
                        "⬇️ Download CSV (coaching ∧ ¬schade)",
                        df_a.to_csv(index=False).encode("utf-8"),
                        file_name="coaching_zonder_schade.csv",
                        mime="text/csv",
                        key="dl_coach_not_schade"
                    )

            with st.expander(f"🟥 In schadelijst maar niet in Coachinglijst ({len(schade_niet_in_coach)})", expanded=False):
                df_b = _make_table(schade_niet_in_coach)
                st.dataframe(df_b, use_container_width=True) if not df_b.empty else st.caption("Geen resultaten.")
                if not df_b.empty:
                    st.download_button(
                        "⬇️ Download CSV (schade ∧ ¬coaching)",
                        df_b.to_csv(index=False).encode("utf-8"),
                        file_name="schade_zonder_coaching.csv",
                        mime="text/csv",
                        key="dl_schade_not_coach"
                    )

            st.markdown("---")
            st.markdown("## 🚩 schades en niet in *Coaching* of *Voltooid*")
            gebruik_filters_s = st.checkbox(
                "Tel schades binnen huidige filters (uit = volledige dataset)",
                value=False,
                key="more_schades_use_filters"
            )
            df_basis_s = df_filtered if gebruik_filters_s else df
            thr = st.number_input(
                "Toon bestuurders met méér dan ... schades",
                min_value=1, value=2, step=1, key="more_schades_threshold"
            )
            pnr_counts = df_basis_s["dienstnummer"].dropna().astype(str).value_counts()
            pnrs_meer_dan = set(pnr_counts[pnr_counts > thr].index)
            set_coaching_all = set_lopend_all | set_voltooid_all
            result_set = pnrs_meer_dan - set_coaching_all

            if result_set:
                pnrs_sorted = sorted(result_set, key=lambda x: (-pnr_counts.get(x, 0), x))
                _names = [_naam(p) for p in pnrs_sorted]
                _badges = [badge_van_chauffeur(f"{p} - {n}") for p, n in zip(pnrs_sorted, _names)]
                df_no_coach = pd.DataFrame({
                    "Dienstnr": pnrs_sorted,
                    "Naam": [f"{b}{n}" for b, n in zip(_badges, _names)],
                    "Schades": [int(pnr_counts.get(p, 0)) for p in pnrs_sorted],
                    "Status (coachinglijst)": ["Niet aangevraagd"] * len(pnrs_sorted)
                }).sort_values(["Schades","Naam"], ascending=[False,True]).reset_index(drop=True)
            else:
                df_no_coach = pd.DataFrame(columns=["Dienstnr","Naam","Schades","Status (coachinglijst)"])

            with st.expander(f"🟥 > {thr} schades en niet in coaching/voltooid ({len(result_set)})", expanded=True):
                if df_no_coach.empty:
                    st.caption("Geen resultaten.")
                    st.caption(f"PNR's >{thr} vóór uitsluiting: {len(pnrs_meer_dan)}")
                    st.caption(f"Uitgesloten door coaching/voltooid: {len(pnrs_meer_dan & set_coaching_all)}")
                else:
                    st.dataframe(df_no_coach, use_container_width=True)
                    st.download_button(
                        "⬇️ Download CSV",
                        df_no_coach.to_csv(index=False).encode("utf-8"),
                        file_name=f"meerdan_{thr}_schades_niet_in_coaching_voltooid.csv",
                        mime="text/csv",
                        key="dl_more_schades_no_coaching"
                    )

        except Exception as e:
            st.error("Er ging iets mis in het Coaching-tab.")
            st.exception(e)

def main():
    st.set_page_config(page_title="Schade Dashboard", page_icon="📊", layout="wide")
    if not st.session_state.get("authenticated"):
        login_gate()
    else:
        run_dashboard()

if __name__ == "__main__":
    main()
