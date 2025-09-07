"""Data loading utilities for the Schade Dashboard.

Exposes:
- load_schade_prepared(path="schade met macro.xlsm", sheet="BRON") -> (DataFrame, options dict)
- lees_coachingslijst(pad="Coachingslijst.xlsx") -> (ids_geel, ids_blauw, total_geel_rows, total_blauw_rows, excel_info, warn)
"""
from __future__ import annotations

import re
import pandas as pd
import streamlit as st

# =========================
# Data laden / voorbereiden
# =========================
@st.cache_data(show_spinner=False, ttl=3600)
def load_schade_prepared(path: str = "schade met macro.xlsm", sheet: str = "BRON"):
    """Load base schade-data efficiently and prepare derived columns used across the app.

    Returns a tuple: (df_ok, options)
    - df_ok: cleaned dataframe with derived columns (dienstnummer, KwartaalP, Kwartaal, Maand, *_disp)
    - options: dict for filter controls (teamcoach, locatie, voertuig, kwartaal, min_datum, max_datum)
    """
    # Lees enkel de nodige kolommen (scheelt I/O en RAM)
    usecols = ["Datum", "volledige naam", "teamcoach", "Locatie", "Bus/ Tram", "Link"]
    df_raw = pd.read_excel(path, sheet_name=sheet, usecols=usecols)
    df_raw.columns = df_raw.columns.str.strip()

    # Datum in twee passes (met fallback)
    d1 = pd.to_datetime(df_raw["Datum"], errors="coerce", dayfirst=True)
    need_retry = d1.isna()
    if need_retry.any():
        d2 = pd.to_datetime(df_raw.loc[need_retry, "Datum"], errors="coerce", dayfirst=False)
        d1.loc[need_retry] = d2
    df_raw["Datum"] = d1
    df_ok = df_raw[df_raw["Datum"].notna()].copy()

    # String cleanup één keer
    for col in ("volledige naam", "teamcoach", "Locatie", "Bus/ Tram", "Link"):
        if col in df_ok.columns:
            df_ok[col] = df_ok[col].astype("string").str.strip()

    # Afgeleiden: filters/aggregaties
    df_ok["dienstnummer"]   = df_ok["volledige naam"].astype(str).str.extract(r"^(\d+)", expand=False)\
                                 .astype("string").str.strip()
    df_ok["dienstnummer_s"] = df_ok["dienstnummer"].astype("string")  # vaak nodig → cache als string
    df_ok["KwartaalP"]      = df_ok["Datum"].dt.to_period("Q")
    df_ok["Kwartaal"]       = df_ok["KwartaalP"].astype(str)
    df_ok["Maand"]          = df_ok["Datum"].dt.to_period("M").dt.to_timestamp()

    def _clean_display_series(s: pd.Series) -> pd.Series:
        s = s.astype("string").str.strip()
        bad = s.isna() | s.eq("") | s.str.lower().isin({"nan","none","<na>"})
        return s.mask(bad, "onbekend")

    # Categorical kolommen → sneller .isin/.unique en minder geheugen
    df_ok["volledige naam_disp"] = _clean_display_series(df_ok["volledige naam"]).astype("category")
    df_ok["teamcoach_disp"]      = _clean_display_series(df_ok["teamcoach"]).astype("category")
    df_ok["Locatie_disp"]        = _clean_display_series(df_ok["Locatie"]).astype("category")
    df_ok["BusTram_disp"]        = _clean_display_series(df_ok["Bus/ Tram"]).astype("category")

    options = {
        "teamcoach": sorted(df_ok["teamcoach_disp"].cat.categories.astype(str).tolist()),
        "locatie":   sorted(df_ok["Locatie_disp"].cat.categories.astype(str).tolist()),
        "voertuig":  sorted(df_ok["BusTram_disp"].cat.categories.astype(str).tolist()),
        "kwartaal":  sorted(df_ok["KwartaalP"].dropna().astype(str).unique().tolist()),
        "min_datum": df_ok["Datum"].min().normalize(),
        "max_datum": df_ok["Datum"].max().normalize(),
    }

    # Kolomnamen normaliseren (éénmalig)
    df_ok.columns = (
        df_ok.columns.astype(str)
             .str.normalize("NFKC")
             .str.strip()
    )
    return df_ok, options


# ========= Coachingslijst inlezen =========
@st.cache_data(show_spinner=False)
def lees_coachingslijst(pad: str = "Coachingslijst.xlsx"):
    """Read the coaching workbook and build helper structures.

    Returns:
        ids_geel (set[str])           – dienstnummers met status Voltooid
        ids_blauw (set[str])          – dienstnummers met status Coaching (lopend)
        total_geel_rows (int)         – ruwe rijen in 'voltooide coachings'
        total_blauw_rows (int)        – ruwe rijen in 'coaching'
        excel_info (dict[str, dict])  – per pnr: naam/teamcoach/status/beoordeling/coaching_datums
        warn (str|None)               – waarschuwingsboodschap of None
    """
    ids_geel, ids_blauw = set(), set()
    total_geel_rows, total_blauw_rows = 0, 0
    excel_info: dict[str, dict] = {}
    df_voltooide_clean = None
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
    date_hints      = ["datum coaching", "datumcoaching", "coaching datum", "datum"]

    def lees_sheet(sheetnaam: str, status_label: str):
        ids = set()
        total_rows = 0
        df_small = None
        try:
            dfc = pd.read_excel(xls, sheet_name=sheetnaam)
        except Exception:
            return ids, total_rows, None

        dfc.columns = dfc.columns.str.strip().str.lower()

        kol_pnr   = next((k for k in pnr_keys if k in dfc.columns), None)
        kol_full  = next((k for k in fullname_keys if k in dfc.columns), None)
        kol_vn    = next((k for k in voornaam_keys if k in dfc.columns), None)
        kol_an    = next((k for k in achternaam_keys if k in dfc.columns), None)
        kol_coach = next((k for k in coach_keys if k in dfc.columns), None)
        kol_rate  = next((k for k in rating_keys if k in dfc.columns), None)

        kol_date = None
        for hint in date_hints:
            if hint in dfc.columns:
                kol_date = hint
                break
        if not kol_date:
            for k in dfc.columns:
                if ("datum" in k) and ("coach" in k):
                    kol_date = k
                    break

        if kol_pnr is None:
            return ids, total_rows, None

        s_pnr = (
            dfc[kol_pnr].astype(str)
            .str.extract(r"(\d+)", expand=False)
            .dropna().str.strip()
        )
        total_rows = int(s_pnr.shape[0])
        ids = set(s_pnr.tolist())

        # Vul excel_info per rij
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
                naam = f"{vn} {an}".strip() or None
                if naam and naam.lower() in {"nan", "none", ""}:
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

            if kol_date:
                d_raw = dfc[kol_date].iloc[i]
                d = pd.to_datetime(d_raw, errors="coerce", dayfirst=True)
                if pd.notna(d):
                    lst = info.get("coaching_datums", [])
                    if d.strftime("%d-%m-%Y") not in lst:
                        lst.append(d.strftime("%d-%m-%Y"))
                    info["coaching_datums"] = lst

            excel_info[pnr] = info

        # Compacte DF per sheet (buiten de loop)
        if kol_date:
            df_small = dfc[[kol_pnr, kol_date]].copy()
            df_small.columns = ["dienstnummer", "Datum coaching"]
            df_small["dienstnummer"] = (
                df_small["dienstnummer"].astype(str).str.extract(r"(\d+)", expand=False).str.strip()
            )
            df_small["Datum coaching"] = pd.to_datetime(
                df_small["Datum coaching"], errors="coerce", dayfirst=True
            )
            if kol_rate:
                map_rate = {
                    "zeer goed": "zeer goed",
                    "goed": "goed",
                    "voldoende": "voldoende",
                    "onvoldoende": "onvoldoende",
                    "slecht": "slecht",
                    "zeer slecht": "zeer slecht",
                    "zeergoed": "zeer goed",
                    "zeerslecht": "zeer slecht",
                }
                df_small["Beoordeling"] = (
                    dfc[kol_rate].astype(str).str.strip().str.lower().replace(map_rate)
                )
            else:
                df_small["Beoordeling"] = None

        return ids, total_rows, df_small

    s_geel  = vind_sheet(xls, "voltooide coachings")
    s_blauw = vind_sheet(xls, "coaching")

    if s_geel:
        ids_geel,  total_geel_rows,  df_voltooide_clean = lees_sheet(s_geel,  "Voltooid")
    if s_blauw:
        ids_blauw, total_blauw_rows, _                 = lees_sheet(s_blauw, "Coaching")

    if isinstance(df_voltooide_clean, pd.DataFrame):
        st.session_state["coachings_df"] = df_voltooide_clean

    # normaliseer/unique datums per pnr
    for p, inf in excel_info.items():
        if "coaching_datums" in inf and isinstance(inf["coaching_datums"], list):
            try:
                dd = sorted(
                    set(inf["coaching_datums"]),
                    key=lambda x: pd.to_datetime(x, dayfirst=True, errors="coerce")
                )
            except Exception:
                dd = sorted(set(inf["coaching_datums"]))
            inf["coaching_datums"] = dd

    return ids_geel, ids_blauw, total_geel_rows, total_blauw_rows, excel_info, None


__all__ = [
    "load_schade_prepared",
    "lees_coachingslijst",
]
