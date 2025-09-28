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
    with st.expander("📄 Voorbeeld van de data"):
        st.dataframe(df.head(), use_container_width=True)
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
with t_overzicht:
    st.subheader("📅 Overzicht geselecteerde periode")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📈 Inkomen", euro(inkomen))
    c2.metric("📌 Vaste kosten", euro(uitgaven_vast), f"{pct(uitgaven_vast, inkomen, absolute=True)} van inkomen")
    c3.metric("📎 Variabele kosten", euro(uitgaven_var), f"{pct(uitgaven_var, inkomen, absolute=True)} van inkomen")
    c4.metric("💰 Totaal saldo", euro(netto), f"{pct(netto, inkomen, signed=True)} van inkomen")

    # Gauges
    # 1) gemiddelde financiële gezondheid (alle maanden binnen volledige df)
    try:
        scores_all = []
        spaar_pct_list, vaste_pct_list = [], []

        for ym, df_month in df.groupby(df["datum"].dt.to_period("M"), sort=True):
            if df_month.empty:
                continue
            cat = df_month["categorie"].astype(str).str.strip().str.lower()
            is_loon = is_income(cat)
            ink = df_month[is_loon]["bedrag"].sum()
            uitg = df_month[~is_loon]["bedrag"].sum()
            saldo = ink + uitg

            sparen_pct = _safe_div(saldo, ink)
            spaar_pct_list.append(sparen_pct * 100 if not pd.isna(sparen_pct) else np.nan)

            vaste_ratio = np.nan
            if "vast/variabel" in df_month.columns:
                vaste_lasten = df_month[(df_month["vast/variabel"] == "Vast") & (~is_loon)]["bedrag"].sum()
                vaste_ratio = _safe_div(abs(vaste_lasten), abs(ink) if ink != 0 else np.nan)
            vaste_pct_list.append(vaste_ratio * 100 if not pd.isna(vaste_ratio) else np.nan)

            score_sparen = _clamp(sparen_pct / 0.2 if not pd.isna(sparen_pct) else np.nan, 0, 1)
            score_vast = np.nan if pd.isna(vaste_ratio) else (1.0 - _clamp((vaste_ratio - 0.5) / 0.5, 0, 1))

            components = {"Sparen": (score_sparen, 0.5), "Vaste lasten": (score_vast, 0.5)}
            avail = {k: v for k, (v, w) in components.items() if not pd.isna(v)}
            if not avail:
                continue
            total_weight = sum([components[k][1] for k in avail.keys()])
            score_0_1 = sum([components[k][0] * components[k][1] for k in avail.keys()]) / total_weight
            scores_all.append(score_0_1)

        fig_avg = None
        if scores_all:
            avg_score = int(round((sum(scores_all) / len(scores_all)) * 100))
            fig_avg = go.Figure(go.Indicator(
                mode="gauge+number",
                value=avg_score,
                number={'suffix': "/100"},
                gauge={'axis': {'range': [0, 100]}, 'bar': {'thickness': 0.3},
                       'steps': [
                           {'range': [0, 50], 'color': '#fca5a5'},
                           {'range': [50, 65], 'color': '#fcd34d'},
                           {'range': [65, 80], 'color': '#a7f3d0'},
                           {'range': [80, 100], 'color': '#86efac'},
                       ]}
            ))
            fig_avg.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10))

        # 2) uitgaven tov inkomen
        perc_all = None
        fig_exp_all = None
        ink_all = df_filtered[is_income(df["categorie"].astype(str).str.lower())]["bedrag"].sum()
        uit_all = df_filtered[~is_income(df["categorie"].astype(str).str.lower())]["bedrag"].sum()
        if not pd.isna(ink_all) and abs(ink_all) > 1e-9:
            perc_all = float(abs(uit_all) / abs(ink_all) * 100.0)
            axis_max = max(120, min(200, (int(perc_all // 10) + 2) * 10))
            fig_exp_all = go.Figure(go.Indicator(
                mode="gauge+number", value=perc_all, number={'suffix': '%'},
                gauge={'axis': {'range': [0, axis_max]}, 'bar': {'thickness': 0.3},
                       'steps': [
                           {'range': [0, 33.33], 'color': '#86efac'},
                           {'range': [33.33, 100], 'color': '#fcd34d'},
                           {'range': [100, axis_max], 'color': '#fca5a5'},
                       ],
                       'threshold': {'line': {'color': 'black', 'width': 2}, 'thickness': 0.75, 'value': 100}}
            ))
            fig_exp_all.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10))

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("🎯 Gemiddelde gezondheid")
            if fig_avg:
                st.plotly_chart(fig_avg, use_container_width=True)
            else:
                st.info("Onvoldoende gegevens voor de score.")
        with col2:
            st.subheader("🎯 Uitgaven t.o.v. inkomen")
            if fig_exp_all:
                st.plotly_chart(fig_exp_all, use_container_width=True)
                st.caption("Groen < 33.3%, geel 33.3–100%, rood ≥ 100%.")
            else:
                st.info("Geen inkomen gevonden in alle data.")
    except Exception as e:
        st.warning(f"Kon gauges niet tekenen: {e}")

# -------------- Maand --------------
with t_maand:
    st.subheader(f"📆 Overzicht voor {geselecteerde_maand}")

    # Trend vs vorige maand
    if not df_maand.empty:
        ref = df_maand["datum"].max()
        prev_year, prev_month = ((ref.year - 1, 12) if ref.month == 1 else (ref.year, ref.month - 1))
        prev_mask = (df_filtered["datum"].dt.year == prev_year) & (df_filtered["datum"].dt.month == prev_month)
        df_prev = df_filtered[prev_mask].copy()

        def total_of(dfin: pd.DataFrame, *, cat=None, vv=None):
            d = dfin.copy()
            cat_col = d["categorie"].astype(str).str.strip().str.lower()
            income_mask = is_income(cat_col)
            if cat == "inkomsten":
                d = d[income_mask]
            elif cat == "uitgaven":
                d = d[~income_mask]
            if vv is not None:
                d = d[d["vast/variabel"].astype(str).str.strip().str.title().eq(vv)]
            return d["bedrag"].sum()

        prev_ink = total_of(df_prev, cat="inkomsten") if not df_prev.empty else 0.0
        prev_vast = total_of(df_prev, vv="Vast") if not df_prev.empty else 0.0
        prev_var  = total_of(df_prev, vv="Variabel") if not df_prev.empty else 0.0
        prev_net  = prev_ink + prev_vast + prev_var

        delta_ink = inkomen_m - prev_ink
        delta_vast = uit_vast_m - prev_vast
        delta_var = uit_var_m - prev_var
        delta_net = netto_m - prev_net

        tc1, tc2, tc3, tc4 = st.columns(4)
        tc1.metric("📈 Inkomen (trend)", euro(inkomen_m), delta=euro(delta_ink))
        tc2.metric("📌 Vaste kosten (trend)", euro(uit_vast_m), delta=euro(delta_vast))
        tc3.metric("📎 Variabele kosten (trend)", euro(uit_var_m), delta=euro(delta_var))
        tc4.metric("💰 Netto (trend)", euro(netto_m), delta=euro(delta_net))

        # Top categorieën in de maand
        top = (
            df_maand[~is_income(df_maand["categorie"].astype(str).str.lower())]
            .groupby(["categorie", "vast/variabel"], dropna=False)["bedrag"].sum().abs()
            .reset_index().sort_values("bedrag", ascending=False).head(12)
        )
        if not top.empty:
            fig_top = px.bar(top, x="categorie", y="bedrag", color="vast/variabel",
                             title=f"Top uitgaven — {geselecteerde_maand}", labels={"bedrag": "€"})
            st.plotly_chart(fig_top, use_container_width=True)
    else:
        st.info("Geen transacties in deze maand binnen het filter.")

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
with t_whatif:
    st.subheader("🧪 Wat-als scenario")
    extra_inkomen = st.number_input("Extra inkomen per maand (€)", value=0.0, step=50.0)
    minder_vaste_kosten = st.number_input("Minder vaste kosten per maand (€)", value=0.0, step=50.0)
    minder_variabele_kosten = st.number_input("Minder variabele kosten per maand (€)", value=0.0, step=50.0)

    cat_all_all = df["categorie"].astype(str).str.strip().str.lower()
    is_loon_all_all = is_income(cat_all_all)
    inkomen_all = df[is_loon_all_all]["bedrag"].sum()
    vaste_all = df[(~is_loon_all_all) & (df["vast/variabel"].eq("Vast"))]["bedrag"].sum()
    variabele_all = df[(~is_loon_all_all) & (df["vast/variabel"].eq("Variabel"))]["bedrag"].sum()

    # Bestaande ratio
    perc_base = abs((vaste_all + variabele_all) / inkomen_all) * 100 if inkomen_all != 0 else None

    maanden = len(df["datum"].dt.to_period("M").unique())
    inkomen_sim = inkomen_all + extra_inkomen * maanden
    vaste_sim = vaste_all - minder_vaste_kosten * maanden
    variabele_sim = variabele_all - minder_variabele_kosten * maanden
    perc_sim = abs((vaste_sim + variabele_sim) / inkomen_sim) * 100 if inkomen_sim != 0 else None

    if perc_sim is not None:
        axis_max = max(120, min(200, (int(perc_sim // 10) + 2) * 10))
        fig_sim = go.Figure(go.Indicator(
            mode="gauge+number", value=perc_sim, number={'suffix': '%'},
            gauge={'axis': {'range': [0, axis_max]}, 'bar': {'thickness': 0.3},
                   'steps': [
                       {'range': [0, 33.33], 'color': '#86efac'},
                       {'range': [33.33, 100], 'color': '#fcd34d'},
                       {'range': [100, axis_max], 'color': '#fca5a5'},
                   ], 'threshold': {'line': {'color': 'black', 'width': 2}, 'thickness': 0.75, 'value': 100}}
        ))
        fig_sim.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig_sim, use_container_width=True)
        if perc_base is not None:
            st.caption(f"Δ t.o.v. huidige situatie: {perc_sim - perc_base:+.1f}%")
    else:
        st.info("Onvoldoende gegevens om scenario te berekenen.")

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
