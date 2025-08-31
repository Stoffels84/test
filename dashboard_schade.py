import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
import tempfile
import hashlib
from datetime import datetime
import os
import re
from streamlit_autorefresh import st_autorefresh

# ========= Instellingen =========
LOGIN_ACTIEF = False  # Zet True om login te activeren
plt.rcParams["figure.dpi"] = 150
st.set_page_config(page_title="Schadegevallen Dashboard", layout="wide")

# 🔄 Auto-refresh: herlaad de pagina elk uur
st_autorefresh(interval=3600 * 1000, key="data_refresh")

# ========= Helpers =========
def hash_wachtwoord(wachtwoord: str) -> str:
    return hashlib.sha256(str(wachtwoord).encode()).hexdigest()

@st.cache_data(show_spinner=False, ttl=3600)  # cache max 1 uur geldig
def load_excel(path, **kwargs):
    try:
        return pd.read_excel(path, **kwargs)
    except FileNotFoundError:
        st.error(f"Bestand niet gevonden: {path}")
        st.stop()
    except Exception as e:
        st.error(f"Kon '{path}' niet lezen: {e}")
        st.stop()

def naam_naar_dn(naam: str) -> str | None:
    """Haal dienstnummer uit 'volledige naam' zoals '1234 - Voornaam Achternaam'."""
    if pd.isna(naam):
        return None
    s = str(naam).strip()
    m = re.match(r"\s*(\d+)", s)
    return m.group(1) if m else None

def toon_chauffeur(x):
    """Geef nette chauffeur-naam terug, met fallback. Knipt vooraan '1234 - ' weg."""
    if x is None or pd.isna(x):
        return "onbekend"
    s = str(x).strip()
    if not s or s.lower() in {"nan", "none", "<na>"}:
        return "onbekend"
    s = re.sub(r"^\s*\d+\s*-\s*", "", s)  # strip '1234 - ' of '1234-'
    return s

def safe_name(x) -> str:
    """Netjes tonen; vermijd 'nan'/'none'/lege strings."""
    s = "" if x is pd.NA else str(x or "").strip()
    return "onbekend" if s.lower() in {"nan", "none", ""} else s

def _parse_excel_dates(series: pd.Series) -> pd.Series:
    """Robuuste datumparser: probeer EU (dayfirst) en val terug op US (monthfirst)."""
    d1 = pd.to_datetime(series, errors="coerce", dayfirst=True)
    need_retry = d1.isna()
    if need_retry.any():
        d2 = pd.to_datetime(series[need_retry], errors="coerce", dayfirst=False)
        d1.loc[need_retry] = d2
    return d1

# Kleine helper om hyperlinks uit Excel-formules te halen
HYPERLINK_RE = re.compile(r'HYPERLINK\(\s*"([^"]+)"', re.IGNORECASE)
def extract_url(x) -> str | None:
    if pd.isna(x):
        return None
    s = str(x).strip()
    if s.startswith(("http://", "https://")):
        return s
    m = HYPERLINK_RE.search(s)
    return m.group(1) if m else None

# ========= Kleuren / status =========
COLOR_GEEL  = "#FFD54F"  # voltooide coaching
COLOR_BLAUW = "#2196F3"  # in coaching
COLOR_MIX   = "#7E57C2"  # beide
COLOR_GRIJS = "#BDBDBD"  # geen

# === Nieuw: badge op basis van beoordeling + lopend ===
def _beoordeling_emoji(rate: str) -> str:
    r = (rate or "").strip().lower()
    if r in {"zeer goed", "goed", "voldoende"}:
        return "🟢 "
    if r in {"slecht", "onvoldoende", "zeer slecht"}:
        return "🔴 "
    return ""  # geen beoordeling bekend

def badge_van_chauffeur(naam: str) -> str:
    """
    Bepaalt de badges voor een chauffeur:
    - Groen/Oranje/Rood op basis van 'Beoordeling coaching' uit excel_info
    - Zwart erbij als er een lopende coaching is
    """
    dn = naam_naar_dn(naam)
    if not dn:
        return ""
    sdn = str(dn).strip()
    info = excel_info.get(sdn, {})
    beoordeling = info.get("beoordeling")
    status_excel = info.get("status")  # "Voltooid" of "Coaching"
    kleur = _beoordeling_emoji(beoordeling)
    lopend = (status_excel == "Coaching") or (sdn in coaching_ids)
    return f"{kleur}{'⚫ ' if lopend else ''}"

# ========= Coachingslijst inlezen (incl. naam/teamcoach uit Excel) =========
@st.cache_data(show_spinner=False)
def lees_coachingslijst(pad="Coachingslijst.xlsx"):
    """
    Leest Coachingslijst.xlsx en retourneert:
    - ids_geel: set met unieke P-nrs 'Voltooide coachings'
    - ids_blauw: set met unieke P-nrs 'Coaching'
    - total_geel_rows: totaal # rijen (incl. dubbels) in 'Voltooide coachings'
    - total_blauw_rows: totaal # rijen (incl. dubbels) in 'Coaching'
    - excel_info: dict[pnr] -> {'naam': 'Voornaam Achternaam', 'teamcoach': ..., 'status': ..., 'beoordeling': ...}
    - warn: eventuele foutmelding
    """
    ids_geel, ids_blauw = set(), set()
    total_geel_rows, total_blauw_rows = 0, 0
    excel_info = {}
    try:
        xls = pd.ExcelFile(pad)
    except Exception as e:
        return ids_geel, ids_blauw, total_geel_rows, total_blauw_rows, excel_info, f"Coachingslijst niet gevonden of onleesbaar: {e}"

    def vind_sheet(xls, naam):
        return next((s for s in xls.sheet_names if s.strip().lower() == naam), None)

    # Aliases
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
        kol_rate  = next((k for k in rating_keys if k in dfc.columns), None)  # alleen in 'Voltooide coachings'

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

# ========= DATA LADEN & VOORBEREIDEN (SNEL) =========
def _clean_display_series(s: pd.Series) -> pd.Series:
    """Vectorized 'safe_name' zonder Python-loops."""
    s = s.astype("string").str.strip()
    bad = s.isna() | s.eq("") | s.str.lower().isin({"nan", "none", "<na>"})
    return s.mask(bad, "onbekend")

@st.cache_data(show_spinner=False, ttl=3600)
def load_schade_prepared(path="schade met macro.xlsm", sheet="BRON"):
    # 1) Inlezen + kolommen trimmen
    df_raw = pd.read_excel(path, sheet_name=sheet)
    df_raw.columns = df_raw.columns.str.strip()

    # 2) Datum robuust + filteren op geldige datums
    d1 = pd.to_datetime(df_raw["Datum"], errors="coerce", dayfirst=True)
    need_retry = d1.isna()
    if need_retry.any():
        d2 = pd.to_datetime(df_raw.loc[need_retry, "Datum"], errors="coerce", dayfirst=False)
        d1.loc[need_retry] = d2
    df_raw["Datum"] = d1
    df_ok = df_raw[df_raw["Datum"].notna()].copy()

    # 3) Strings strippen (vectorized)
    for col in ("volledige naam", "teamcoach", "Locatie", "Bus/ Tram", "Link"):
        if col in df_ok.columns:
            df_ok[col] = df_ok[col].astype("string").str.strip()

    # 4) Afgeleide velden
    df_ok["dienstnummer"] = (
        df_ok["volledige naam"].astype(str).str.extract(r"^(\d+)", expand=False).astype("string").str.strip()
    )
    df_ok["KwartaalP"] = df_ok["Datum"].dt.to_period("Q")
    df_ok["Kwartaal"]  = df_ok["KwartaalP"].astype(str)

    # 5) Display-kolommen
    df_ok["volledige naam_disp"] = _clean_display_series(df_ok["volledige naam"])
    df_ok["teamcoach_disp"]      = _clean_display_series(df_ok["teamcoach"])
    df_ok["Locatie_disp"]        = _clean_display_series(df_ok["Locatie"])
    df_ok["BusTram_disp"]        = _clean_display_series(df_ok["Bus/ Tram"])

    # 6) Optielijsten + datumbereik
    options = {
        "teamcoach": sorted(df_ok["teamcoach_disp"].dropna().unique().tolist()),
        "locatie":   sorted(df_ok["Locatie_disp"].dropna().unique().tolist()),
        "voertuig":  sorted(df_ok["BusTram_disp"].dropna().unique().tolist()),
        "kwartaal":  sorted(df_ok["KwartaalP"].dropna().astype(str).unique().tolist()),
        "min_datum": df_ok["Datum"].min().normalize(),
        "max_datum": df_ok["Datum"].max().normalize(),
    }
    return df_ok, options

@st.cache_data
def df_to_csv_bytes(d: pd.DataFrame) -> bytes:
    return d.to_csv(index=False).encode("utf-8")

# === laad + prepare (cached) ===
df, options = load_schade_prepared()

# ========= Gebruikersbestand (login) =========
gebruikers_df = load_excel("chauffeurs.xlsx")
gebruikers_df.columns = gebruikers_df.columns.str.strip().str.lower()

# normaliseer kolommen (login/wachtwoord varianten)
kol_map = {}
if "gebruikersnaam" in gebruikers_df.columns:
    kol_map["gebruikersnaam"] = "gebruikersnaam"
elif "login" in gebruikers_df.columns:
    kol_map["login"] = "gebruikersnaam"

if "paswoord" in gebruikers_df.columns:
    kol_map["paswoord"] = "paswoord"
elif "wachtwoord" in gebruikers_df.columns:
    kol_map["wachtwoord"] = "paswoord"

for c in ["rol", "dienstnummer", "laatste login"]:
    if c in gebruikers_df.columns:
        kol_map[c] = c
gebruikers_df = gebruikers_df.rename(columns=kol_map)

# Vereisten check
vereist_login_kolommen = {"gebruikersnaam", "paswoord"}
missend_login = [c for c in vereist_login_kolommen if c not in gebruikers_df.columns]
if missend_login:
    st.error(f"Login configuratie onvolledig. Ontbrekende kolommen (na normalisatie): {', '.join(missend_login)}")
    st.stop()

# Strings netjes
gebruikers_df["gebruikersnaam"] = gebruikers_df["gebruikersnaam"].astype(str).str.strip()
gebruikers_df["paswoord"] = gebruikers_df["paswoord"].astype(str).str.strip()
for c in ["rol", "dienstnummer", "laatste login"]:
    if c not in gebruikers_df.columns:
        gebruikers_df[c] = pd.NA

# Session login status
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if LOGIN_ACTIEF and not st.session_state.logged_in:
    st.title("🔐 Inloggen")
    username = st.text_input("Gebruikersnaam")
    password = st.text_input("Wachtwoord", type="password")
    if st.button("Log in"):
        rij = gebruikers_df.loc[gebruikers_df["gebruikersnaam"] == str(username).strip()]
        if not rij.empty:
            opgeslagen = str(rij["paswoord"].iloc[0])
            ok = (opgeslagen == str(password)) or (opgeslagen == hash_wachtwoord(password))
            if ok:
                st.session_state.logged_in = True
                st.session_state.username = str(username).strip()
                st.success("✅ Ingelogd!")
                if "laatste login" in gebruikers_df.columns:
                    try:
                        gebruikers_df.loc[rij.index, "laatste login"] = datetime.now()
                        gebruikers_df.to_excel("chauffeurs.xlsx", index=False)
                    except Exception as e:
                        st.warning(f"Kon 'laatste login' niet opslaan: {e}")
                st.rerun()
            else:
                st.error("❌ Onjuiste gebruikersnaam of wachtwoord.")
        else:
            st.error("❌ Onjuiste gebruikersnaam of wachtwoord.")
    st.stop()
else:
    if not LOGIN_ACTIEF:
        st.session_state.logged_in = True
        st.session_state.username = "demo"

# Rol + naam
if not LOGIN_ACTIEF:
    rol = "teamcoach"; naam = "demo"
else:
    ingelogde_info = gebruikers_df.loc[gebruikers_df["gebruikersnaam"] == st.session_state.username].iloc[0]
    rol = str(ingelogde_info.get("rol", "teamcoach")).strip()
    if rol == "chauffeur":
        naam = str(ingelogde_info.get("dienstnummer", ingelogde_info["gebruikersnaam"]))
    else:
        naam = str(ingelogde_info["gebruikersnaam"]).strip()

# ========= Coachingslijst =========
gecoachte_ids, coaching_ids, totaal_voltooid_rijen, totaal_lopend_rijen, excel_info, coach_warn = lees_coachingslijst()
if coach_warn:
    st.sidebar.warning(f"⚠️ {coach_warn}")

# Flags op df (optioneel)
df["gecoacht_geel"]  = df["dienstnummer"].astype(str).isin(gecoachte_ids)
df["gecoacht_blauw"] = df["dienstnummer"].astype(str).isin(coaching_ids)

# ========= UI: Titel + Caption =========
st.title("📊 Schadegevallen Dashboard")
st.caption("🟢 = goede beoordeling · 🟠 = voldoende · 🔴 = slecht/zeer slecht · ⚫ = lopende coaching")

# ========= Query params presets =========
qp = st.query_params  # Streamlit 1.32+

def _clean_list(values, allowed):
    return [v for v in (values or []) if v in allowed]

# Opties (uit de caching 'options')
teamcoach_options = options["teamcoach"]
locatie_options   = options["locatie"]
voertuig_options  = options["voertuig"]
kwartaal_options  = options["kwartaal"]

# Prefs uit URL
pref_tc = _clean_list(qp.get_all("teamcoach"), teamcoach_options) or teamcoach_options
pref_lo = _clean_list(qp.get_all("locatie"),  locatie_options)  or locatie_options
pref_vh = _clean_list(qp.get_all("voertuig"),  voertuig_options) or voertuig_options
pref_kw = _clean_list(qp.get_all("kwartaal"),  kwartaal_options)  or kwartaal_options

with st.sidebar:
    st.image("logo.png", use_container_width=True)
    st.header("🔍 Filters")

    # Helperfunctie: multiselect met "Alle"-optie
    def multiselect_all(label, options, all_label, key):
        opts_with_all = [all_label] + options
        picked_raw = st.multiselect(label, options=opts_with_all, default=[all_label], key=key)
        picked = options if (all_label in picked_raw or len(picked_raw) == 0) else picked_raw
        return picked

    # Teamcoach
    selected_teamcoaches = multiselect_all(
        "Teamcoach", teamcoach_options, "— Alle teamcoaches —", key="filter_teamcoach"
    )

    # Locatie
    selected_locaties = multiselect_all(
        "Locatie", locatie_options, "— Alle locaties —", key="filter_locatie"
    )

    # Voertuig
    selected_voertuigen = multiselect_all(
        "Voertuigtype", voertuig_options, "— Alle voertuigen —", key="filter_voertuig"
    )

    # Kwartaal
    selected_kwartalen = multiselect_all(
        "Kwartaal", kwartaal_options, "— Alle kwartalen —", key="filter_kwartaal"
    )

    # Periode afleiden uit kwartalen of volledige dataset
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

# === Filters toepassen ===
apply_quarters = bool(selected_kwartalen)
sel_periods = pd.PeriodIndex(selected_kwartalen, freq="Q") if apply_quarters else None

mask = (
    df["teamcoach_disp"].isin(selected_teamcoaches)
    & df["Locatie_disp"].isin(selected_locaties)
    & df["BusTram_disp"].isin(selected_voertuigen)
    & (df["KwartaalP"].isin(sel_periods) if apply_quarters else True)
)
df_filtered = df.loc[mask]

# Datumfilter
start = pd.to_datetime(date_from)
end   = pd.to_datetime(date_to) + pd.Timedelta(days=1)  # inclusief einddag
mask_date = (df_filtered["Datum"] >= start) & (df_filtered["Datum"] < end)
df_filtered = df_filtered.loc[mask_date]

if df_filtered.empty:
    st.warning("⚠️ Geen schadegevallen gevonden voor de geselecteerde filters.")
    st.stop()

# ========= KPI + export =========
st.metric("Totaal aantal schadegevallen", len(df_filtered))
st.download_button(
    "⬇️ Download gefilterde data (CSV)",
    df_to_csv_bytes(df_filtered),  # gecachete bytes
    file_name=f"schade_filtered_{datetime.today().strftime('%Y%m%d')}.csv",
    mime="text/csv",
    help="Exporteer de huidige selectie inclusief datumfilter."
)

# ========= Tabs =========
chauffeur_tab, voertuig_tab, locatie_tab, opzoeken_tab, coaching_tab = st.tabs(
    ["👤 Chauffeur", "🚌 Voertuig", "📍 Locatie", "🔎 Opzoeken", "🎯 Coaching"]
)

# ========= PDF Export (per teamcoach) =========
st.markdown("---")
st.sidebar.subheader("📄 PDF Export per teamcoach")
pdf_coach = st.sidebar.selectbox("Kies teamcoach voor export", teamcoach_options)
generate_pdf = st.sidebar.button("Genereer PDF")

if generate_pdf:
    kolommen_pdf = ["Datum", "volledige naam_disp", "Locatie_disp", "BusTram_disp"]
    if "Link" in df.columns:
        kolommen_pdf.append("Link")

    schade_pdf = df_filtered[df_filtered["teamcoach_disp"] == pdf_coach][kolommen_pdf].copy()
    schade_pdf = schade_pdf.sort_values(by="Datum")
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph(f"Overzicht schadegevallen - Teamcoach: <b>{pdf_coach}</b>", styles["Title"]))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"📅 Rapportdatum: {datetime.today().strftime('%d-%m-%Y')}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    totaal = len(schade_pdf)
    elements.append(Paragraph(f"📌 Totaal aantal schadegevallen: <b>{totaal}</b>", styles["Normal"]))
    elements.append(Spacer(1, 12))

    if not schade_pdf.empty:
        eerste_datum = schade_pdf["Datum"].min().strftime("%d-%m-%Y")
        laatste_datum = schade_pdf["Datum"].max().strftime("%d-%m-%Y")
        elements.append(Paragraph("📊 Samenvatting:", styles["Heading2"]))
        elements.append(Paragraph(f"- Periode: {eerste_datum} t/m {laatste_datum}", styles["Normal"]))
        elements.append(Paragraph(f"- Unieke chauffeurs: {schade_pdf['volledige naam_disp'].nunique()}", styles["Normal"]))
        elements.append(Paragraph(f"- Unieke locaties: {schade_pdf['Locatie_disp'].nunique()}", styles["Normal"]))
        elements.append(Spacer(1, 12))

    aantal_per_chauffeur = schade_pdf["volledige naam_disp"].value_counts()
    elements.append(Paragraph("👤 Aantal schadegevallen per chauffeur:", styles["Heading2"]))
    for nm, count in aantal_per_chauffeur.items():
        elements.append(Paragraph(f"- {safe_name(nm)}: {count}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    aantal_per_locatie = schade_pdf["Locatie_disp"].value_counts()
    elements.append(Paragraph("📍 Aantal schadegevallen per locatie:", styles["Heading2"]))
    for loc, count in aantal_per_locatie.items():
        elements.append(Paragraph(f"- {safe_name(loc)}: {count}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    chart_path = None
    if not schade_pdf.empty:
        schade_pdf["Maand"] = schade_pdf["Datum"].dt.to_period("M").astype(str)
        maand_data = schade_pdf["Maand"].value_counts().sort_index()
        fig, ax = plt.subplots()
        maand_data.plot(kind="bar", ax=ax)
        ax.set_title("Schadegevallen per maand")
        ax.set_ylabel("Aantal")
        plt.xticks(rotation=45)
        plt.tight_layout()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
            fig.savefig(tmpfile.name, dpi=150)
            plt.close(fig)
            chart_path = tmpfile.name
            elements.append(Paragraph("📊 Schadegevallen per maand:", styles["Heading2"]))
            elements.append(Paragraph("Deze grafiek toont het aantal gemelde schadegevallen per maand voor deze teamcoach.", styles["Italic"]))
            elements.append(Spacer(1, 6))
            elements.append(Image(tmpfile.name, width=400, height=200))
            elements.append(Spacer(1, 12))

    # Compacte tabel met individuele schadegevallen
    elements.append(Paragraph("📂 Individuele schadegevallen:", styles["Heading2"]))
    elements.append(Spacer(1, 6))

    kol_head = ["Datum", "Chauffeur", "Voertuig", "Locatie"]
    heeft_link = "Link" in schade_pdf.columns
    if heeft_link:
        kol_head.append("Link")

    tabel_data = [kol_head]
    for _, row in schade_pdf.iterrows():
        datum = row["Datum"].strftime("%d-%m-%Y") if pd.notna(row["Datum"]) else "onbekend"
        nm = row["volledige naam_disp"]; voertuig = row["BusTram_disp"]; locatie = row["Locatie_disp"]
        rij = [datum, nm, voertuig, locatie]
        if heeft_link:
            link = extract_url(row.get("Link"))
            rij.append(link if link else "-")
        tabel_data.append(rij)

    if len(tabel_data) > 1:
        colw = [60, 150, 70, 130] + ([120] if heeft_link else [])
        tbl = Table(tabel_data, repeatRows=1, colWidths=colw)
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
    bestandsnaam = f"schade_{pdf_coach.replace(' ', '_')}_{datetime.today().strftime('%Y%m%d')}.pdf"
    st.sidebar.download_button(label="📥 Download PDF", data=buffer, file_name=bestandsnaam, mime="application/pdf")

    if chart_path and os.path.exists(chart_path):
        try:
            os.remove(chart_path)
        except Exception:
            pass



# ========= TAB 1: Chauffeur (snel & simpel) =========
with chauffeur_tab:
    st.subheader("📂 Schadegevallen per chauffeur")

    # Tel op basis van rauwe 'volledige naam' zodat PNR zichtbaar blijft voor badges
    grp = (
        df_filtered.groupby("volledige naam")
        .size()
        .sort_values(ascending=False)
        .reset_index(name="aantal")
        .rename(columns={"volledige naam": "chauffeur_raw"})
    )

    if grp.empty:
        st.info("Geen schadegevallen binnen de huidige filters.")
    else:
        # KPI's
        totaal_schades = int(grp["aantal"].sum())
        totaal_chauffeurs_auto = int(grp.shape[0])

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Aantal chauffeurs (met schade)", totaal_chauffeurs_auto)
            man_ch = st.number_input(
                "Handmatig aantal chauffeurs",
                min_value=1, value=max(1, totaal_chauffeurs_auto), step=1,
                key="chf_manual_count"
            )
        c2.metric("Gemiddeld aantal schades", round(totaal_schades / man_ch, 2))
        c3.metric("Totaal aantal schades", totaal_schades)

        # Intervallen (5,10,15,...)
        step = 5
        max_val = int(grp["aantal"].max())
        edges = list(range(0, max_val + step, step))
        if not edges or edges[-1] < max_val: edges.append(max_val + step)

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

                    # detailregels
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

# ========= TAB 2: Voertuig (snel, zonder grafieken) =========
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
                if "Link" in df_filtered.columns: kol_list.append("Link")
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

# ========= TAB 3: Locatie (schoon, geen coaching-info) =========
with locatie_tab:
    st.subheader("📍 Schadegevallen per locatie")
    ok = True

    if "Locatie_disp" not in df_filtered.columns:
        st.warning("⚠️ Kolom 'Locatie' niet gevonden in de huidige selectie.")
        ok = False

    if ok:
        loc_options = sorted([x for x in df_filtered["Locatie_disp"].dropna().unique().tolist() if str(x).strip()])
        gekozen_locs = st.multiselect(
            "Zoek locatie(s)",
            options=loc_options,
            default=[],
            placeholder="Type om te zoeken…",
            key="loc_ms",
        )
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
            work.groupby("Locatie_disp")
            .agg(Schades=("dienstnummer_s","size"), Unieke_chauffeurs=("dienstnummer_s","nunique"))
            .reset_index()
            .rename(columns={"Locatie_disp":"Locatie"})
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
        agg_view["Periode"] = agg_view.apply(
            lambda r: f"{r['Eerste']:%d-%m-%Y} – {r['Laatste']:%d-%m-%Y}" if pd.notna(r["Eerste"]) and pd.notna(r["Laatste"]) else "—",
            axis=1
        )
        cols_show = ["Locatie","Schades","Unieke_chauffeurs","Unieke_voertuigen","Unieke_teamcoaches","Periode"]
        st.dataframe(
            agg_view[cols_show].sort_values("Schades", ascending=False).reset_index(drop=True),
            use_container_width=True
        )
        st.download_button(
            "⬇️ Download samenvatting (CSV)",
            agg_view[cols_show].to_csv(index=False).encode("utf-8"),
            file_name="locaties_samenvatting.csv",
            mime="text/csv",
            key="dl_loc_summary",
        )

        st.markdown("---")
        st.subheader("📂 Schadegevallen per locatie")

        for _, r in agg.sort_values("Schades", ascending=False).iterrows():
            locatie = r["Locatie"]
            subset = work.loc[work["Locatie_disp"] == locatie].copy()
            if subset.empty: 
                continue

            kol_list = ["Datum","volledige naam_disp","BusTram_disp"]
            if "Link" in subset.columns: kol_list.append("Link")
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

# ========= TAB 4: Opzoeken (met coachingstatus) =========
with opzoeken_tab:
    st.subheader("🔎 Opzoeken op personeelsnummer")

    zoek = st.text_input(
        "Personeelsnummer (dienstnummer)",
        placeholder="bv. 41092",
        key="zoek_pnr_input"
    )
    dn_hits = re.findall(r"\d+", str(zoek).strip())
    pnr = dn_hits[0] if dn_hits else ""

    if not pnr:
        st.info("Geef een personeelsnummer in om resultaten te zien.")
    else:
        # Binnen huidige filters (voor teller/tabel)
        res = df_filtered[df_filtered["dienstnummer"].astype(str).str.strip() == pnr].copy()
        # Volledige dataset (fallbacks)
        res_all = df[df["dienstnummer"].astype(str).str.strip() == pnr].copy()

        # Naam + teamcoach
        if not res.empty:
            naam_disp = res["volledige naam_disp"].iloc[0]
            teamcoach_disp = res["teamcoach_disp"].iloc[0] if "teamcoach_disp" in res.columns else "onbekend"
            naam_raw = res["volledige naam"].iloc[0] if "volledige naam" in res.columns else naam_disp
        elif not res_all.empty:
            naam_disp = res_all["volledige naam_disp"].iloc[0]
            teamcoach_disp = res_all["teamcoach_disp"].iloc[0] if "teamcoach_disp" in res_all.columns else "onbekend"
            naam_raw = res_all["volledige naam"].iloc[0] if "volledige naam" in res_all.columns else naam_disp
        else:
            naam_disp = (excel_info.get(pnr, {}) or {}).get("naam") or ""
            teamcoach_disp = (excel_info.get(pnr, {}) or {}).get("teamcoach") or "onbekend"
            naam_raw = naam_disp

        # Dubbele PNR uit naam strippen
        try:
            naam_clean = toon_chauffeur(naam_raw)
        except Exception:
            naam_clean = re.sub(rf"^\s*{re.escape(str(pnr))}\s*-?\s*", "", str(naam_raw or "")).strip()
            naam_clean = re.sub(r"^\s*\d+\s*-\s*", "", naam_clean).strip()

        chauffeur_label = f"{pnr} {naam_clean}".strip() if naam_clean else str(pnr)

        # Coachingstatus
        set_lopend   = set(map(str, coaching_ids))
        set_voltooid = set(map(str, gecoachte_ids))
        if pnr in set_lopend:
            status_lbl, status_emoji = "Lopend", "⚫"
            status_bron = "bron: Coaching (lopend)"
        elif pnr in set_voltooid:
            beo_raw = (excel_info.get(pnr, {}) or {}).get("beoordeling", "")
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
            status_bron = "bron: niet aanwezig in Coachingslijst.xlsx"

        # Header
        st.markdown(f"**👤 Chauffeur:** {chauffeur_label}")
        st.markdown(f"**🧑‍💼 Teamcoach:** {teamcoach_disp}")
        st.markdown(f"**🎯 Coachingstatus:** {status_emoji} {status_lbl}  \n*{status_bron}*")
        st.markdown("---")

        # Teller + tabel
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
                "Locatie_disp": st.column_config.TextColumn("Locatie"),
            }
            if heeft_link:
                column_config["URL"] = st.column_config.LinkColumn("Link", display_text="openen")
            st.dataframe(res[kol], column_config=column_config, use_container_width=True)

# ========= TAB 5: Coaching (snel, zonder st.stop) =========
with coaching_tab:
    try:
        st.subheader("🎯 Coaching – vergelijkingen")

        # Sets (string) + optionele TC-filter
        set_lopend_all   = set(map(str, coaching_ids))     # Excel: sheet 'Coaching'
        set_voltooid_all = set(map(str, gecoachte_ids))    # Excel: sheet 'Voltooide coachings'

        def _filter_by_tc(pnrs: set[str]) -> set[str]:
            if not selected_teamcoaches:
                return set(pnrs)
            out = set()
            for p in pnrs:
                tc = (excel_info.get(p, {}) or {}).get("teamcoach")
                if tc in selected_teamcoaches:
                    out.add(p)
            return out

        set_lopend_tc   = _filter_by_tc(set_lopend_all)
        set_voltooid_tc = _filter_by_tc(set_voltooid_all)

        # PNRS in huidige schadelijst
        pnrs_schade_sel = set(df_filtered["dienstnummer"].dropna().astype(str))

        # KPI's
        c1, c2 = st.columns(2)
        c1.metric("🔵 Lopend (in schadelijst)", len(pnrs_schade_sel & set_lopend_tc))
        c2.metric("🟡 Voltooid (in schadelijst)", len(pnrs_schade_sel & set_voltooid_tc))
        st.markdown("---")

        # Totale aantallen uit Excel
        r1, r2 = st.columns(2)
        r1.metric("🔵 Unieke personen (Coaching, Excel)", len(set_lopend_all))
        r2.metric("🟡 Unieke personen (Voltooid, Excel)", len(set_voltooid_all))

        st.markdown("---")
        st.markdown("## 🔎 Vergelijking schadelijst ↔ Coachingslijst")

        status_keuze = st.radio(
            "Welke status vergelijken?",
            options=["Lopend","Voltooid","Beide"], index=0, horizontal=True, key="coach_status_select"
        )
        if status_keuze == "Lopend":
            set_coach_sel = set_lopend_tc
        elif status_keuze == "Voltooid":
            set_coach_sel = set_voltooid_tc
        else:
            set_coach_sel = set_lopend_tc | set_voltooid_tc

        coach_niet_in_schade = set_coach_sel - pnrs_schade_sel
        schade_niet_in_coach = pnrs_schade_sel - set_coach_sel

        def _naam(p):
            nm = (excel_info.get(p, {}) or {}).get("naam")
            if nm and str(nm).strip().lower() not in {"nan","none",""}:
                return str(nm)
            r = df.loc[df["dienstnummer"].astype(str) == str(p), "volledige naam_disp"]
            return r.iloc[0] if not r.empty else str(p)

        def _tc(p):
            tc = (excel_info.get(p, {}) or {}).get("teamcoach")
            if tc and str(tc).strip().lower() not in {"nan","none",""}:
                return str(tc)
            r = df.loc[df["dienstnummer"].astype(str) == str(p), "teamcoach_disp"]
            return r.iloc[0] if not r.empty else "onbekend"

        def _status_volledig(p):
            in_l = p in set_lopend_all
            in_v = p in set_voltooid_all
            if in_l and in_v: return "Beide"
            if in_l: return "Lopend"
            if in_v: return "Voltooid"
            return "Niet aangevraagd"

        def _make_table(pnrs_set):
            if not pnrs_set: 
                return pd.DataFrame(columns=["Dienstnr","Naam","Teamcoach","Status (coachinglijst)"])
            rows = [{
                "Dienstnr": p,
                "Naam": f"{badge_van_chauffeur(f'{p} - {_naam(p)}')}{_naam(p)}",
                "Teamcoach": _tc(p),
                "Status (coachinglijst)": _status_volledig(p)
            } for p in sorted(map(str, pnrs_set))]
            out = pd.DataFrame(rows)
            return out.sort_values(["Teamcoach","Naam"]).reset_index(drop=True)

        with st.expander(f"🟦 In Coachinglijst maar niet in schadelijst ({len(coach_niet_in_schade)})", expanded=False):
            df_a = _make_table(coach_niet_in_schade)
            st.dataframe(df_a, use_container_width=True) if not df_a.empty else st.caption("Geen resultaten.")
            if not df_a.empty:
                st.download_button(
                    "⬇️ Download CSV (coaching ∧ ¬schade)",
                    df_a.to_csv(index=False).encode("utf-8"),
                    file_name="coaching_zonder_schade.csv",
                    mime="text/csv",
                    key="dl_coach_not_schade",
                )

        with st.expander(f"🟥 In schadelijst maar niet in Coachinglijst ({len(schade_niet_in_coach)})", expanded=False):
            df_b = _make_table(schade_niet_in_coach)
            if not df_b.empty:
                df_b["Status (coachinglijst)"] = df_b["Status (coachinglijst)"].replace({"Geen":"Niet aangevraagd"})
            st.dataframe(df_b, use_container_width=True) if not df_b.empty else st.caption("Geen resultaten.")
            if not df_b.empty:
                st.download_button(
                    "⬇️ Download CSV (schade ∧ ¬coaching)",
                    df_b.to_csv(index=False).encode("utf-8"),
                    file_name="schade_zonder_coaching.csv",
                    mime="text/csv",
                    key="dl_schade_not_coach",
                )

        # Extra: >N schades en NIET in coaching (lopend of voltooid)
        st.markdown("---")
        st.markdown("## 🚩 >N schades en niet in *Coaching* of *Voltooid*")
        gebruik_filters_s = st.checkbox(
            "Tel schades binnen huidige filters (uit = volledige dataset)",
            value=False, key="more_schades_use_filters"
        )
        df_basis_s = df_filtered if gebruik_filters_s else df
        thr = st.number_input("Toon bestuurders met méér dan ... schades", min_value=1, value=2, step=1, key="more_schades_threshold")

        pnr_counts = (
            df_basis_s["dienstnummer"].dropna().astype(str).value_counts()
        )
        pnrs_meer_dan = set(pnr_counts[pnr_counts > thr].index)
        set_coaching_all = set_lopend_all | set_voltooid_all
        result_set = pnrs_meer_dan - set_coaching_all

        rows = []
        for p in sorted(result_set, key=lambda x: (-pnr_counts.get(x,0), x)):
            rows.append({
                "Dienstnr": p,
                "Naam": f"{badge_van_chauffeur(f'{p} - {_naam(p)}')}{_naam(p)}",
                "Teamcoach": _tc(p),
                "Schades": int(pnr_counts.get(p,0)),
                "Status (coachinglijst)": "Niet aangevraagd",
            })
        df_no_coach = pd.DataFrame(rows)
        if not df_no_coach.empty:
            df_no_coach = df_no_coach.sort_values(["Schades","Teamcoach","Naam"], ascending=[False,True,True]).reset_index(drop=True)

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
                    key="dl_more_schades_no_coaching",
                )

    except Exception as e:
        st.error("Er ging iets mis in het Coaching-tab.")
        st.exception(e)
