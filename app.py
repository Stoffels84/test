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
# - Alle info teamcoach: Gesprekken
#
# Dashboard:
# - Zoeken op personeelsnr/naam/voertuig
# - Tabel met klikbare Link (=> naar EAF)
# - Coachings datums voor gevonden P-nr
# - Gesprekken voor die chauffeur (met rommelkolommen gefilterd)
#
# Chauffeur:
# - Teamcoach filter + Top 10/20/Alles
# - Tabel Chauffeur/Aantal
# - Bar chart: schades per teamcoach
#
# Voertuig:
# - Tabel type voertuig + aantallen
# - Gestapelde balk per maand + voertuigtype
# - Lijngrafiek tendens per voertuigtype
#
# Run:
#   pip install -r requirements.txt
#   streamlit run app.py
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


def gesprekken_column_config(cols: list[str]) -> dict:
    """
    Column config voor gesprekken zodat tekst (zeker Info) mooi wrapt
    met st.data_editor (read-only via disabled=True).
    """
    cfg: dict = {}
    for c in cols:
        if norm(c) == "info":
            cfg[c] = st.column_config.TextColumn(c, width="large")
        elif norm(c) == "onderwerp":
            cfg[c] = st.column_config.TextColumn(c, width="medium")
        elif norm(c) == "chauffeurnaam":
            cfg[c] = st.column_config.TextColumn(c, width="medium")
        else:
            # default: laat Streamlit beslissen
            pass
    return cfg


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
GESPREK_COLS = gesprekken_keep_columns(df_gesprekken)

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

if st.sidebar.button("Dashboard", use_container_width=True, type="primary" if st.session_state.page == "dashboard" else "secondary"):
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

st.sidebar.markdown("")
st.sidebar.markdown("**Alle info teamcoach**")
if st.sidebar.button("Gesprekken", use_container_width=True, type="primary" if st.session_state.page == "gesprekken" else "secondary"):
    go("gesprekken")

st.sidebar.divider()

# ============================================================
# SIDEBAR FILTER: JAAR
# ============================================================
st.sidebar.markdown("### Filter")
years = sorted([int(y) for y in df_bron["_jaar"].dropna().unique()])
year_choice = st.sidebar.selectbox("Jaar", options=["ALL"] + years, index=0)


def apply_year_filter(df: pd.DataFrame) -> pd.DataFrame:
    if year_choice == "ALL":
        return df
    return df[df["_jaar"] == int(year_choice)]


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

            coaching_map.setdefault(key, []).append({"status": status, "date": dt if pd.notna(dt) else None, "dateString": date_str})


def sidebar_status():
    coach_count = len(coaching_map.keys())
    filter_text = "alle jaren" if year_choice == "ALL" else f"jaar {year_choice}"
    st.sidebar.caption(f"Klaar. {len(df_filtered)} rijen ({filter_text}). Coachings voor {coach_count} P-nrs geladen.")


# ============================================================
# PAGES
# ============================================================
def page_dashboard():
    st.header("Dashboard – Chauffeur opzoeken")
    st.write("Zoek op **personeelsnummer**, **naam** of **voertuig**. Resultaten respecteren de jaarfilter.")

    c1, c2 = st.columns([3, 1])
    term = c1.text_input("Zoek", placeholder="Personeelsnr, naam of voertuignummer...", label_visibility="collapsed")
    c2.button("Zoeken", use_container_width=True)

    if not term.strip():
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

    # gesprekken onderaan
    st.markdown("### Gesprekken")
    st.caption("ℹ️ Lange teksten worden automatisch gewrapt. (Indien nodig kan je in een cel scrollen.)")

    if df_gesprekken.empty:
        st.info("Gesprekkenbestand is leeg.")
        return

    gesprek_nummer_col = find_col(df_gesprekken, ["nummer", "personeelsnr", "personeelsnummer", "p-nr", "p nr"])
    gesprek_naam_col = find_col(df_gesprekken, ["chauffeurnaam", "volledige naam", "naam"])
    gesprek_datum_col = find_col(df_gesprekken, ["datum"])

    df_g = df_gesprekken.copy()

    # jaarfilter gesprekken
    if gesprek_datum_col:
        df_g["_dt"] = to_datetime_utc_series(df_g[gesprek_datum_col])
        df_g["_jaar"] = df_g["_dt"].dt.year
        if year_choice != "ALL":
            df_g = df_g[df_g["_jaar"] == int(year_choice)]

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

    if gesprek_datum_col and gesprek_datum_col in df_g_match.columns:
        df_g_match[gesprek_datum_col] = to_datetime_utc_series(df_g_match[gesprek_datum_col]).dt.strftime("%d/%m/%Y")

    # >>> Hier: data_editor i.p.v. dataframe (wrapping + hogere rijen)
    st.data_editor(
        df_g_match[GESPREK_COLS],
        use_container_width=True,
        hide_index=True,
        disabled=True,
        column_config=gesprekken_column_config(GESPREK_COLS),
    )


def page_chauffeur():
    st.header("Data rond chauffeur")
    st.write("Overzicht van aantal schades per chauffeur (gefilterd op gekozen jaar).")

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
    st.caption("Gebaseerd op de huidige jaarfilter en eventueel geselecteerde teamcoach.")

    if not col_teamcoach:
        st.info("Kolom 'teamcoach' niet gevonden in BRON.")
        return

    bar = df_ch.copy()
    bar["_tc"] = bar[col_teamcoach].fillna("Onbekend").astype(str).str.strip()
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

    # ---- TOP TABLE ----
    lim_choice = st.selectbox("Toon", ["Top 10", "Top 20", "Alle types"], index=0)
    lim = 10 if lim_choice == "Top 10" else 20 if lim_choice == "Top 20" else None

    temp = df_filtered.copy()
    temp["_veh"] = temp[col_voertuigtype].fillna("Onbekend").astype(str).str.strip()

    table = temp.groupby("_veh").size().reset_index(name="Aantal").sort_values("Aantal", ascending=False)
    table_view = table.head(lim) if lim else table

    st.dataframe(table_view.rename(columns={"_veh": "Type voertuig"}), use_container_width=True, hide_index=True)

    # ---- MONTH DATA (basis voor beide grafieken) ----
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

    # Zorg dat alle maanden zichtbaar blijven (ook met 0)
    vehicles = sorted(pivot["_veh"].unique().tolist())
    full_index = pd.MultiIndex.from_product([range(1, 13), vehicles], names=["_maand", "_veh"])
    filled = (
        pivot.set_index(["_maand", "_veh"])
        .reindex(full_index, fill_value=0)
        .reset_index()
    )
    filled["_m_name"] = filled["_maand"].apply(lambda m: month_names[m - 1])

    # ---- 1) STACKED BAR ----
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

    # ---- 2) LINE TREND ----
    st.subheader("Tendens per voertuigtype (lijngrafiek)")
    st.caption(
        "Zelfde data als de staafgrafiek hierboven, maar als lijngrafiek per voertuigtype. "
        "Handig om de evolutie doorheen het jaar te zien."
    )

    fig_line = px.line(
        filled,
        x="_m_name",
        y="Aantal",
        color="_veh",
        markers=True,
        category_orders={"_m_name": month_names},
    )
    fig_line.update_layout(xaxis_title="Maand", yaxis_title="Aantal schades")
    st.plotly_chart(fig_line, use_container_width=True)


def page_locatie():
    st.header("Data rond locatie")
    st.write("Overzicht van aantal schades per locatie (gefilterd op gekozen jaar).")

    if not col_locatie:
        st.warning("Geen kolom locatie gevonden in BRON.")
        return

    lim_choice = st.selectbox("Toon", ["Top 10", "Top 20", "Alle locaties"], index=0)
    lim = 10 if lim_choice == "Top 10" else 20 if lim_choice == "Top 20" else None

    temp = df_filtered.copy()
    temp["_loc"] = temp[col_locatie].fillna("Onbekend").astype(str).str.strip()
    table = temp.groupby("_loc").size().reset_index(name="Aantal").sort_values("Aantal", ascending=False)
    table_view = table.head(lim) if lim else table

    st.dataframe(table_view.rename(columns={"_loc": "Locatie"}), use_container_width=True, hide_index=True)


def page_coaching():
    st.header("Coaching – vergelijkingen")

    if not col_pnr:
        st.info("Geen P-nr kolom gevonden in BRON.")
        return

    damage_pnr_set = set(df_bron[col_pnr].dropna().apply(pnr_to_clean_string))
    damage_pnr_set.discard("")

    done_pnr_set = set(coaching_map.keys())

    pending_in_damage = len([p for p in coaching_pending_set if p in damage_pnr_set])
    done_in_damage = len([p for p in done_pnr_set if p in damage_pnr_set])

    cA, cB = st.columns(2)
    with cA:
        st.metric("📄 Lopend – ruwe rijen (coachingslijst)", pending_raw_count)
        st.metric("🔵 Lopend (in schadelijst)", pending_in_damage)
    with cB:
        st.metric("📄 Voltooid – ruwe rijen (coachingslijst)", done_raw_count)
        st.metric("🟡 Voltooid (in schadelijst)", done_in_damage)

    st.divider()

    counts = df_filtered.groupby(df_filtered[col_pnr].apply(pnr_to_clean_string)).size()
    high_damage = []
    for pnr_key, cnt in counts.items():
        if not pnr_key:
            continue
        if cnt > 2 and (pnr_key not in coaching_map) and (pnr_key not in coaching_pending_set):
            nm = ""
            if col_naam:
                nm_ser = df_filtered[df_filtered[col_pnr].apply(pnr_to_clean_string) == pnr_key][col_naam].dropna()
                nm = str(nm_ser.iloc[0]).strip() if len(nm_ser) else ""
            high_damage.append({"P-nr": pnr_key, "Naam": nm, "Aantal": int(cnt)})

    st.markdown("### P-nrs > 2 schades zonder coaching (jaarfilter)")
    st.write(f"Aantal: **{len(high_damage)}**")
    if high_damage:
        st.dataframe(pd.DataFrame(high_damage).sort_values("Aantal", ascending=False), use_container_width=True, hide_index=True)


def page_analyse():
    st.header("Analyse")

    st.subheader("1. Totaal schades")
    st.write(f"Totaal aantal schades (jaarfilter): **{len(df_filtered)}**")

    st.subheader("2. Histogram — aantal schades per medewerker")
    st.caption("Mediaan is op basis van alle P-nrs in 'data hastus' indien aanwezig.")

    if not col_pnr:
        st.info("Geen P-nr kolom gevonden in BRON.")
        return

    pnr_series = df_filtered[col_pnr].apply(pnr_to_clean_string)
    damage_per_pnr = pnr_series.value_counts().to_dict()

    damages_all = []
    col_h_pnr = None
    if not df_hastus.empty:
        col_h_pnr = find_col(df_hastus, ["p-nr", "pnr", "personeelsnr", "personeelsnummer", "p nr"])

    if col_h_pnr:
        hastus_pnrs = df_hastus[col_h_pnr].dropna().apply(pnr_to_clean_string).tolist()
        hastus_pnrs = [p for p in hastus_pnrs if p]
        damages_all = [int(damage_per_pnr.get(p, 0)) for p in hastus_pnrs]
    else:
        damages_all = list(map(int, damage_per_pnr.values()))

    if not damages_all:
        st.info("Geen bruikbare P-nrs gevonden.")
        return

    median = float(np.median(damages_all))
    freq = pd.Series(damages_all).value_counts().sort_index()
    hist_df = pd.DataFrame({"Schades": freq.index.astype(int), "Medewerkers": freq.values.astype(int)})

    fig = px.bar(hist_df, x="Schades", y="Medewerkers")
    fig.add_vline(
        x=round(median),
        line_dash="dash",
        line_width=2,
        line_color="red",
        annotation_text=f"Mediaan ≈ {median:.2f}",
        annotation_position="top",
    )
    fig.update_layout(xaxis_title="Aantal schades per medewerker", yaxis_title="Aantal medewerkers", showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("3. Verdeling P-nrs per 10.000-tal (Hastus)")
    if df_hastus.empty or not col_h_pnr:
        st.info("Tabblad 'data hastus' of P-nr kolom niet gevonden.")
        return

    pnrs = pd.to_numeric(df_hastus[col_h_pnr], errors="coerce").dropna().astype(int)
    st.write(f"Totaal P-nrs in **data hastus**: **{len(pnrs)}**")

    bin_size = 10000
    bins = (pnrs // bin_size) * bin_size
    counts = bins.value_counts().sort_index()
    labels = [f"{b}–{b + bin_size - 1}" for b in counts.index.tolist()]
    dist_df = pd.DataFrame({"Range": labels, "Aantal": counts.values})

    fig2 = px.bar(dist_df, x="Range", y="Aantal")
    fig2.update_layout(xaxis_title="10.000-tal range", yaxis_title="Aantal P-nrs", showlegend=False)
    st.plotly_chart(fig2, use_container_width=True)


def page_gesprekken():
    st.header("Gesprekken")
    st.write("Overzicht uit **Overzicht gesprekken (aangepast).xlsx** (respecteert de jaarfilter).")
    st.caption("ℹ️ Lange teksten worden automatisch gewrapt. (Indien nodig kan je in een cel scrollen.)")

    gesprek_nummer_col = find_col(df_gesprekken, ["nummer"])
    gesprek_naam_col = find_col(df_gesprekken, ["chauffeurnaam"])
    gesprek_datum_col = find_col(df_gesprekken, ["datum"])

    df_g = df_gesprekken.copy()

    if gesprek_datum_col:
        df_g["_dt"] = to_datetime_utc_series(df_g[gesprek_datum_col])
        df_g["_jaar"] = df_g["_dt"].dt.year
        if year_choice != "ALL":
            df_g = df_g[df_g["_jaar"] == int(year_choice)]

    c1, c2 = st.columns([3, 1])
    g_term = c1.text_input("Zoek", placeholder="Zoek personeelsnr of naam...", label_visibility="collapsed")
    reset = c2.button("Reset", use_container_width=True)
    if reset:
        st.rerun()

    if g_term.strip():
        tt = g_term.strip().lower()
        m = pd.Series(False, index=df_g.index)
        if gesprek_nummer_col:
            m |= df_g[gesprek_nummer_col].apply(pnr_to_clean_string).astype(str).str.lower().str.contains(re.escape(tt), na=False)
        if gesprek_naam_col:
            m |= df_g[gesprek_naam_col].astype(str).str.lower().str.contains(re.escape(tt), na=False)
        df_g = df_g[m]

    st.caption(f"Resultaten: {len(df_g)}")

    out = df_g.copy()
    if gesprek_datum_col and gesprek_datum_col in out.columns:
        out[gesprek_datum_col] = to_datetime_utc_series(out[gesprek_datum_col]).dt.strftime("%d/%m/%Y")

    # >>> Hier: data_editor i.p.v. dataframe (wrapping + hogere rijen)
    st.data_editor(
        out[GESPREK_COLS],
        use_container_width=True,
        hide_index=True,
        disabled=True,
        column_config=gesprekken_column_config(GESPREK_COLS),
    )


# ============================================================
# ROUTER
# ============================================================
page = st.session_state.page

if page == "dashboard":
    page_dashboard()
elif page == "chauffeur":
    page_chauffeur()
elif page == "voertuig":
    page_voertuig()
elif page == "locatie":
    page_locatie()
elif page == "coaching":
    page_coaching()
elif page == "analyse":
    page_analyse()
elif page == "gesprekken":
    page_gesprekken()
else:
    st.session_state.page = DEFAULT_PAGE
    page_dashboard()

sidebar_status()
