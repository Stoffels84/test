import re
from datetime import datetime

import pandas as pd
import streamlit as st

from auth import login_gate
from helpers import badge_van_chauffeur, df_to_csv_bytes, extract_url
from data_loaders import load_schade_prepared, lees_coachingslijst

# =========================
# DASHBOARD
# =========================
def run_dashboard():
    # Zijbalk: gebruiker + uitloggen
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

    # Data laden (defensief)
    try:
        df, options = load_schade_prepared()
    except Exception as e:
        st.error(f"Kon schadelijst niet laden: {e}")
        st.stop()

    try:
        gecoachte_ids, coaching_ids, total_geel, total_blauw, excel_info, coach_warn = lees_coachingslijst()
    except Exception as e:
        st.error(f"Kon coachingslijst niet laden: {e}")
        st.stop()

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

    # Kolomnamen normaliseren (visueel netjes)
    df_filtered = df_filtered.copy()
    df_filtered.columns = (
        df_filtered.columns.astype(str)
            .str.normalize("NFKC")
            .str.strip()
    )

    # ===== Tabs =====
    chauffeur_tab, voertuig_tab, locatie_tab, opzoeken_tab, coaching_tab = st.tabs(
        ["🧑‍✈️ Chauffeur", "🚌 Voertuig", "📍 Locatie", "🔎 Opzoeken", "🎯 Coaching"]
    )

    # ===== Tab 1: Chauffeur =====
    with chauffeur_tab:
        st.subheader("📂 Schadegevallen per chauffeur")

        def resolve_col(df_in: pd.DataFrame, candidates: list[str]) -> str | None:
            for c in candidates:
                if c in df_in.columns:
                    return c
            return None
        COL_NAAM = resolve_col(
            df_filtered,
            ["volledige naam", "volledige_naam", "chauffeur", "chauffeur naam", "naam", "volledigenaam"]
        )
        COL_NAAM_DISP = resolve_col(
            df_filtered,
            ["volledige naam_disp", "volledige_naam_disp", "naam_display", "displaynaam"]
        )

        if not COL_NAAM:
            st.error(
                "Kon geen kolom voor chauffeur vinden in df_filtered. "
                f"Beschikbare kolommen: {list(df_filtered.columns)}"
            )
        else:
            grp = (
                df_filtered
                .groupby(COL_NAAM, dropna=False)
                .size()
                .sort_values(ascending=False)
                .reset_index(name="aantal")
                .rename(columns={COL_NAAM: "chauffeur_raw"})
            )

            if grp.empty:
                st.info("Geen schadegevallen binnen de huidige filters.")
            else:
                totaal_schades = int(grp["aantal"].sum())
                aantal_ch = int(grp.shape[0])

                c1, c2, c3 = st.columns(3)
                with c1:
                    st.metric("Aantal chauffeurs (met schade)", aantal_ch)
                c2.metric("Gemiddeld aantal schades", round(totaal_schades / max(1, aantal_ch), 2))
                c3.metric("Totaal aantal schades", totaal_schades)

                st.markdown("---")

                if COL_NAAM_DISP and COL_NAAM_DISP in df_filtered.columns:
                    disp_map = (
                        df_filtered[[COL_NAAM, COL_NAAM_DISP]]
                        .dropna()
                        .drop_duplicates()
                        .set_index(COL_NAAM)[COL_NAAM_DISP]
                        .to_dict()
                    )
                else:
                    disp_map = {}

                st.markdown("#### Handmatig aantal chauffeurs")
                handmatig_aantal = st.number_input(
                    "Handmatig aantal chauffeurs",
                    min_value=1, value=598, step=1
                )
                gem_schades_handmatig = round(totaal_schades / max(1, handmatig_aantal), 2)
                col_m, _ = st.columns([1, 2])
                with col_m:
                    st.metric("Gemiddeld aantal schades (handmatig)", gem_schades_handmatig)

                st.markdown("---")

            from functools import lru_cache
            @lru_cache(maxsize=None)
            def _badge_safe(raw):
                try:
                    b = badge_van_chauffeur(raw)
                    return b or ""
                except Exception:
                    return ""

            for _, row in grp.iterrows():
                raw = str(row["chauffeur_raw"])
                disp = disp_map.get(raw, raw)
                badge = _badge_safe(raw)
                st.markdown(f"**{badge}{disp}** — {int(row['aantal'])} schadegevallen")

    # ===== Tab 2: Voertuig =====
    with voertuig_tab:
        st.subheader("🚘 Schadegevallen per voertuigtype")

        if "BusTram_disp" not in df_filtered.columns:
            st.info("Kolom voor voertuigtype niet gevonden.")
        else:
            counts = df_filtered["BusTram_disp"].value_counts(dropna=False)
            if counts.empty:
                st.info("Geen schadegevallen binnen de huidige filters.")
            else:
                c1, c2 = st.columns(2)
                c1.metric("Unieke voertuigtypes", int(counts.shape[0]))
                c2.metric("Totaal schadegevallen", int(len(df_filtered)))

                st.markdown("### 📊 Samenvatting per voertuigtype")
                sum_df = counts.rename_axis("Voertuigtype").reset_index(name="Schades")
                st.dataframe(sum_df, use_container_width=True)

        st.markdown("### 📈 Schades per maand per voertuigtype")
        if {"Datum", "BusTram_disp"}.issubset(df_filtered.columns):
            work = df_filtered.copy()
            if work.empty:
                st.caption("Geen data binnen de huidige filters.")
            else:
                if "Maand" not in work.columns:
                    work["Maand"] = work["Datum"].dt.to_period("M").dt.to_timestamp()

                monthly = (
                    work.groupby(["Maand", "BusTram_disp"])
                        .size()
                        .rename("Schades")
                        .reset_index()
                )
                pivot = (
                    monthly.pivot(index="Maand", columns="BusTram_disp", values="Schades")
                           .sort_index()
                )
                full_idx = pd.period_range(
                    work["Datum"].min().to_period("M"),
                    work["Datum"].max().to_period("M"),
                    freq="M"
                ).to_timestamp()
                pivot = pivot.reindex(full_idx).fillna(0).astype(int)
                st.line_chart(pivot, use_container_width=True)
        else:
            st.caption("Kolommen 'Datum' en/of 'BusTram_disp' ontbreken voor de grafiek.")

    # ===== Tab 3: Locatie =====

    # ===== Tab 3: Locatie =====
    with locatie_tab:
        st.subheader("📍 Schadegevallen per locatie")

        if "Locatie_disp" not in df_filtered.columns:
            st.warning("⚠️ Kolom 'Locatie' niet gevonden in de huidige selectie.")
        else:
            loc_options = sorted([x for x in df_filtered["Locatie_disp"].dropna().unique().tolist() if str(x).strip()])
            gekozen_locs = st.multiselect(
                "Zoek locatie(s)",
                options=loc_options,
                default=[],
                placeholder="Type om te zoeken…",
                key="loc_ms"
            )

            work = df_filtered.copy()
            work["dienstnummer_s"] = work["dienstnummer"].astype(str)
            if gekozen_locs:
                work = work[work["Locatie_disp"].isin(gekozen_locs)]

            if work.empty:
                st.info("Geen resultaten binnen de huidige filters/keuze.")
            else:
                col_top1, _ = st.columns(2)
                with col_top1:
                    min_schades = st.number_input("Min. aantal schades", min_value=1, value=1, step=1, key="loc_min")

                agg = (
                    work.groupby("Locatie_disp")
                        .agg(Schades=("dienstnummer_s","size"),
                             Unieke_chauffeurs=("dienstnummer_s","nunique"))
                        .reset_index().rename(columns={"Locatie_disp":"Locatie"})
                )

                dmin = work.groupby("Locatie_disp")["Datum"].min().rename("Eerste")
                dmax = work.groupby("Locatie_disp")["Datum"].max().rename("Laatste")
                agg = agg.merge(dmin, left_on="Locatie", right_index=True, how="left")
                agg = agg.merge(dmax, left_on="Locatie", right_index=True, how="left")

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
                    agg_view["Periode"] = agg_view.apply(
                        lambda r: f"{r['Eerste']:%d-%m-%Y} – {r['Laatste']:%d-%m-%Y}"
                        if pd.notna(r["Eerste"]) and pd.notna(r["Laatste"]) else "—",
                        axis=1
                    )

                    # ⬇️ NIEUW: Link-kolom (meest recente link per locatie)
                    link_available = "Link" in work.columns
                    if link_available:
                        work = work.copy()
                        work["URL"] = work["Link"].apply(extract_url)
                        latest_idx = (
                            work.sort_values("Datum")
                                .groupby("Locatie_disp")["Datum"]
                                .idxmax()
                        )
                        link_map = work.loc[latest_idx].set_index("Locatie_disp")["URL"].to_dict()
                        agg_view["Link"] = agg_view["Locatie"].map(link_map)

                    # kolommen tonen
                    cols_show = ["Locatie","Schades","Unieke_chauffeurs","Periode"] + (["Link"] if link_available else [])

                    # klikbare kolomconfig
                    column_config = {
                        "Locatie": st.column_config.TextColumn("Locatie"),
                        "Schades": st.column_config.NumberColumn("Schades"),
                        "Unieke_chauffeurs": st.column_config.NumberColumn("Unieke chauffeurs"),
                        "Periode": st.column_config.TextColumn("Periode"),
                    }
                    if link_available:
                        column_config["Link"] = st.column_config.LinkColumn("Link", display_text="openen")

                    st.dataframe(
                        agg_view[cols_show].sort_values("Schades", ascending=False).reset_index(drop=True),
                        use_container_width=True,
                        column_config=column_config
                    )

                    # downloadknop
                    st.download_button(
                        "⬇️ Download samenvatting (CSV)",
                        agg_view[cols_show].to_csv(index=False).encode("utf-8"),
                        file_name="locaties_samenvatting.csv",
                        mime="text/csv",
                        key="dl_loc_summary"
                    )


    
    # ===== Tab 4: Opzoeken =====
    with opzoeken_tab:
        st.subheader("🔎 Opzoeken op personeelsnummer")

        zoek = st.text_input("Personeelsnummer (dienstnummer)", placeholder="bv. 41092", key="zoek_pnr_input")
        m = re.findall(r"\d+", str(zoek or "").strip())
        pnr = m[0] if m else ""

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
                st.error("❌ Helaas, die chauffeur bestaat nog niet. Probeer opnieuw.")

            try:
                s = str(naam_raw or "").strip()
                patroon = rf"^\s*({re.escape(pnr)}|\d+)\s*[-:–—]?\s*"
                naam_clean = re.sub(patroon, "", s)
            except Exception:
                naam_clean = naam_disp

            chauffeur_label = f"{pnr} {naam_clean}".strip() if naam_clean else str(pnr)

            set_lopend   = set(map(str, st.session_state.get("coaching_ids", set())))
            set_voltooid = {p for p,info in st.session_state.get("excel_info", {}).items() if (info or {}).get("status") == "Voltooid"}

            if pnr in set_voltooid:
                beo_raw = (st.session_state.get("excel_info", {}).get(pnr, {}) or {}).get("beoordeling", "")
                b = str(beo_raw or "").strip().lower()
                if b in {"zeer goed", "goed"}:
                    status_lbl, status_emoji = "Goed", "🟢"
                elif b == "voldoende":
                    status_lbl, status_emoji = "Voldoende", "🟠"
                elif b in {"onvoldoende", "slecht", "zeer slecht"}:
                    status_lbl, status_emoji = ("Onvoldoende" if b == "onvoldoende" else "Slecht"), "🔴"
                else:
                    status_lbl, status_emoji = "Voltooid (geen beoordeling)", "🟡"
            elif pnr in set_lopend:
                status_lbl, status_emoji = "Lopend", "⚫"
            else:
                status_lbl, status_emoji = "Niet aangevraagd", "⚪"

            st.markdown(f"**👤 Chauffeur:** {chauffeur_label}")
            st.markdown(f"**🧑‍💼 Teamcoach:** {teamcoach_disp}")

            # Datum coaching
            coaching_rows = []
            coach_df = st.session_state.get("coachings_df")
            if (isinstance(coach_df, pd.DataFrame) and not coach_df.empty
                    and {"dienstnummer", "Datum coaching"}.issubset(set(coach_df.columns))):
                mask = coach_df["dienstnummer"].astype(str).str.strip() == str(pnr).strip()
                rows = coach_df.loc[mask, ["Datum coaching", "Beoordeling"]].copy()
                if not rows.empty:
                    rows["Datum coaching"] = pd.to_datetime(rows["Datum coaching"], errors="coerce", dayfirst=True)
                    rows = rows.dropna(subset=["Datum coaching"])
                    from helpers import _beoordeling_emoji  # lokale import
                    for _, r in rows.iterrows():
                        dstr = r["Datum coaching"].strftime("%d-%m-%Y")
                        rate = str(r.get("Beoordeling", "") or "").strip().lower()
                        dot = _beoordeling_emoji(rate).strip() or ""
                        coaching_rows.append((dstr, dot))

            if not coaching_rows:
                coaching_dates = []
                ex_info = st.session_state.get("excel_info", {})
                if pnr in ex_info:
                    raw = (
                        (ex_info[pnr] or {}).get("coaching_datums")
                        or (ex_info[pnr] or {}).get("Datum coaching")
                        or (ex_info[pnr] or {}).get("datum_coaching")
                    )
                    import re as _re
                    if isinstance(raw, (list, tuple, set)):
                        coaching_dates = [str(x).strip() for x in raw if str(x).strip()]
                    elif isinstance(raw, str) and raw.strip():
                        coaching_dates = _re.split(r"[;,]\s*", raw.strip())

                if coaching_dates:
                    dot = status_emoji if status_emoji in {"🟢","🟠","🔴","🟡","⚫"} else ""
                    coaching_rows = [(d, dot) for d in coaching_dates]

            if coaching_rows:
                st.markdown("**📅 Datum coaching:**")
                coaching_rows.sort(key=lambda t: datetime.strptime(t[0], "%d-%m-%Y"))
                for d, dot in coaching_rows:
                    st.markdown(f"- {dot} {d}".strip())
            else:
                st.markdown("**📅 Datum coaching:** —")

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
            set_voltooid_all = {p for p,info in st.session_state.get("excel_info", {}).items() if (info or {}).get("status") == "Voltooid"}

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
                rows = []
                for p in sorted(map(str, pnrs_set)):
                    nm = _naam(p)
                    nm_badged = f"{badge_van_chauffeur(f'{p} - {nm}')}{nm}"
                    rows.append({
                        "Dienstnr": p,
                        "Naam": nm_badged,
                        "Status (coachinglijst)": _status_volledig(p)
                    })
                return pd.DataFrame(rows).sort_values(["Naam"]).reset_index(drop=True)

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
            st.markdown("## 🚩 schades en niet gepland voor coaching")
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

            rows = [{
                "Dienstnr": p,
                "Naam": f"{badge_van_chauffeur(f'{p} - {_naam(p)}')}{_naam(p)}",
                "Schades": int(pnr_counts.get(p, 0)),
                "Status (coachinglijst)": "Niet aangevraagd",
            } for p in sorted(result_set, key=lambda x: (-pnr_counts.get(x, 0), x))]

            df_no_coach = (
                pd.DataFrame(rows)
                  .sort_values(["Schades","Naam"], ascending=[False,True])
                  .reset_index(drop=True)
                if rows else
                pd.DataFrame(columns=["Dienstnr","Naam","Schades","Status (coachinglijst)"])
            )

            with st.expander(f"🟥 > {thr} schades en niet gepland in coaching ({len(result_set)})", expanded=True):
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
