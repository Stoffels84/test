# app.py
# ============================================================
# OT GENT - Overzicht & rapportering (Streamlit)
# Data komt uit: schade met macro.xlsm (tab: BRON + data hastus)
# Extra bestanden:
# - Coachingslijst.xlsx (tabs: Voltooide coachings + Coaching)
# - Overzicht gesprekken (aangepast).xlsx (1e tab)
#
# Sidebar links:
# - Dashboard
# - Schade: Chauffeur, Voertuig, Locatie, Coaching, Analyse
#
# Dashboard:
# - Zoeken op personeelsnr/naam/voertuig
# - Tabel met klikbare Link (=> naar EAF)
# - Coachings datums voor gevonden P-nr
# - Gesprekken voor die chauffeur (met rommelkolommen gefilterd)
#
# Wijzigingen in deze versie:
# - Jaarfilter is MULTISELECT (meerdere jaren tegelijk)
# - Chauffeurs/teamcoach "uit dienst" tellen NIET mee in de grafiek "Schades per teamcoach"
#   (maar blijven overal elders WEL meetellen)
# - Refresh-knop (cache clear) in sidebar
# - Zoekveld als form (Enter + knop)
# - Gesprekken: sorteren op datum (nieuwste bovenaan)
# - Gesprekken tabel: wrap + kolombreedtes (geen dubbelklik)
# ============================================================

from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st


# ============================================================
# CONFIG
# ============================================================
st.set_page_config(page_title="OT GENT - Overzicht & rapportering", layout="wide")

BASE_DIR = Path(__file__).parent
FILE_SCHADE = BASE_DIR / "schade met macro.xlsm"
FILE_COACHING = BASE_DIR / "Coachingslijst.xlsx"
FILE_GESPREKKEN = BASE_DIR / "Overzicht gesprekken (aangepast).xlsx"

SHEET_BRON = "BRON"
SHEET_HASTUS = "data hastus"
SHEET_COACH_DONE = "Voltooide coachings"
SHEET_COACH_PENDING = "Coaching"


# ============================================================
# HELPERS
# ============================================================
def norm(x) -> str:
    return str(x).strip().lower() if x is not None else ""


def find_col(df: pd.DataFrame, possible_names: list[str]) -> str | None:
    cols_norm = {norm(c): c for c in df.columns}
    for name in possible_names:
        key = norm(name)
        if key in cols_norm:
            return cols_norm[key]
    return None


def to_datetime_utc_series(s: pd.Series) -> pd.Series:
    def parse_one(x):
        if pd.isna(x):
            return pd.NaT
        if isinstance(x, (datetime, pd.Timestamp)):
            return pd.to_datetime(x, utc=True, errors="coerce")
        if isinstance(x, (int, float)) and not pd.isna(x):
            # Excel serial date
            return pd.to_datetime("1899-12-30", utc=True) + pd.to_timedelta(float(x), unit="D")
        try:
            return pd.to_datetime(str(x), utc=True, errors="coerce", dayfirst=True)
        except Exception:
            return pd.NaT

    return s.apply(parse_one)


def safe_read_excel(path: Path, sheet_name=None) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Bestand niet gevonden: {path.name}")
    return pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")


def clean_url(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and np.isnan(v):
        return ""
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return ""
    return s


def looks_like_pnr(term: str) -> bool:
    return bool(re.fullmatch(r"\d{4,}", term.strip()))


def pnr_to_clean_string(v) -> str:
    """Fix Excel floats zoals 41520.0 -> 41520"""
    if v is None:
        return ""
    if isinstance(v, float) and np.isnan(v):
        return ""
    try:
        f = float(v)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass
    s = str(v).strip()
    if re.fullmatch(r"\d+\.0", s):
        return s.split(".")[0]
    return s


def gesprekken_keep_columns(df: pd.DataFrame) -> list[str]:
    """
    Toon alleen echte gesprek-kolommen.
    Prefer: Nummer/Chauffeurnaam/Datum/Onderwerp/Info (als aanwezig).
    Anders: alles behalve Unnamed/Maand/Jaar/Aantal/in dienst + lege kolommen.
    """
    preferred = ["Nummer", "Chauffeurnaam", "Datum", "Onderwerp", "Info"]
    existing = {str(c).strip(): c for c in df.columns}
    pref_real = [existing[p] for p in preferred if p in existing]
    if pref_real:
        return pref_real

    drop_patterns = [r"^unnamed:", r"^maand$", r"^jaar$", r"^aantal$", r"^in dienst$"]
    keep = []
    for c in df.columns:
        cn = norm(c)
        if any(re.match(p, cn) for p in drop_patterns):
            continue
        if df[c].isna().all():
            continue
        keep.append(c)
    return keep if keep else list(df.columns)


def coaching_status_from_text(text) -> str | None:
    if text is None or str(text).strip() == "":
        return None
    t = str(text).strip().lower()
    if "slecht" in t:
        return "bad"
    if "onvoldoende" in t or "voldoende" in t:
        return "medium"
    if "zeer goed" in t or t == "goed" or " goed" in t:
        return "good"
    return None


def is_uit_dienst_value(v) -> bool:
    t = norm(v)
    return t in {"uit dienst", "uitdienst", "out of service"} or "uit dienst" in t


def inject_css():
    st.markdown(
        """
        <style>
        .wrap-table table { width: 100%; border-collapse: collapse; table-layout: fixed; }
        .wrap-table th, .wrap-table td {
            border-bottom: 1px solid rgba(255,255,255,0.10);
            padding: 8px 10px;
            vertical-align: top;
            white-space: pre-wrap;
            word-break: break-word;
            text-align: left;
        }
        .wrap-table th { font-weight: 600; }

        /* Kolombreedtes gesprekken (volgorde = GESPREK_COLS) */
        .wrap-table th:nth-child(1), .wrap-table td:nth-child(1) { width: 180px; }
        .wrap-table th:nth-child(2), .wrap-table td:nth-child(2) { width: 90px; white-space: nowrap; }
        .wrap-table th:nth-child(3), .wrap-table td:nth-child(3) { width: 160px; }
        .wrap-table th:nth-child(4), .wrap-table td:nth-child(4) { width: auto; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_wrap_table(df: pd.DataFrame):
    html = df.to_html(index=False, escape=True)
    st.markdown(f'<div class="wrap-table">{html}</div>', unsafe_allow_html=True)


inject_css()


# ============================================================
# FILE CHECK
# ============================================================
missing = [p.name for p in [FILE_SCHADE, FILE_COACHING, FILE_GESPREKKEN] if not p.exists()]
if missing:
    st.error("Ik mis deze bestanden in dezelfde map als app.py:\n\n- " + "\n- ".join(missing))
    st.stop()


# ============================================================
# LOAD DATA (cached)
# ============================================================
@st.cache_data(show_spinner=True)
def load_schade() -> tuple[pd.DataFrame, pd.DataFrame]:
    df_bron = safe_read_excel(FILE_SCHADE, sheet_name=SHEET_BRON)
    df_bron.columns = [str(c).strip() for c in df_bron.columns]

    df_hastus = pd.DataFrame()
    try:
        df_hastus = safe_read_excel(FILE_SCHADE, sheet_name=SHEET_HASTUS)
        df_hastus.columns = [str(c).strip() for c in df_hastus.columns]
    except Exception:
        df_hastus = pd.DataFrame()

    return df_bron, df_hastus


@st.cache_data(show_spinner=True)
def load_coaching() -> tuple[pd.DataFrame, set[str], int, int]:
    done_df = pd.DataFrame()
    pending_set: set[str] = set()
    done_raw = 0
    pending_raw = 0

    xls = pd.ExcelFile(FILE_COACHING, engine="openpyxl")

    if SHEET_COACH_DONE in xls.sheet_names:
        done_df = pd.read_excel(xls, sheet_name=SHEET_COACH_DONE)
        done_df.columns = [str(c).strip() for c in done_df.columns]
        done_raw = len(done_df)

    if SHEET_COACH_PENDING in xls.sheet_names:
        pending_sheet = pd.read_excel(xls, sheet_name=SHEET_COACH_PENDING, header=None)
        if pending_sheet.shape[1] >= 4:
            col = pending_sheet.iloc[1:, 3]  # kolom D
            for v in col.dropna().astype(str).map(str.strip):
                if v:
                    pending_raw += 1
                    pending_set.add(v)

    return done_df, pending_set, done_raw, pending_raw


@st.cache_data(show_spinner=True)
def load_gesprekken() -> pd.DataFrame:
    df = safe_read_excel(FILE_GESPREKKEN, sheet_name=0)
    df.columns = [str(c).strip() for c in df.columns]
    return df


df_bron, df_hastus = load_schade()
df_coach_done, coaching_pending_set, done_raw_count, pending_raw_count = load_coaching()
df_gesprekken = load_gesprekken()

# ============================================================
# MAP COLUMNS (BRON)
# ============================================================
col_datum = find_col(df_bron, ["datum"])
col_naam = find_col(df_bron, ["volledige naam", "chauffeur", "naam", "bestuurder"])
col_voertuigtype = find_col(df_bron, ["bus/tram", "bus/ tram", "voertuigtype", "type voertuig"])
col_voertuignr = find_col(df_bron, ["voertuig", "voertuignummer", "voertuig nr", "busnummer", "tramnummer", "voertuignr"])
col_type = find_col(df_bron, ["type"])
col_locatie = find_col(df_bron, ["locatie"])
col_link = find_col(df_bron, ["link"])
col_pnr = find_col(df_bron, ["personeelsnr", "personeelsnummer", "personeels nr", "p-nr", "p nr"])
col_teamcoach = find_col(df_bron, ["teamcoach"])

if col_datum is None:
    st.error("Kolom 'datum' niet gevonden in tab BRON.")
    st.stop()

df_bron = df_bron.copy()
df_bron["_datum_dt"] = to_datetime_utc_series(df_bron[col_datum])
df_bron["_jaar"] = df_bron["_datum_dt"].dt.year


# ============================================================
# Gesprekken kolommen (vaste volgorde als mogelijk)
# ============================================================
gesp_chauffeur = find_col(df_gesprekken, ["Chauffeurnaam", "chauffeurnaam", "volledige naam", "naam"])
gesp_datum = find_col(df_gesprekken, ["Datum", "datum"])
gesp_onderwerp = find_col(df_gesprekken, ["Onderwerp", "onderwerp"])
gesp_info = find_col(df_gesprekken, ["Info", "info"])

if all([gesp_chauffeur, gesp_datum, gesp_onderwerp, gesp_info]):
    GESPREK_COLS = [gesp_chauffeur, gesp_datum, gesp_onderwerp, gesp_info]
else:
    GESPREK_COLS = gesprekken_keep_columns(df_gesprekken)


# ============================================================
# SIDEBAR NAVIGATIE (links)
# ============================================================
DEFAULT_PAGE = "dashboard"
if "page" not in st.session_state:
    st.session_state.page = DEFAULT_PAGE


def go(page_key: str):
    st.session_state.page = page_key


st.sidebar.markdown("## OT GENT")
st.sidebar.caption("Overzicht & rapportering")
st.sidebar.divider()

# Refresh data knop (cache clear + rerun)
if st.sidebar.button("🔄 Data herladen", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

if st.sidebar.button(
    "Dashboard",
    use_container_width=True,
    type="primary" if st.session_state.page == "dashboard" else "secondary",
):
    go("dashboard")

st.sidebar.markdown("")
st.sidebar.markdown("**Schade**")
if st.sidebar.button("Chauffeur", use_container_width=True, type="primary" if st.session_state.page == "chauffeur" else "secondary"):
    go("chauffeur")
if st.sidebar.button("Voertuig", use_container_width=True, type="primary" if st.session_state.page == "voertuig" else "secondary"):
    go("voertuig")
if st.sidebar.button("Locatie", use_container_width=True, type="primary" if st.session_state.page == "locatie" else "secondary"):
    go("locatie")
if st.sidebar.button("Coaching", use_container_width=True, type="primary" if st.session_state.page == "coaching" else "secondary"):
    go("coaching")
if st.sidebar.button("Analyse", use_container_width=True, type="primary" if st.session_state.page == "analyse" else "secondary"):
    go("analyse")

st.sidebar.divider()

# ============================================================
# SIDEBAR FILTER: JAAR (MULTISELECT)
# ============================================================
st.sidebar.markdown("### Filter")
years = sorted([int(y) for y in df_bron["_jaar"].dropna().unique()])

# Default = alles geselecteerd (multiselect vereist list)
years_choice = st.sidebar.multiselect("Jaar", options=years, default=years)


def apply_year_filter(df: pd.DataFrame) -> pd.DataFrame:
    if not years_choice:
        return df  # als user alles uitvinkt: gedraag alsof "alles"
    return df[df["_jaar"].isin([int(y) for y in years_choice])]


df_filtered = apply_year_filter(df_bron)

# ============================================================
# COACHING MAP (pnr -> list dates/status)
# ============================================================
coaching_map: dict[str, list[dict]] = {}

if not df_coach_done.empty:
    col_done_pnr = find_col(df_coach_done, ["P-nr", "pnr", "personeelsnr", "personeelsnummer", "p nr"])
    col_done_rating = find_col(df_coach_done, ["Beoordeling coaching"])
    col_done_date = find_col(df_coach_done, ["datum", "datum coaching"])

    if col_done_pnr and col_done_rating:
        tmp = df_coach_done.copy()
        tmp["_coach_dt"] = to_datetime_utc_series(tmp[col_done_date]) if col_done_date else pd.NaT

        for _, r in tmp.iterrows():
            p = r.get(col_done_pnr, None)
            if pd.isna(p):
                continue
            key = pnr_to_clean_string(p)
            status = coaching_status_from_text(r.get(col_done_rating, None))
            if not status:
                continue

            dt = r.get("_coach_dt", pd.NaT)
            date_str = ""
            if pd.notna(dt):
                date_str = pd.to_datetime(dt).strftime("%d/%m/%Y")

            coaching_map.setdefault(key, []).append(
                {"status": status, "date": dt if pd.notna(dt) else None, "dateString": date_str}
            )


def sidebar_status():
    coach_count = len(coaching_map.keys())
    if years_choice and len(years_choice) != len(years):
        filter_text = "jaren " + ", ".join(map(str, sorted(years_choice)))
    else:
        filter_text = "alle jaren"
    st.sidebar.caption(f"Klaar. {len(df_filtered)} rijen ({filter_text}). Coachings voor {coach_count} P-nrs geladen.")


# ============================================================
# PAGES
# ============================================================
def page_dashboard():
    st.header("Dashboard – Chauffeur opzoeken")
    st.write("Zoek op **personeelsnummer**, **naam** of **voertuig**. Resultaten respecteren de jaarfilter.")

    # Zoekveld in form => Enter + knop werken identiek
    with st.form("search_form", clear_on_submit=False):
        c1, c2 = st.columns([3, 1])
        term = c1.text_input("Zoek", placeholder="Personeelsnr, naam of voertuignummer...", label_visibility="collapsed")
        submitted = c2.form_submit_button("Zoeken", use_container_width=True)

    if not submitted and not term.strip():
        st.info("Tip: je kunt een deel van de naam, het nummer of het voertuignummer ingeven.")
        return

    t = term.strip().lower()

    def contains(colname):
        if not colname:
            return pd.Series([False] * len(df_filtered), index=df_filtered.index)
        return df_filtered[colname].astype(str).str.lower().str.contains(re.escape(t), na=False)

    mask = pd.Series(False, index=df_filtered.index)
    if col_naam:
        mask |= contains(col_naam)
    if col_pnr:
        mask |= contains(col_pnr)
    if col_voertuignr:
        mask |= contains(col_voertuignr)
    elif col_voertuigtype:
        mask |= contains(col_voertuigtype)

    results = df_filtered[mask].sort_values("_datum_dt", ascending=False)

    if results.empty:
        st.warning("Geen resultaten gevonden (binnen de gekozen jaarfilter).")
        return

    # context chauffeur
    selected_pnr = ""
    selected_name = ""
    if col_pnr and looks_like_pnr(term):
        selected_pnr = pnr_to_clean_string(term)
        sub_p = results[results[col_pnr].apply(pnr_to_clean_string) == selected_pnr]
        if not sub_p.empty and col_naam:
            selected_name = str(sub_p.iloc[0][col_naam]).strip()
        elif col_naam:
            selected_name = str(results.iloc[0][col_naam]).strip()
    else:
        if col_pnr:
            selected_pnr = pnr_to_clean_string(results.iloc[0][col_pnr])
        if col_naam:
            selected_name = str(results.iloc[0][col_naam]).strip()

    if selected_name or selected_pnr:
        st.info(f"📌 Geselecteerd: **{selected_name or 'Onbekend'}** — P-nr **{selected_pnr or 'Onbekend'}**")

    # coachings
    if selected_pnr:
        entries = coaching_map.get(selected_pnr, [])
        if entries:
            entries_sorted = sorted(entries, key=lambda e: (e["date"] is None, e["date"]))
            title = f"Coachings voor **{selected_pnr}**"
            if selected_name:
                title += f" — {selected_name}"
            st.markdown(f"#### {title}")
            dates = [e["dateString"] for e in entries_sorted if e.get("dateString")]
            if dates:
                st.write(" ".join([f"`{d}`" for d in dates]))
        else:
            st.caption(f"Geen coachings gevonden voor P-nr {selected_pnr}.")

    # schade tabel
    out = pd.DataFrame()
    out["Datum"] = results["_datum_dt"].dt.strftime("%d/%m/%Y")
    out["Chauffeur"] = results[col_naam] if col_naam else ""
    out["Personeelsnr"] = results[col_pnr].apply(pnr_to_clean_string) if col_pnr else ""
    out["Voertuignr"] = results[col_voertuignr] if col_voertuignr else ""
    out["Voertuigtype"] = results[col_voertuigtype] if col_voertuigtype else ""
    out["Type"] = results[col_type] if col_type else ""
    out["Locatie"] = results[col_locatie] if col_locatie else ""
    out["Link"] = results[col_link].map(clean_url) if col_link else ""

    st.dataframe(
        out,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("Link", display_text="=> naar EAF", validate="^https?://.*")
        },
    )

    # gesprekken onderaan (blijft in dashboard)
    st.markdown("### Gesprekken")
    st.caption("Alle tekst staat meteen volledig open (geen doorklikken).")

    if df_gesprekken.empty:
        st.info("Gesprekkenbestand is leeg.")
        return

    gesprek_nummer_col = find_col(df_gesprekken, ["nummer", "personeelsnr", "personeelsnummer", "p-nr", "p nr"])
    gesprek_naam_col = find_col(df_gesprekken, ["chauffeurnaam", "volledige naam", "naam"])
    gesprek_datum_col = find_col(df_gesprekken, ["datum"])

    df_g = df_gesprekken.copy()

    # jaarfilter gesprekken (multiselect)
    if gesprek_datum_col:
        df_g["_dt"] = to_datetime_utc_series(df_g[gesprek_datum_col])
        df_g["_jaar"] = df_g["_dt"].dt.year
        if years_choice:
            df_g = df_g[df_g["_jaar"].isin([int(y) for y in years_choice])]

    gmask = pd.Series(False, index=df_g.index)

    if selected_pnr and gesprek_nummer_col:
        gmask |= df_g[gesprek_nummer_col].apply(pnr_to_clean_string).astype(str).str.strip() == selected_pnr

    if (not gmask.any()) and selected_name and gesprek_naam_col:
        nm = selected_name.strip().lower()
        gmask |= df_g[gesprek_naam_col].astype(str).str.lower().str.contains(re.escape(nm), na=False)

    df_g_match = df_g[gmask].copy()

    if df_g_match.empty:
        st.info("Geen gesprekken gevonden (binnen de gekozen jaarfilter).")
        return

    # sorteer newest first
    if gesprek_datum_col and gesprek_datum_col in df_g_match.columns:
        df_g_match["_dt_sort"] = to_datetime_utc_series(df_g_match[gesprek_datum_col])
        df_g_match = df_g_match.sort_values("_dt_sort", ascending=False)
        df_g_match[gesprek_datum_col] = df_g_match["_dt_sort"].dt.strftime("%d/%m/%Y")

    render_wrap_table(df_g_match[GESPREK_COLS])


def page_chauffeur():
    st.header("Data rond chauffeur")
    st.write("Overzicht van aantal schades per chauffeur (gefilterd op gekozen jaren).")

    if not col_naam:
        st.warning("Geen kolom 'volledige naam / chauffeur / naam' gevonden in BRON.")
        return

    tc_options = ["Alle teamcoaches"]
    if col_teamcoach:
        vals = df_filtered[col_teamcoach].dropna().astype(str).str.strip()
        tc_options += sorted([v for v in vals.unique() if v])

    c1, c2 = st.columns([2, 1])
    tc_choice = c1.selectbox("Teamcoach", tc_options)
    lim_choice = c2.selectbox("Toon", ["Top 10", "Top 20", "Alle chauffeurs"], index=0)
    lim = 10 if lim_choice == "Top 10" else 20 if lim_choice == "Top 20" else None

    df_ch = df_filtered.copy()
    if col_teamcoach and tc_choice != "Alle teamcoaches":
        df_ch = df_ch[df_ch[col_teamcoach].astype(str).str.strip() == tc_choice]

    temp = df_ch.copy()
    temp["_chauffeur"] = temp[col_naam].fillna("Onbekend").astype(str).str.strip()
    table = temp.groupby("_chauffeur").size().reset_index(name="Aantal").sort_values("Aantal", ascending=False)
    table_view = table.head(lim) if lim else table

    st.dataframe(table_view.rename(columns={"_chauffeur": "Chauffeur"}), use_container_width=True, hide_index=True)

    st.subheader("Schades per teamcoach")
    st.caption("Gebaseerd op de huidige jaarfilter en eventueel geselecteerde teamcoach. ('uit dienst' telt niet mee.)")

    if not col_teamcoach:
        st.info("Kolom 'teamcoach' niet gevonden in BRON.")
        return

    # BELANGRIJK: alleen voor deze grafiek "uit dienst" uitsluiten
    bar = df_ch.copy()
    bar["_tc_raw"] = bar[col_teamcoach].fillna("Onbekend").astype(str).str.strip()
    bar = bar[~bar["_tc_raw"].map(is_uit_dienst_value)].copy()  # alleen hier filteren
    bar["_tc"] = bar["_tc_raw"].replace("", "Onbekend")

    if bar.empty:
        st.info("Geen data voor grafiek (na uitsluiten 'uit dienst').")
        return

    bar_df = bar.groupby("_tc").size().reset_index(name="Aantal").sort_values("Aantal", ascending=False)

    fig = px.bar(bar_df, x="_tc", y="Aantal")
    fig.update_layout(xaxis_title="Teamcoach", yaxis_title="Aantal schades", showlegend=False)
    st.plotly_chart(fig, use_container_width=True)


def page_voertuig():
    st.header("Data rond voertuig (Bus/Tram)")
    st.write(
        "Overzicht van aantal schades per type voertuig op basis van kolom **Bus/Tram** (of gelijkaardig). "
        "Respecteert de jaarfilter."
    )

    if not col_voertuigtype:
        st.warning("Geen kolom voertuigtype (Bus/Tram/...) gevonden in BRON.")
        return

    lim_choice = st.selectbox("Toon", ["Top 10", "Top 20", "Alle types"], index=0)
    lim = 10 if lim_choice == "Top 10" else 20 if lim_choice == "Top 20" else None

    temp = df_filtered.copy()
    temp["_veh"] = temp[col_voertuigtype].fillna("Onbekend").astype(str).str.strip()

    table = temp.groupby("_veh").size().reset_index(name="Aantal").sort_values("Aantal", ascending=False)
    table_view = table.head(lim) if lim else table

    st.dataframe(table_view.rename(columns={"_veh": "Type voertuig"}), use_container_width=True, hide_index=True)

    if "_datum_dt" not in temp.columns:
        st.info("Geen datumkolom verwerkt.")
        return

    dm = temp[temp["_datum_dt"].notna()].copy()
    if dm.empty:
        st.info("Geen datums gevonden om per maand te groeperen.")
        return

    dm["_maand"] = dm["_datum_dt"].dt.month
    month_names = ["Jan", "Feb", "Mrt", "Apr", "Mei", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]
    dm["_m_name"] = dm["_maand"].apply(lambda m: month_names[m - 1])
    dm["_veh"] = dm["_veh"].replace("", "Onbekend")

    pivot = dm.groupby(["_maand", "_m_name", "_veh"]).size().reset_index(name="Aantal")

    vehicles = sorted(pivot["_veh"].unique().tolist())
    full_index = pd.MultiIndex.from_product([range(1, 13), vehicles], names=["_maand", "_veh"])
    filled = pivot.set_index(["_maand", "_veh"]).reindex(full_index, fill_value=0).reset_index()
    filled["_m_name"] = filled["_maand"].apply(lambda m: month_names[m - 1])

    st.subheader("Schades per maand en voertuigtype (gestapelde balken)")
    st.caption("X-as = maanden, kleur = voertuigtype. Respecteert de gekozen jaarfilter.")

    fig_bar = px.bar(
        filled,
        x="_m_name",
        y="Aantal",
        color="_veh",
        barmode="stack",
        category_orders={"_m_name": month_names},
    )
    fig_bar.update_layout(xaxis_title="Maand", yaxis_title="Aantal schades")
    st.plotly_chart(fig_bar, use_container_width=True)

    st.subheader("Tendens per voertuigtype (lijngrafiek)")
    st.caption("Zelfde data als de staafgrafiek hierboven, maar als lijngrafiek per voertuigtype.")

    fig_line = px.line(
        filled,
        x="_m_name",
        y="Aantal",
        color="_veh",
        markers=True,
        category_orders={"_m_name_
