import io
import calendar
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from pandas.api.types import CategoricalDtype

# ============================================================
# ⚙️ App-config
# ============================================================
st.set_page_config(page_title="Huishoudboekje V2", page_icon="📊", layout="wide")
st.title("📊 Huishoudboekje — V2")
st.caption("Sneller, consistenter, mobielvriendelijker. Eén bestand, klaar voor deploy.")

# ============================================================
# 🌍 Constantes & helpers
# ============================================================
MAANDEN_NL = [
    "Januari", "Februari", "Maart", "April", "Mei", "Juni",
    "Juli", "Augustus", "September", "Oktober", "November", "December"
]
maand_type = CategoricalDtype(categories=MAANDEN_NL, ordered=True)

INKOMST_CATS = {"inkomsten loon", "inkomsten", "loon", "salaris", "bonus", "teruggave", "rente"}


def euro(x: float | int | None) -> str:
    try:
        return f"€ {x:,.2f}".replace(",", "⎖").replace(".", ",").replace("⎖", ".")
    except Exception:
        return "€ 0,00"


def pct(value, total, *, signed=False, absolute=False):
    if total is None or total == 0 or pd.isna(total):
        return "—"
    num = abs(value) if absolute else value
    p = (num / total) * 100
    return f"{p:+.1f}%" if signed else f"{p:.1f}%"


def _clamp(x, lo=0.0, hi=1.0):
    try:
        return float(min(max(x, lo), hi))
    except Exception:
        return np.nan


def _safe_div(a, b):
    return np.nan if (b is None or b == 0 or pd.isna(b)) else a / b


def is_income(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin(INKOMST_CATS)


def norm_vv(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.title().replace({"": "Onbekend"})


# ============================================================
# 📥 Data inladen
# ============================================================
with st.sidebar:
    st.subheader("📥 Data")
    upload = st.file_uploader("Laad Excel (Data-sheet verplicht)", type=["xlsx", "xlsm"], key="upload_v2")
    st.caption("Kolommen: datum, bedrag, categorie, (optioneel) vast/variabel")


@st.cache_data(show_spinner=False)
def laad_data_default_or_bytes(file_bytes: bytes | None, *, pad="huishoud.xlsx") -> pd.DataFrame:
    src = io.BytesIO(file_bytes) if file_bytes else pad
    df = pd.read_excel(src, sheet_name="Data", engine="openpyxl")
    df.columns = df.columns.str.strip().str.lower()

    verplicht = ["datum", "bedrag", "categorie"]
    ontbreekt = [k for k in verplicht if k not in df.columns]
    if ontbreekt:
        raise ValueError(f"Ontbrekende kolommen: {', '.join(ontbreekt)}")

    df["datum"] = pd.to_datetime(df["datum"], errors="coerce")
    df["bedrag"] = pd.to_numeric(df["bedrag"], errors="coerce")
    df["categorie"] = df["categorie"].astype(str).str.strip().str.title()
    if "vast/variabel" not in df.columns:
        df["vast/variabel"] = "Onbekend"

    df["vast/variabel"] = norm_vv(df["vast/variabel"])  # normalize

    df = df.dropna(subset=["datum", "bedrag", "categorie"]).copy()
    df = df[df["categorie"].str.strip() != ""]

    # Datum helpers
    df["maand"] = df["datum"].dt.month
    df["jaar"] = df["datum"].dt.year
    df["maand_naam"] = df["maand"].apply(lambda m: MAANDEN_NL[int(m)-1] if pd.notnull(m) else "")
    df["maand_naam"] = df["maand_naam"].astype(maand_type)

    # Tekens normaliseren (optioneel): als alles positief is, maak uitgaven negatief
    cat_low = df["categorie"].astype(str).str.strip().str.lower()
    income_mask = is_income(cat_low)
    if (
        df.loc[~income_mask, "bedrag"].ge(0).mean() > 0.95
        and df.loc[income_mask, "bedrag"].ge(0).mean() > 0.95
    ):
        df.loc[~income_mask, "bedrag"] = -df.loc[~income_mask, "bedrag"].abs()

    return df


# Laad data
try:
    file_bytes = upload.getvalue() if upload is not None else None
    df = laad_data_default_or_bytes(file_bytes)
    st.success("✅ Data geladen!")
except FileNotFoundError:
    st.warning("Geen bestand gevonden en geen upload. Maak een 'huishoud.xlsx' met sheet 'Data'.")
    st.stop()
except Exception as e:
    st.error(f"❌ Fout bij het laden: {e}")
    st.stop()


# ============================================================
# 🧭 Filters (met Query Params + Reset)
# ============================================================
qp = st.query_params

if "default_start" not in st.session_state:
    st.session_state.default_start = df["datum"].min().date()
    st.session_state.default_end = df["datum"].max().date()

# Init state from query params if present
if "date_from" in qp:
    try:
        st.session_state.date_from = pd.to_datetime(qp.get("date_from")).date()
    except Exception:
        st.session_state.date_from = st.session_state.default_start
else:
    st.session_state.date_from = st.session_state.get("date_from", st.session_state.default_start)

if "date_to" in qp:
    try:
        st.session_state.date_to = pd.to_datetime(qp.get("date_to")).date()
    except Exception:
        st.session_state.date_to = st.session_state.default_end
else:
    st.session_state.date_to = st.session_state.get("date_to", st.session_state.default_end)

with st.sidebar:
    st.subheader("📅 Periode")
    c1, c2 = st.columns([3, 1])
    with c1:
        start_datum = st.date_input("Van", st.session_state.date_from, key="date_from")
        eind_datum = st.date_input("Tot", st.session_state.date_to, key="date_to")
    with c2:
        if st.button("🔄 Reset"):
            st.session_state.date_from = st.session_state.default_start
            st.session_state.date_to = st.session_state.default_end
            st.query_params["date_from"] = str(st.session_state.default_start)
            st.query_params["date_to"] = str(st.session_state.default_end)
            st.rerun()

# Keep query params in sync
st.query_params["date_from"] = str(start_datum)
st.query_params["date_to"] = str(eind_datum)

# Filter toepassen
mask = (df["datum"] >= pd.to_datetime(start_datum)) & (df["datum"] <= pd.to_datetime(eind_datum))
df_filtered = df.loc[mask].copy()
df_filtered["maand_naam"] = df_filtered["maand_naam"].astype(maand_type)

if df_filtered.empty:
    st.warning("⚠️ Geen data in deze periode.")
    st.stop()

# Maandselectie
aanwezig = set(df_filtered["maand_naam"].dropna().astype(str).tolist())
beschikbare_maanden = [m for m in MAANDEN_NL if m in aanwezig]

default_maand = (
    st.query_params.get("month")
    if st.query_params.get("month") in beschikbare_maanden
    else (beschikbare_maanden[-1] if beschikbare_maanden else MAANDEN_NL[0])
)
with st.sidebar:
    geselecteerde_maand = st.selectbox(
        "📆 Kies maand",
        beschikbare_maanden,
        index=(beschikbare_maanden.index(default_maand) if beschikbare_maanden else 0),
        key="maand_select_v2",
    )
# Sync in URL
st.query_params["month"] = geselecteerde_maand


# ============================================================
# 📊 Kernberekeningen
# ============================================================
cat_all = df_filtered["categorie"].astype(str).str.strip().str.lower()
is_loon_all = is_income(cat_all)

inkomen = df_filtered[is_loon_all]["bedrag"].sum()
uitgaven_vast = df_filtered[(~is_loon_all) & (df_filtered["vast/variabel"] == "Vast")]["bedrag"].sum()
uitgaven_var = df_filtered[(~is_loon_all) & (df_filtered["vast/variabel"] == "Variabel")]["bedrag"].sum()

netto = inkomen + uitgaven_vast + uitgaven_var

# Voor de geselecteerde maand
df_maand = df_filtered[df_filtered["maand_naam"] == geselecteerde_maand].copy()
cat_m = df_maand["categorie"].astype(str).str.strip().str.lower()
is_loon_m = is_income(cat_m)
inkomen_m = df_maand[is_loon_m]["bedrag"].sum()
uit_vast_m = df_maand[(~is_loon_m) & (df_maand["vast/variabel"] == "Vast")]["bedrag"].sum()
uit_var_m = df_maand[(~is_loon_m) & (df_maand["vast/variabel"] == "Variabel")]["bedrag"].sum()
netto_m = inkomen_m + uit_vast_m + uit_var_m


# ============================================================
# 🧭 Tabs
# ============================================================
t_overzicht, t_maand, t_budget, t_whatif, t_data = st.tabs([
    "Overzicht", "Maand", "Budgetten", "Wat-als", "Data"
])

# -------------- Overzicht --------------
# -------------- Overzicht --------------
with t_overzicht:
    st.subheader("📅 Overzicht")

    # ── Bereik kiezen: alle data of de gekozen maand uit de Maand-tab
    gekozen_maand = st.session_state.get("maand_select_tab")
    opties = ["Alle data"] + (["Gekozen maand"] if gekozen_maand else [])
    bereik = st.radio("Bereik", opties, index=0, horizontal=True)

    if bereik == "Gekozen maand" and gekozen_maand:
        df_scope = df[df["maand_naam"].astype(str) == gekozen_maand].copy()
        bereik_label = f"geselecteerde maand: {gekozen_maand}"
    else:
        df_scope = df.copy()
        bereik_label = "alle data"

    if df_scope.empty:
        st.info("⚠️ Geen data in het gekozen bereik.")
        st.stop()

    # ── KPI's
    cat_scope = df_scope["categorie"].astype(str).str.strip().str.lower()
    is_loon_scope = is_income(cat_scope)

    inkomen = df_scope[is_loon_scope]["bedrag"].sum()
    uitgaven_vast = df_scope[(~is_loon_scope) & (df_scope["vast/variabel"] == "Vast")]["bedrag"].sum()
    uitgaven_var  = df_scope[(~is_loon_scope) & (df_scope["vast/variabel"] == "Variabel")]["bedrag"].sum()
    netto = inkomen + uitgaven_vast + uitgaven_var

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📈 Inkomen", euro(inkomen))
    c2.metric("📌 Vaste kosten", euro(uitgaven_vast), f"{pct(uitgaven_vast, inkomen, absolute=True)} van inkomen")
    c3.metric("📎 Variabele kosten", euro(uitgaven_var), f"{pct(uitgaven_var, inkomen, absolute=True)} van inkomen")
    c4.metric("💰 Totaal saldo", euro(netto), f"{pct(netto, inkomen, signed=True)} van inkomen")

    # ── Gauges
    try:
        # 1) Gemiddelde financiële gezondheid (per maand binnen df_scope, daarna gemiddelde)
        scores = []
        for _, m in df_scope.groupby(df_scope["datum"].dt.to_period("M"), sort=True):
            if m.empty:
                continue
            cat = m["categorie"].astype(str).str.strip().str.lower()
            inc = m[is_income(cat)]["bedrag"].sum()
            exp = m[~is_income(cat)]["bedrag"].sum()
            saldo = inc + exp

            # componenten
            sparen_pct = _safe_div(saldo, inc)  # doel ≈ 20%
            score_sparen = _clamp(sparen_pct / 0.2 if not pd.isna(sparen_pct) else np.nan, 0, 1)

            vaste_ratio = np.nan
            if "vast/variabel" in m.columns:
                vaste_lasten = m[(m["vast/variabel"] == "Vast") & (~is_income(cat))]["bedrag"].sum()
                vaste_ratio = _safe_div(abs(vaste_lasten), abs(inc) if inc != 0 else np.nan)
            score_vast = np.nan if pd.isna(vaste_ratio) else (1 - _clamp((vaste_ratio - 0.5) / 0.5, 0, 1))

            # ⬇️ extra strafcomponent als uitgaven > inkomen
            spend_ratio = _safe_div(abs(exp), abs(inc))
            score_spend = 1 - _clamp(spend_ratio - 1.0, 0, 1)  # >100% => snel naar 0

            # weging
            components = []
            if not pd.isna(score_sparen): components.append((score_sparen, 0.5))
            if not pd.isna(score_vast):   components.append((score_vast,   0.3))
            if not pd.isna(score_spend):  components.append((score_spend,  0.2))

            if components:
                score = sum(s*w for s, w in components) / sum(w for _, w in components)
                scores.append(score)

        fig_avg = None
        if scores:
            avg_score = int(round(np.mean(scores) * 100))
            fig_avg = go.Figure(go.Indicator(
                mode="gauge+number",
                value=avg_score,
                number={'suffix': "/100"},
                gauge={
                    'axis': {'range': [0, 100]},
                    'bar': {'thickness': 0.3},
                    'steps': [
                        {'range': [0, 50],  'color': '#fca5a5'},
                        {'range': [50, 65], 'color': '#fcd34d'},
                        {'range': [65, 80], 'color': '#a7f3d0'},
                        {'range': [80, 100],'color': '#86efac'},
                    ],
                }
            ))
            fig_avg.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10))

        # 2) Uitgaven t.o.v. inkomen (zelfde scope)
        fig_exp = None
        if abs(inkomen) > 1e-9:
            perc = float(abs(uitgaven_vast + uitgaven_var) / abs(inkomen) * 100.0)
            axis_max = max(120, min(200, (int(perc // 10) + 2) * 10))
            fig_exp = go.Figure(go.Indicator(
                mode="gauge+number",
                value=perc,
                number={'suffix': "%"},
                gauge={
                    'axis': {'range': [0, axis_max]},
                    'bar': {'thickness': 0.3},
                    'steps': [
                        {'range': [0, 33.33],   'color': '#86efac'},
                        {'range': [33.33, 100], 'color': '#fcd34d'},
                        {'range': [100, axis_max], 'color': '#fca5a5'},
                    ],
                    'threshold': {'line': {'color': 'black', 'width': 2}, 'thickness': 0.75, 'value': 100}
                }
            ))
            fig_exp.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10))

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("🎯 Gemiddelde gezondheid")
            if fig_avg is not None:
                _ = st.plotly_chart(fig_avg, use_container_width=True)
            else:
                st.info("Onvoldoende gegevens voor de score.")
        with col2:
            st.subheader("🎯 Uitgaven t.o.v. inkomen")
            if fig_exp is not None:
                _ = st.plotly_chart(fig_exp, use_container_width=True)
                st.caption("Bereik: " + bereik_label + " — Groen < 33.3%, geel 33.3–100%, rood ≥ 100%.")
            else:
                st.info("Geen inkomen gevonden.")

    except Exception as e:
        st.warning(f"Kon gauges niet tekenen: {e}")



# -------------- Maand --------------
# -------------- Maand --------------
# -------------- Maand --------------
with t_maand:
    st.header("📆 Maandoverzicht")

    # -- Maandkeuze (in de tab) --
    aanwezig = df["maand_naam"].dropna().astype(str).unique().tolist()
    beschikbare_maanden = [m for m in MAANDEN_NL if m in aanwezig]
    default_maand = (
        st.query_params.get("month")
        if st.query_params.get("month") in beschikbare_maanden
        else (beschikbare_maanden[-1] if beschikbare_maanden else MAANDEN_NL[0])
    )

    geselecteerde_maand = st.selectbox(
        "📆 Kies een maand",
        beschikbare_maanden,
        index=(beschikbare_maanden.index(default_maand) if beschikbare_maanden else 0),
        key="maand_select_tab",
    )
    st.query_params["month"] = geselecteerde_maand  # bookmarkbaar

    st.subheader(f"🗓️ Overzicht voor {geselecteerde_maand}")

    # -- Filter: alleen gekozen maand --
    df_maand = df[df["maand_naam"].astype(str) == geselecteerde_maand].copy()
    if df_maand.empty:
        st.warning("⚠️ Geen data voor deze maand.")
        st.stop()

    # -- KPI's (maand) --
    cat_m = df_maand["categorie"].astype(str).str.strip().str.lower()
    is_loon_m = is_income(cat_m)

    inkomen_m  = df_maand[is_loon_m]["bedrag"].sum()
    uit_vast_m = df_maand[(~is_loon_m) & (df_maand["vast/variabel"].astype(str).str.strip().str.title() == "Vast")]["bedrag"].sum()
    uit_var_m  = df_maand[(~is_loon_m) & (df_maand["vast/variabel"].astype(str).str.strip().str.title() == "Variabel")]["bedrag"].sum()
    netto_m    = inkomen_m + uit_vast_m + uit_var_m

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📈 Inkomen (maand)", euro(inkomen_m))
    c2.metric("📌 Vaste kosten (maand)", euro(uit_vast_m))
    c3.metric("📎 Variabele kosten (maand)", euro(uit_var_m))
    c4.metric("💰 Netto (maand)", euro(netto_m))

    # =====================================================================
    # 📊 Alle categorieën verticaal, kleur = Vast/Variabel,
    #     categorie boven budget = rode omlijning (accent)
    # =====================================================================

    # 1) Uitgaven per categorie + vast/variabel (voor kleur)
    bars_df = (
        df_maand[~is_income(df_maand["categorie"].astype(str).str.lower())]
        .groupby(["categorie", "vast/variabel"], dropna=False)["bedrag"]
        .sum()
        .abs()
        .reset_index()
        .rename(columns={"bedrag": "bedrag_abs"})
    )

    # 2) Totaal per categorie (voor budgetvergelijking en omlijning)
    totals = (
        bars_df.groupby("categorie", as_index=False)["bedrag_abs"]
        .sum()
        .rename(columns={"bedrag_abs": "totaal_cat"})
    )

    # 3) Budget per categorie (uit Budget-tab state, indien beschikbaar)
    budget_state = st.session_state.get("budget_state")
    if isinstance(budget_state, pd.DataFrame) and "budget" in budget_state.columns:
        budget_map = budget_state[["categorie", "budget"]].copy()
        budget_map["budget"] = pd.to_numeric(budget_map["budget"], errors="coerce")
    else:
        budget_map = pd.DataFrame({"categorie": totals["categorie"], "budget": np.nan})

    # 4) Join totals + budget en markeer overschrijding
    totals = totals.merge(budget_map, on="categorie", how="left")
    totals["boven_budget"] = totals["budget"].notna() & (totals["totaal_cat"] > totals["budget"])

    # 5) Hoofd-barchart (alle categorieën, y = categorie)
    # --- 5) Hoofd-barchart (alle categorieën, y = categorie) ---

if not bars_df.empty:
    # Sorteer y-as op totaal (groot → klein)
    sort_order = totals.sort_values("totaal_cat", ascending=True)["categorie"].tolist()
    bars_df["categorie"] = pd.Categorical(bars_df["categorie"], categories=sort_order, ordered=True)
    bars_df = bars_df.sort_values(["categorie", "vast/variabel"])

    # Join: vlag 'boven budget' per categorie
    bars_df = bars_df.merge(totals[["categorie", "boven_budget", "budget", "totaal_cat"]],
                            on="categorie", how="left")

    # Nieuw kleurveld: rood als boven budget, anders Vast/Variabel-kleur behouden
    bars_df["kleur"] = np.where(
        bars_df["boven_budget"], "🚨 Boven budget", bars_df["vast/variabel"]
    )

    # (optioneel) nette namen voor legend
    color_map = {
        "Vast": "#1f77b4",         # jouw vaste kleur (donkerblauw)
        "Variabel": "#8ec7ff",     # jouw variabele kleur (lichtblauw)
        "🚨 Boven budget": "red"    # overschrijding: rood
    }

    # Hover extra info: budget + totaalsom
    bars_df["budget_txt"] = bars_df["budget"].apply(lambda x: euro(x) if pd.notna(x) else "—")
    bars_df["totaal_txt"] = bars_df["totaal_cat"].apply(euro)

    fig_top = px.bar(
        bars_df,
        x="bedrag_abs",
        y="categorie",
        color="kleur",
        orientation="h",
        title=f"Uitgaven per categorie — {geselecteerde_maand}",
        labels={"bedrag_abs": "€", "categorie": "Categorie", "kleur": "Legenda"},
        color_discrete_map=color_map,
        hover_data={
            "budget_txt": True,
            "totaal_txt": True,
            "vast/variabel": True,
            "bedrag_abs": ":.2f",
            "kleur": False,
            "budget": False,
            "totaal_cat": False,
        },
    )

    # Layout: categorievolgorde + dynamische hoogte
    fig_top.update_layout(
        yaxis=dict(categoryorder="array", categoryarray=sort_order),
        height=max(350, 26 * len(sort_order) + 120),
        margin=dict(l=10, r=10, t=40, b=10),
        barmode="group",
        legend_title_text="",
    )

    # Duidelijke hovertekst
    fig_top.update_traces(
        hovertemplate="Categorie: %{y}<br>Bedrag: € %{x:.2f}<br>Type: %{customdata[0]}<br>"
                      "Budget: %{customdata[1]}<br>Totaal cat.: %{customdata[2]}<extra></extra>",
        customdata=np.stack([
            bars_df["vast/variabel"],
            bars_df["budget_txt"],
            bars_df["totaal_txt"]
        ], axis=-1)
    )

    st.plotly_chart(fig_top, use_container_width=True)

    # (optioneel) tabelletje eronder ongewijzigd laten


        # Tabelletje eronder (optioneel, handig om verschillen te zien)
        with st.expander("📋 Detailtabel (totaal vs budget)"):
            tbl = totals.assign(
                Budget=totals["budget"].apply(lambda x: euro(x) if pd.notna(x) else "—"),
                Totaal=totals["totaal_cat"].apply(euro),
                Status=np.where(totals["boven_budget"], "🚨 Boven budget", "✅ Binnen budget")
            )[["categorie", "Totaal", "Budget", "Status"]].rename(columns={"categorie": "Categorie"})
            st.dataframe(tbl, use_container_width=True)
    else:
        st.info("Geen uitgaven voor de geselecteerde maand.")





# -------------- Budgetten --------------
with t_budget:
    st.subheader(f"🎯 Budgetten — {geselecteerde_maand}")

    # Alle vaste categorieën
    vaste_cats = (
        df[df["vast/variabel"].eq("Vast")]["categorie"].astype(str).str.strip().str.title().dropna().unique()
    )

    # Uitgaven deze maand (alleen vast)
    uitgaven_mnd_ser = (
        df_filtered[
            (df_filtered["maand_naam"] == geselecteerde_maand)
            & (~is_income(df_filtered["categorie"].astype(str).str.lower()))
            & (df_filtered["vast/variabel"].eq("Vast"))
        ]
        .groupby("categorie")["bedrag"].sum().abs()
    )

    # Gemiddelde per categorie uit voorgaande maanden
    if df_maand.empty:
        gemiddelde_per_cat = pd.Series(dtype=float)
    else:
        ref = df_maand["datum"].max()
        maand_start = pd.Timestamp(ref.year, ref.month, 1)
        prev = df[(df["datum"] < maand_start) & (df["vast/variabel"].eq("Vast")) & (~is_income(df["categorie"].astype(str).str.lower()))].copy()
        if prev.empty:
            gemiddelde_per_cat = pd.Series(dtype=float)
        else:
            per_mnd_cat = prev.groupby([prev["datum"].dt.to_period("M"), "categorie"])['bedrag'].sum().abs()
            gemiddelde_per_cat = per_mnd_cat.groupby("categorie").mean()

    # Editor-state
    current_cats = pd.DataFrame({"categorie": sorted(vaste_cats)})
    if "budget_state" not in st.session_state:
        st.session_state.budget_state = current_cats.assign(budget=np.nan)
    else:
        st.session_state.budget_state = current_cats.merge(st.session_state.budget_state, on="categorie", how="left")

    if not gemiddelde_per_cat.empty:
        mask_na = st.session_state.budget_state["budget"].isna()
        st.session_state.budget_state.loc[mask_na, "budget"] = (
            st.session_state.budget_state.loc[mask_na, "categorie"].map(gemiddelde_per_cat)
        )

    with st.expander("✏️ Stel budgetten in", expanded=False):
        budget_df = st.data_editor(
            st.session_state.budget_state,
            num_rows="dynamic",
            hide_index=True,
            key="budget_editor_v2",
            column_config={
                "categorie": st.column_config.TextColumn("Categorie", disabled=True),
                "budget": st.column_config.NumberColumn("Budget (€)", min_value=0.0, step=10.0,
                                                         help="Auto = gemiddelde vorige maanden; aanpasbaar")
            }
        )
        st.session_state.budget_state = budget_df

    # Join & tabel
    uitgaven_full = (
        uitgaven_mnd_ser.reindex(sorted(vaste_cats)).fillna(0.0).rename("uitgave")
    )
    budget_join = (
        st.session_state.budget_state.set_index("categorie").join(uitgaven_full, how="left").reset_index()
    )
    budget_join["budget"] = pd.to_numeric(budget_join["budget"], errors="coerce")
    budget_join["uitgave"] = pd.to_numeric(budget_join["uitgave"], errors="coerce").fillna(0)
    budget_join["verschil"] = budget_join["budget"] - budget_join["uitgave"]

    tabel = budget_join.assign(
        Budget=budget_join["budget"].apply(lambda x: euro(x) if pd.notna(x) else "—"),
        Uitgave=budget_join["uitgave"].apply(euro),
        **{"Δ (budget - uitgave)": budget_join["verschil"].apply(lambda x: euro(x) if pd.notna(x) else "—")},
        Status=np.where(
            budget_join["budget"].notna() & (budget_join["uitgave"] > budget_join["budget"]),
            "🚨 Over budget",
            np.where(budget_join["budget"].notna(), "✅ Binnen budget", "—")
        )
    )
    kol = ["categorie", "Budget", "Uitgave", "Δ (budget - uitgave)", "Status"]
    st.dataframe(tabel.loc[:, kol].rename(columns={"categorie": "Categorie"}), use_container_width=True)

    # Chart
    b_plot = budget_join.dropna(subset=["budget"]).copy()
    if not b_plot.empty:
        b_plot = b_plot.sort_values("uitgave", ascending=False)
        fig_b = px.bar(
            b_plot.melt(id_vars=["categorie"], value_vars=["uitgave", "budget"], var_name="type", value_name="€"),
            x="categorie", y="€", color="type", barmode="group",
            title=f"Uitgaven vs. Budget — {geselecteerde_maand}", labels={"categorie": "Categorie"}
        )
        st.plotly_chart(fig_b, use_container_width=True)

    # Prognose einde maand
    st.subheader("🔮 Prognose einde van de maand")
    if not df_maand.empty:
        laatste_datum = df_maand["datum"].max()
        jaar, mnd = laatste_datum.year, laatste_datum.month
        mask_ym = (
            (df_filtered["datum"].dt.year == jaar)
            & (df_filtered["datum"].dt.month == mnd)
            & (~is_income(df_filtered["categorie"].astype(str).str.lower()))
        )
        df_ym = df_filtered[mask_ym].copy()
        if not df_ym.empty:
            uitg_tmv = abs(df_ym[df_ym["datum"] <= laatste_datum]["bedrag"].sum())
            spent_per_cat = (
                df_ym[df_ym["datum"] <= laatste_datum].groupby("categorie")["bedrag"].sum().abs()
            )
            budget_per_cat = (
                budget_join.set_index("categorie")["budget"].astype(float)
                if "budget" in budget_join.columns else pd.Series(dtype=float)
            )
            resterend_per_cat = (budget_per_cat - spent_per_cat).clip(lower=0).fillna(0)
            proj = float(uitg_tmv + resterend_per_cat.sum())

            c1, c2, c3 = st.columns(3)
            c1.metric("Uitgaven t/m vandaag", euro(uitg_tmv))
            c2.metric("Voorspelling maandtotaal", euro(proj))
            c3.metric("Nog te verwachten", euro(proj - uitg_tmv))

            totaal_budget = pd.to_numeric(budget_join["budget"], errors="coerce").sum(skipna=True)
            if not np.isnan(totaal_budget) and totaal_budget > 0:
                if proj > totaal_budget:
                    st.error(f"⚠️ Verwachte uitgaven ({euro(proj)}) liggen boven totaalbudget ({euro(totaal_budget)}).")
                else:
                    st.success(f"✅ Verwachte uitgaven ({euro(proj)}) liggen binnen totaalbudget ({euro(totaal_budget)}).")
            st.caption("Prognose gebaseerd op budgetten: resterend = max(0, budget − uitgegeven).")
        else:
            st.info("Geen uitgaven gevonden voor de gekozen jaar-maand.")
    else:
        st.info("Geen data in de geselecteerde maand voor prognose.")

# -------------- Wat-als --------------
# -------------- Wat-als --------------
with t_whatif:
    st.subheader("🧪 Wat-als scenario")

    # Bereik kiezen (zelfde logica als in Overzicht)
    gekozen_maand = st.session_state.get("maand_select_tab")
    opties = ["Alle data"] + (["Gekozen maand"] if gekozen_maand else [])
    bereik = st.radio("Bereik", opties, index=0, horizontal=True, key="whatif_scope_radio")

    if bereik == "Gekozen maand" and gekozen_maand:
        df_scope = df[df["maand_naam"].astype(str) == gekozen_maand].copy()
        bereik_label = f"geselecteerde maand: {gekozen_maand}"
    else:
        df_scope = df.copy()
        bereik_label = "alle data"

    if df_scope.empty:
        st.info("⚠️ Geen data in dit bereik.")
        st.stop()

    # Invoer
    c_a, c_b, c_c = st.columns(3)
    with c_a:
        extra_inkomen = st.number_input("Extra inkomen per maand (€)", value=0.0, step=50.0, key="whatif_extra_inc")
    with c_b:
        minder_vaste_kosten = st.number_input("Minder vaste kosten per maand (€)", value=0.0, step=50.0, key="whatif_less_fixed")
    with c_c:
        minder_variabele_kosten = st.number_input("Minder variabele kosten per maand (€)", value=0.0, step=50.0, key="whatif_less_var")

    # Basisdata op gekozen bereik
    cat = df_scope["categorie"].astype(str).str.strip().str.lower()
    is_loon = is_income(cat)

    inkomen_bas = df_scope[is_loon]["bedrag"].sum()
    vaste_bas   = df_scope[(~is_loon) & (df_scope["vast/variabel"].eq("Vast"))]["bedrag"].sum()
    var_bas     = df_scope[(~is_loon) & (df_scope["vast/variabel"].eq("Variabel"))]["bedrag"].sum()
    tot_uitg_bas = vaste_bas + var_bas

    # Aantal maanden in het bereik
    n_maanden = int(df_scope["datum"].dt.to_period("M").nunique())

    # Bestaande ratio
    perc_base = abs(tot_uitg_bas) / abs(inkomen_bas) * 100 if abs(inkomen_bas) > 1e-9 else None

    # Simulatie (op maandbasis)
    inkomen_sim = inkomen_bas + extra_inkomen * n_maanden
    vaste_sim   = vaste_bas - minder_vaste_kosten * n_maanden
    var_sim     = var_bas - minder_variabele_kosten * n_maanden
    tot_uitg_sim = vaste_sim + var_sim

    perc_sim = abs(tot_uitg_sim) / abs(inkomen_sim) * 100 if abs(inkomen_sim) > 1e-9 else None

    # KPI’s tonen
    k1, k2, k3 = st.columns(3)
    k1.metric("Inkomen (basis)",   euro(inkomen_bas))
    k2.metric("Uitgaven (basis)",  euro(tot_uitg_bas))
    k3.metric("Maanden in bereik", n_maanden)

    # Gauge + delta
    if perc_sim is not None:
        axis_max = max(120, min(200, (int(perc_sim // 10) + 2) * 10))
        fig_sim = go.Figure(go.Indicator(
            mode="gauge+number",
            value=perc_sim,
            number={'suffix': "%"},
            gauge={
                'axis': {'range': [0, axis_max]},
                'bar': {'thickness': 0.3},
                'steps': [
                    {'range': [0, 33.33],   'color': '#86efac'},
                    {'range': [33.33, 100], 'color': '#fcd34d'},
                    {'range': [100, axis_max], 'color': '#fca5a5'},
                ],
                'threshold': {'line': {'color': 'black', 'width': 2}, 'thickness': 0.75, 'value': 100}
            }
        ))
        fig_sim.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10))

        # Unieke key voorkomt DuplicateElementId
        _ = st.plotly_chart(fig_sim, use_container_width=True, key="whatif_gauge")

        if perc_base is not None:
            st.caption(
                f"Bereik: {bereik_label} — Uitgaven/inkomen nu: {perc_base:.1f}% → "
                f"scenario: {perc_sim:.1f}% (Δ {perc_sim - perc_base:+.1f}%)."
            )
        else:
            st.caption(f"Bereik: {bereik_label}")
    else:
        st.info("Onvoldoende gegevens (inkomen = 0) om de simulatie te tonen.")


# -------------- Data --------------
with t_data:
    st.subheader("📦 Gegevens")
    st.dataframe(df_filtered.sort_values("datum"), use_container_width=True)
import io
buf = io.BytesIO()
df_filtered.to_csv(buf, index=False)  # niets retourneren, alleen schrijven
buf.seek(0)  # terug naar begin!
st.download_button(
    "⬇️ Download CSV (filter)",
    data=buf,  # je mag direct de buffer geven
    file_name="huishoud_filtered.csv",
    mime="text/csv",
)


st.caption("© Huishoudboekje V2 — gemaakt met Streamlit.")
