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

    # Kleur op basis van beoordeling
    kleur = _beoordeling_emoji(beoordeling)

    # Lopen coaching?
    lopend = (status_excel == "Coaching") or (sdn in coaching_ids)

    # Combineer: kleur eerst, dan zwart als lopend
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
        kol_rate  = next((k for k in rating_keys if k in dfc.columns), None)  # alleen aanwezig in "Voltooide coachings"

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

            # 1) Probeer Voornaam + Achternaam (waarbij 'naam' als achternaam geldt)
            vn = str(dfc[kol_vn].iloc[i]).strip() if kol_vn else ""
            an = str(dfc[kol_an].iloc[i]).strip() if kol_an else ""

            # 2) Als beide leeg zijn, val terug op één kolom met volledige naam (indien aanwezig)
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

            # Beoordeling meenemen (alleen zinvol/aanwezig bij 'Voltooid')
            if kol_rate and status_label == "Voltooid":
                raw_rate = str(dfc[kol_rate].iloc[i]).strip().lower()
                if raw_rate and raw_rate not in {"nan", "none", ""}:
                    # Normaliseer naar vaste set
                    mapping = {
                        "zeer goed": "zeer goed",
                        "goed": "goed",
                        "voldoende": "voldoende",
                        "slecht": "slecht",
                        "zeer slecht": "zeer slecht",
                        # evt. toleranter maken:
                        "zeergoed": "zeer goed",
                        "zeerslecht": "zeer slecht",
                    }
                    info["beoordeling"] = mapping.get(raw_rate, raw_rate)  # onbekende waarde blijft zichtbaar
            excel_info[pnr] = info

        return ids, total_rows

    s_geel  = vind_sheet(xls, "voltooide coachings")
    s_blauw = vind_sheet(xls, "coaching")

    if s_geel:
        ids_geel,  total_geel_rows  = lees_sheet(s_geel,  "Voltooid")
    if s_blauw:
        ids_blauw, total_blauw_rows = lees_sheet(s_blauw, "Coaching")

    return ids_geel, ids_blauw, total_geel_rows, total_blauw_rows, excel_info, None



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

# ========= Data laden =========
raw = load_excel("schade met macro.xlsm", sheet_name="BRON").copy()
raw.columns = raw.columns.str.strip()

# -- parse datums robuust
raw["Datum"] = _parse_excel_dates(raw["Datum"])

# -- normaliseer relevante kolommen (als string; nog NIET filteren op leeg)
for col in ["volledige naam", "teamcoach", "Locatie", "Bus/ Tram", "Link"]:
    if col in raw.columns:
        raw[col] = raw[col].astype("string").str.strip()

# --- df_for_options: ALLE rijen met geldige datum (voor kwartaal-lijst)
df_for_options = raw[raw["Datum"].notna()].copy()
df_for_options["KwartaalP"] = df_for_options["Datum"].dt.to_period("Q")

# --- df: analyses (alleen datums moeten geldig zijn; lege velden worden 'onbekend')
df = raw[raw["Datum"].notna()].copy()

# Display-kolommen met 'onbekend'
df["volledige naam_disp"] = df["volledige naam"].apply(safe_name)
df["teamcoach_disp"]      = df["teamcoach"].apply(safe_name)
df["Locatie_disp"]        = df["Locatie"].apply(safe_name)
df["BusTram_disp"]        = df["Bus/ Tram"].apply(safe_name)

# Overige afgeleiden
dn = df["volledige naam"].astype(str).str.extract(r"^(\d+)", expand=False)
df["dienstnummer"] = dn.astype("string").str.strip()
df["KwartaalP"]    = df["Datum"].dt.to_period("Q")
df["Kwartaal"]     = df["KwartaalP"].astype(str)

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

# Opties (komen uit display-kolommen zodat 'onbekend' selecteerbaar is)
teamcoach_options = sorted(df["teamcoach_disp"].dropna().unique().tolist())
locatie_options   = sorted(df["Locatie_disp"].dropna().unique().tolist())
voertuig_options  = sorted(df["BusTram_disp"].dropna().unique().tolist())
kwartaal_options  = sorted(df_for_options["KwartaalP"].dropna().astype(str).unique().tolist())

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
        date_from = df["Datum"].min().normalize()
        date_to   = df["Datum"].max().normalize()

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
    df_filtered.to_csv(index=False).encode("utf-8"),
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

# ========= TAB 1: Chauffeur =========
with chauffeur_tab:
    st.subheader("📂 Schadegevallen per chauffeur")
    st.caption("🟢 = goede beoordeling · 🟠 = voldoende · 🔴 = slecht/zeer slecht · ⚫ = lopende coaching")

    chart_series = df_filtered["volledige naam_disp"].value_counts()

    if chart_series.empty:
        st.warning("⚠️ Geen schadegevallen gevonden voor de geselecteerde filters.")
    else:
        # Dataframe voor badges
        plot_df = chart_series.rename_axis("chauffeur").reset_index(name="aantal")
        plot_df["badge"] = plot_df["chauffeur"].apply(badge_van_chauffeur)  # ← geen 'status' meer

        # ========== KPI blok ==========
        totaal_chauffeurs_auto = int(plot_df["chauffeur"].nunique())
        totaal_schades = int(plot_df["aantal"].sum())

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Aantal chauffeurs (met schade)", totaal_chauffeurs_auto)
            handmatig_aantal = st.number_input(
                "Handmatig aantal chauffeurs",
                min_value=1,
                value=max(1, totaal_chauffeurs_auto),
                step=1,
                help="Vul hier het aantal chauffeurs in om het gemiddelde te herberekenen."
            )

        gem_handmatig = round(totaal_schades / handmatig_aantal, 2) if handmatig_aantal else 0.0
        col2.metric("Gemiddeld aantal schades", gem_handmatig)
        col3.metric("Totaal aantal schades", totaal_schades)

        if handmatig_aantal != totaal_chauffeurs_auto:
            st.caption(f"ℹ️ Handmatige invoer actief: {handmatig_aantal} i.p.v. {totaal_chauffeurs_auto}.")

        # ========== Accordeons per interval ==========
        st.subheader("📊 Chauffeurs gegroepeerd per interval")

        step = 5
        max_val = int(plot_df["aantal"].max()) if not plot_df.empty else 0
        if max_val <= 0:
            edges = [0, step]
        else:
            edges = list(range(0, max_val + step, step))
            if edges[-1] < max_val:
                edges.append(edges[-1] + step)

        plot_df["interval"] = pd.cut(
            plot_df["aantal"],
            bins=edges,
            right=True,
            include_lowest=True
        )

        for interval, groep in plot_df.groupby("interval", sort=False):
            if groep.empty or pd.isna(interval):
                continue
            left, right = int(interval.left), int(interval.right)
            low = max(1, left + 1)
            titel = f"{low} t/m {right} schades ({len(groep)} chauffeurs)"

            with st.expander(titel):
                for _, rec in groep.sort_values("aantal", ascending=False).iterrows():
                    chauffeur_label = rec["chauffeur"]
                    aantal = int(rec["aantal"])
                    badge  = rec["badge"]              # ← alleen badge gebruiken

                    subtitel = f"{badge}{chauffeur_label} — {aantal} schadegevallen"
                    with st.expander(subtitel):
                        cols = ["Datum", "BusTram_disp", "Locatie_disp", "teamcoach_disp", "Link"] \
                               if "Link" in df_filtered.columns else \
                               ["Datum", "BusTram_disp", "Locatie_disp", "teamcoach_disp"]
                        schade_chauffeur = (
                            df_filtered.loc[df_filtered["volledige naam_disp"] == chauffeur_label, cols]
                            .sort_values(by="Datum")
                        )
                        for _, row in schade_chauffeur.iterrows():
                            datum_str = row["Datum"].strftime("%d-%m-%Y") if pd.notna(row["Datum"]) else "onbekend"
                            voertuig  = row["BusTram_disp"]
                            loc       = row["Locatie_disp"]
                            coach     = row["teamcoach_disp"]
                            link      = extract_url(row.get("Link")) if "Link" in cols else None
                            prefix = f"📅 {datum_str} — 🚌 {voertuig} — 📍 {loc} — 🧑‍💼 {coach} — "
                            if isinstance(link, str) and link:
                                st.markdown(prefix + f"[🔗 Link]({link})", unsafe_allow_html=True)
                            else:
                                st.markdown(prefix + "❌ Geen geldige link")


# ========= TAB 2: Voertuig =========
with voertuig_tab:
    # --- Deel 1: Lijngrafiek per maand (nu met JAAR-MAAND) ---
    st.subheader("📈 Schadegevallen per maand per voertuigtype")

    df_per_maand = df_filtered.copy()
    if "Datum" in df_per_maand.columns:
        df_per_maand = df_per_maand[df_per_maand["Datum"].notna()].copy()
    else:
        df_per_maand["Datum"] = pd.NaT

    voertuig_col = (
        "BusTram_disp"
        if "BusTram_disp" in df_per_maand.columns
        else ("Bus/ Tram" if "Bus/ Tram" in df_per_maand.columns else None)
    )

    if voertuig_col is None:
        st.warning("⚠️ Kolom voor voertuigtype niet gevonden.")
    elif df_per_maand.empty or not df_per_maand["Datum"].notna().any():
        st.info("ℹ️ Geen geldige datums binnen de huidige filters om een maandoverzicht te tonen.")
    else:
        df_per_maand["JaarMaandP"] = df_per_maand["Datum"].dt.to_period("M")
        df_per_maand["JaarMaand"]  = df_per_maand["JaarMaandP"].astype(str)
        groep = (
            df_per_maand.groupby(["JaarMaand", voertuig_col])
            .size()
            .unstack(fill_value=0)
        )
        start_m = df_per_maand["JaarMaandP"].min()
        eind_m  = df_per_maand["JaarMaandP"].max()
        alle_maanden = pd.period_range(start=start_m, end=eind_m, freq="M").astype(str)
        groep = groep.reindex(alle_maanden, fill_value=0)

        fig2, ax2 = plt.subplots(figsize=(10, 4))
        groep.plot(ax=ax2, marker="o")
        ax2.set_xlabel("Jaar-Maand")
        ax2.set_ylabel("Aantal schadegevallen")
        ax2.set_title("Schadegevallen per maand per voertuigtype (YYYY-MM)")
        ax2.legend(title="Voertuig")
        plt.xticks(rotation=45)
        plt.tight_layout()
        st.pyplot(fig2)

    # --- Deel 2: Aantal schadegevallen per type voertuig ---
    st.subheader("Aantal schadegevallen per type voertuig")

    voertuig_col = "BusTram_disp" if "BusTram_disp" in df_filtered.columns else (
        "Bus/ Tram" if "Bus/ Tram" in df_filtered.columns else None
    )
    if voertuig_col is None:
        st.warning("⚠️ Kolom voor voertuigtype niet gevonden.")
    else:
        chart_data = df_filtered[voertuig_col].value_counts()

        if chart_data.empty:
            st.warning("⚠️ Geen schadegevallen gevonden voor de geselecteerde filters.")
        else:
            fig, ax = plt.subplots(figsize=(8, max(1.5, len(chart_data) * 0.3 + 1)))
            chart_data.sort_values().plot(kind="barh", ax=ax)
            ax.set_xlabel("Aantal schadegevallen")
            ax.set_ylabel("Voertuigtype")
            ax.set_title("Schadegevallen per type voertuig")
            st.pyplot(fig)

            st.subheader("📂 Schadegevallen per voertuigtype")

            for voertuig in chart_data.sort_values(ascending=False).index.tolist():
                kol_list = ["Datum", "volledige naam_disp"]
                if voertuig_col not in kol_list:
                    kol_list.append(voertuig_col)
                if "Link" in df_filtered.columns:
                    kol_list.append("Link")
                if "teamcoach_disp" in df_filtered.columns:
                    kol_list.append("teamcoach_disp")
                if "Locatie_disp" in df_filtered.columns:
                    kol_list.append("Locatie_disp")

                aanwezige_kol = [k for k in kol_list if k in df_filtered.columns]
                schade_per_voertuig = (
                    df_filtered.loc[df_filtered[voertuig_col] == voertuig, aanwezige_kol]
                    .sort_values(by="Datum")
                )
                aantal = len(schade_per_voertuig)

                with st.expander(f"{voertuig} — {aantal} schadegevallen"):
                    if schade_per_voertuig.empty:
                        st.caption("Geen rijen binnen de huidige filters.")
                    else:
                        for _, row in schade_per_voertuig.iterrows():
                            datum_obj = row.get("Datum")
                            datum_str = datum_obj.strftime("%d-%m-%Y") if pd.notna(datum_obj) else "onbekend"
                            chauffeur = row.get("volledige naam_disp", "onbekend")
                            coach     = row.get("teamcoach_disp", "onbekend")
                            locatie   = row.get("Locatie_disp", "onbekend")
                            link = extract_url(row.get("Link")) if "Link" in schade_per_voertuig.columns else None

                            prefix = f"📅 {datum_str} — 👤 {chauffeur} — 🧑‍💼 {coach} — 📍 {locatie} — "
                            if isinstance(link, str) and link:
                                st.markdown(prefix + f"[🔗 Link]({link})", unsafe_allow_html=True)
                            else:
                                st.markdown(prefix + "❌ Geen geldige link")

# ========= TAB 3: Locatie =========
with locatie_tab:
    st.subheader("Aantal schadegevallen per locatie")

    locatie_col = "Locatie_disp" if "Locatie_disp" in df_filtered.columns else None
    if locatie_col is None:
        st.warning("⚠️ Kolom voor locatie niet gevonden.")
    else:
        chart_data = df_filtered[locatie_col].value_counts()

        if chart_data.empty:
            st.warning("⚠️ Geen schadegevallen gevonden voor de geselecteerde filters.")
        else:
            fig, ax = plt.subplots(figsize=(8, max(1.5, len(chart_data) * 0.3 + 1)))
            chart_data.sort_values().plot(kind="barh", ax=ax)
            ax.set_xlabel("Aantal schadegevallen")
            ax.set_ylabel("Locatie")
            ax.set_title("Schadegevallen per locatie")
            st.pyplot(fig)

            st.subheader("📂 Schadegevallen per locatie")

            for locatie in chart_data.sort_values(ascending=False).index.tolist():
                kol_list = ["Datum", "volledige naam_disp", "BusTram_disp", "teamcoach_disp"]
                if "Link" in df_filtered.columns:
                    kol_list.append("Link")
                aanwezige_kol = [k for k in kol_list if k in df_filtered.columns]
                schade_per_locatie = (
                    df_filtered.loc[df_filtered[locatie_col] == locatie, aanwezige_kol]
                    .sort_values(by="Datum")
                )
                aantal = len(schade_per_locatie)

                with st.expander(f"{locatie} — {aantal} schadegevallen"):
                    if schade_per_locatie.empty:
                        st.caption("Geen rijen binnen de huidige filters.")
                    else:
                        for _, row in schade_per_locatie.iterrows():
                            datum_obj = row.get("Datum")
                            datum_str = datum_obj.strftime("%d-%m-%Y") if pd.notna(datum_obj) else "onbekend"
                            chauffeur = row.get("volledige naam_disp", "onbekend")
                            voertuig  = row.get("BusTram_disp", "onbekend")
                            coach     = row.get("teamcoach_disp", "onbekend")
                            link = extract_url(row.get("Link")) if "Link" in schade_per_locatie.columns else None

                            prefix = f"📅 {datum_str} — 👤 {chauffeur} — 🚌 {voertuig} — 🧑‍💼 {coach} — "
                            if isinstance(link, str) and link:
                                st.markdown(prefix + f"[🔗 Link]({link})", unsafe_allow_html=True)
                            else:
                                st.markdown(prefix + "❌ Geen geldige of aanwezige link")

# ========= TAB 4: Opzoeken =========
with opzoeken_tab:
    st.subheader("🔎 Opzoeken op personeelsnummer")

    zoek = st.text_input("Personeelsnummer (dienstnummer)", placeholder="bv. 41092")

    dn_in = re.findall(r"\d+", str(zoek))
    dn_in = dn_in[0] if dn_in else ""

    if not dn_in:
        st.info("Geef een personeelsnummer in om resultaten te zien.")
    else:
        if "dienstnummer" not in df.columns:
            st.error("Kolom 'dienstnummer' ontbreekt in de data.")
        else:
            res = df[df["dienstnummer"].astype(str).str.strip() == dn_in].copy()

            if res.empty:
                st.warning(f"Geen resultaten gevonden voor personeelsnr **{dn_in}**.")
            else:
                naam_chauffeur = res["volledige naam_disp"].iloc[0]
                naam_teamcoach = res["teamcoach_disp"].iloc[0] if "teamcoach_disp" in res.columns else "onbekend"

                st.markdown(f"**👤 Chauffeur:** {naam_chauffeur}")
                st.markdown(f"**🧑‍💼 Teamcoach:** {naam_teamcoach}")
                st.markdown("---")

                st.metric("Aantal schadegevallen", len(res))

                heeft_link = "Link" in res.columns
                res["URL"] = res["Link"].apply(extract_url) if heeft_link else None

                toon_kol = ["Datum", "Locatie_disp"]
                if heeft_link:
                    toon_kol.append("URL")

                res = res.sort_values("Datum", ascending=False)

                if heeft_link:
                    st.dataframe(
                        res[toon_kol],
                        column_config={
                            "Datum": st.column_config.DateColumn("Datum", format="DD-MM-YYYY"),
                            "Locatie_disp": st.column_config.TextColumn("Locatie"),
                            "URL": st.column_config.LinkColumn("Link", display_text="🔗 openen")
                        },
                        use_container_width=True,
                    )
                else:
                    st.dataframe(
                        res[toon_kol],
                        column_config={
                            "Datum": st.column_config.DateColumn("Datum", format="DD-MM-YYYY"),
                            "Locatie_disp": st.column_config.TextColumn("Locatie"),
                        },
                        use_container_width=True,
                    )


# ========= TAB 5: Coaching =========
with coaching_tab:
    st.subheader("🎯 Coachingsoverzicht")

    # ---------- Helpers ----------
    def _norm(x):
        return str(x).strip().casefold() if pd.notna(x) else ""

    # Sidebar-selectie
    selected_norms = {_norm(tc) for tc in selected_teamcoaches}
    gekozen_label = ", ".join(selected_teamcoaches) if selected_teamcoaches else "—"
    all_selected = len(selected_teamcoaches) == len(teamcoach_options)

    # Mapping per P-nr uit SCHADELIJST (matcht sidebar-opties)
    dn_to_info_df = (
        df.groupby("dienstnummer")[["volledige naam_disp", "teamcoach_disp"]]
          .agg(lambda s: s.mode().iat[0] if not s.mode().empty else s.iloc[0])
          .to_dict(orient="index")
    )

    # IDs in schadelijst voor gekozen coach(es)
    ids_schade_sel = set(
        p for p in
        df.loc[df["teamcoach_disp"].apply(lambda s: _norm(s) in selected_norms), "dienstnummer"]
          .dropna().astype(str).str.extract(r"(\d+)", expand=False)
          .dropna().str.strip().unique().tolist()
    ) if not all_selected else set(
        df["dienstnummer"].dropna().astype(str).str.extract(r"(\d+)", expand=False)
          .dropna().str.strip().unique().tolist()
    )

    # Tellingen in schadelijst per status (gefilterd op gekozen coach(es))
    geel_count  = len(gecoachte_ids  & ids_schade_sel)
    zwart_count = len(coaching_ids   & ids_schade_sel)  # lopend

    st.markdown("### ℹ️ Coaching-status (gefilterd op teamcoach-selectie)")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("🟡 Voltooide coachings (in schadelijst)", geel_count)
    with col2:
        st.metric("⚫ Coaching (lopend, in schadelijst)", zwart_count)
    st.caption(f"Gekozen teamcoach(es): {gekozen_label}")

    st.caption("---")

    # ---------- Totale aantallen uit Excel ----------
    st.markdown("### 📊 Totale aantallen uit Coachingslijst.xlsx")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("🟡 Voltooide coachings (Excel-rijen)", totaal_voltooid_rijen)
        st.metric("🟡 Unieke personen (Excel)", len(gecoachte_ids))
    with col2:
        st.metric("⚫ Lopende coachings (Excel-rijen)", totaal_lopend_rijen)
        st.metric("⚫ Unieke personen (Excel)", len(coaching_ids))

    st.caption("---")

    # ---------- Vergelijking schadelijst ↔ Excel ----------
    st.markdown("### 🔍 Vergelijking schadelijst ↔ Excel")
    status_keuze = st.radio(
        "Welke status uit coachingslijst vergelijken?",
        ["Lopend", "Voltooid", "Beide"],
        horizontal=True,
        key="cmp_status"
    )
    if status_keuze == "Lopend":
        bron_ids = coaching_ids
    elif status_keuze == "Voltooid":
        bron_ids = gecoachte_ids
    else:
        bron_ids = coaching_ids | gecoachte_ids

    # IDs in Excel die bij de (sidebar) coach-selectie horen
    def _tc_norm_for_pnr(pnr: str) -> str:
        # 1) uit schadelijst (matcht filteropties), 2) fallback excel
        tc_df = dn_to_info_df.get(pnr, {}).get("teamcoach_disp")
        if _norm(tc_df):
            return _norm(tc_df)
        return _norm(excel_info.get(pnr, {}).get("teamcoach"))

    ids_excel_sel = {
        pnr for pnr in bron_ids
        if all_selected or (_tc_norm_for_pnr(pnr) in selected_norms)
    }

    # Verschillen
    missing_in_schade = sorted(ids_excel_sel - ids_schade_sel)  # wel in Excel, niet in schadelijst
    extra_in_schade   = sorted(ids_schade_sel - ids_excel_sel)  # wel in schadelijst, niet in Excel

    st.caption(f"Vergelijking voor status: {status_keuze} · Teamcoach(es): {gekozen_label}")

    with st.expander(f"🟦 In Coachinglijst maar niet in schadelijst ({len(missing_in_schade)})", expanded=False):
        if not missing_in_schade:
            st.write("—")
        else:
            for dn in missing_in_schade:
                ex = excel_info.get(dn, {})
                naam_excel  = ex.get("naam", "onbekend")
                coach_excel = ex.get("teamcoach", "onbekend")
                beoordeling = ex.get("beoordeling", "—") if ex.get("status") == "Voltooid" else "—"
                st.write(f"• {dn} — {naam_excel} (teamcoach: {coach_excel}, beoordeling: {beoordeling})")

    with st.expander(f"🟥 In schadelijst maar niet in Coachinglijst ({len(extra_in_schade)})", expanded=False):
        if not extra_in_schade:
            st.write("—")
        else:
            for dn in extra_in_schade:
                dfinfo = dn_to_info_df.get(dn, {})
                naam  = dfinfo.get("volledige naam_disp", "onbekend")
                coach = dfinfo.get("teamcoach_disp", "onbekend")
                st.write(f"• {dn} — {naam} (teamcoach: {coach}, beoordeling: —)")

    st.caption("---")

    # ---------- Beoordelingen-overzicht ----------
    st.markdown("### ⭐ Beoordelingen van voltooide coachings (gefilterd op teamcoach-selectie)")

    beoordelingen_sel = []
    for pnr, ex in excel_info.items():
        if ex.get("status") != "Voltooid":
            continue

        tc_norm = _tc_norm_for_pnr(pnr)
        if not all_selected and (tc_norm not in selected_norms):
            continue

        rate = str(ex.get("beoordeling", "")).strip().lower()
        if rate:
            beoordelingen_sel.append(rate)

    if len(beoordelingen_sel) == 0:
        st.info("Geen beoordelingen gevonden voor de huidige selectie.")
    else:
        # vaste volgorde
        volgorde = ["zeer goed", "goed", "voldoende", "slecht", "zeer slecht"]
        vc = pd.Series(beoordelingen_sel).value_counts()
        series = pd.Series({k: int(vc.get(k, 0)) for k in volgorde})

        col_a, col_b = st.columns([2, 1])
        with col_a:
            st.bar_chart(series)
        with col_b:
            st.metric("Totaal beoordeelde coachings", int(series.sum()))
            st.metric(
                "⌀ Score-indicatie (1–5)",
                round(
                    (
                        series.get("zeer goed", 0) * 5 +
                        series.get("goed", 0) * 4 +
                        series.get("voldoende", 0) * 3 +
                        series.get("slecht", 0) * 2 +
                        series.get("zeer slecht", 0) * 1
                    ) / max(series.sum(), 1),
                    2
                )
            )

        st.dataframe(
            series.rename("aantal").reset_index().rename(columns={"index": "beoordeling"}),
            use_container_width=True
        )
