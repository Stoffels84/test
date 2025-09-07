import streamlit as st
import pandas as pd
import re
from datetime import datetime

# Imports uit auth.py
from auth import login_gate, load_contact_map

# Imports helpers (later verplaatsen naar helpers.py)
from helpers import badge_van_chauffeur, df_to_csv_bytes, extract_url

# Imports data loaders (later verplaatsen naar data_loaders.py)
from data_loaders import load_schade_prepared, lees_coachingslijst

# =========================
# DASHBOARD
# =========================
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

    # Data laden
    df, options = load_schade_prepared()
    gecoachte_ids, coaching_ids, total_geel, total_blauw, excel_info, coach_warn = lees_coachingslijst()
    st.session_state["gecoachte_ids"] = gecoachte_ids
    st.session_state["coaching_ids"]  = coaching_ids
    st.session_state["excel_info"]    = excel_info

    # Extra kolommen
    df["gecoacht_geel"]  = df["dienstnummer"].astype(str).isin(gecoachte_ids)
    df["gecoacht_blauw"] = df["dienstnummer"].astype(str).isin(coaching_ids)

    # Titel + caption
    st.title("📊 Schadegevallen Dashboard")
    st.caption("🟢 goed · 🟠 voldoende · 🔴 slecht/zeer slecht · ⚫ lopende coaching")
    if coach_warn:
        st.sidebar.warning(f"⚠️ {coach_warn}")

    # Filters
    def _ms_all(label, options, all_label, key):
        opts = [all_label] + options
        picked = st.sidebar.multiselect(label, opts, default=[all_label], key=key)
        return options if (all_label in picked or not picked) else picked

    teamcoach_options = options["teamcoach"]
    locatie_options   = options["locatie"]
    voertuig_options  = options["voertuig"]
    kwartaal_options  = options["kwartaal"]

    with st.sidebar:
        st.image("logo.png", use_container_width=True)
        st.header("🔍 Filters")
        selected_teamcoaches = _ms_all("Teamcoach", teamcoach_options, "— Alle teamcoaches —", "flt_tc")
        selected_locaties    = _ms_all("Locatie",   locatie_options,   "— Alle locaties —",   "flt_loc")
        selected_voertuigen  = _ms_all("Voertuig",  voertuig_options,  "— Alle voertuigen —", "flt_vt")
        selected_kwartalen   = _ms_all("Kwartaal",  kwartaal_options,  "— Alle kwartalen —",  "flt_kw")

        if selected_kwartalen:
            per_idx  = pd.PeriodIndex(selected_kwartalen, freq="Q")
            date_from = per_idx.start_time.min().normalize()
            date_to   = per_idx.end_time.max().normalize()
        else:
            date_from = options["min_datum"]
            date_to   = options["max_datum"]

    # Filter toepassen
    apply_quarters = bool(selected_kwartalen)
    sel_periods = pd.PeriodIndex(selected_kwartalen, freq="Q") if apply_quarters else None

    mask = (
        df["teamcoach_disp"].isin(selected_teamcoaches)
        & df["Locatie_disp"].isin(selected_locaties)
        & df["BusTram_disp"].isin(selected_voertuigen)
        & (df["KwartaalP"].isin(sel_periods) if apply_quarters else True)
    )
    df_filtered = df.loc[mask].copy()
    start = pd.to_datetime(date_from)
    end   = pd.to_datetime(date_to) + pd.Timedelta(days=1)
    df_filtered = df_filtered[(df_filtered["Datum"] >= start) & (df_filtered["Datum"] < end)]

    if df_filtered.empty:
        st.warning("⚠️ Geen schadegevallen gevonden voor de geselecteerde filters.")
        st.stop()

    # KPI + CSV export
    st.metric("Totaal aantal schadegevallen", len(df_filtered))
    st.download_button(
        "⬇️ Download gefilterde data (CSV)",
        df_to_csv_bytes(df_filtered),
        file_name=f"schade_filtered_{datetime.today().strftime('%Y%m%d')}.csv",
        mime="text/csv",
        help="Exporteer de huidige selectie inclusief datumfilter."
    )

    # Lichte kolom-normalisatie
    df_filtered = df_filtered.copy()
    df_filtered.columns = (
        df_filtered.columns.astype(str)
            .str.normalize("NFKC")
            .str.strip()
    )

    # Tabs en inhoud volgen hier (niet gewijzigd)
    # ... (de rest van je run_dashboard code blijft hetzelfde)

# =========================
# main
# =========================
def main():
    st.set_page_config(page_title="Schade Dashboard", page_icon="📊", layout="wide")
    if not st.session_state.get("authenticated"):
        login_gate()
        return
    run_dashboard()

if __name__ == "__main__":
    main()
