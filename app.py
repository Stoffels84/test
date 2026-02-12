# app.py
# ============================================================
# CHAUFFEUR DASHBOARD (FTP)
# ------------------------------------------------------------
# SECTIES IN DE APP (van boven naar beneden):
#   0) Config + helpers + FTP config
#   1) Data loaders (JSON + Excel via FTP)
#   2) UI: Titel + zoekbalk (personeelsnummer)
#   3) UI: Persoonlijke gegevens (uit personeelsficheGB.json)
#   4) UI: Dienst van vandaag (steekkaart/yyyymmdd*.xlsx, sheet: Dienstlijst)
#   5) UI: Schade (schade met macro.xlsm, sheet: BRON)  <-- ONDERAAN
# ============================================================

from __future__ import annotations

import json
from datetime import date
from io import BytesIO

import pandas as pd
import streamlit as st

from ftp_storage import (
    ftp_download_bytes,
    ftp_download_text,
    ftp_list_files,
)

# ============================================================
# 0) CONFIG + HELPERS + FTP CONFIG
# ============================================================

st.set_page_config(page_title="Chauffeur Dashboard", layout="wide")


def normalize_pnr(x) -> str:
    """Normaliseer nummers (bv. 123.0 -> 123) en strip spaties."""
    if x is None:
        return ""
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def get_ftp_cfg():
    """
    Verwacht Streamlit secrets:
    [FTP]
    host="..."
    port=21
    username="..."
    password="..."
    base_dir="/pad/naar/map"  (optioneel)
    """
    cfg = st.secrets["FTP"]
    host = cfg["host"]
    port = int(cfg.get("port", 21))
    username = cfg["username"]
    password = cfg["password"]
    base_dir = str(cfg.get("base_dir", "")).strip()
    return host, port, username, password, base_dir


def join_remote(base_dir: str, *parts: str) -> str:
    """Maak een remote pad dat werkt met/zonder base_dir."""
    base_dir = str(base_dir or "").strip().strip("/")
    clean_parts = [p.strip().strip("/") for p in parts if str(p).strip() != ""]
    if base_dir == "":
        return "/".join(clean_parts) if clean_parts else ""
    return "/".join([base_dir] + clean_parts)


# ============================================================
# 1) DATA LOADERS (FTP)
# ============================================================

# ---------- 1A) JSON: personeelsficheGB.json ----------

@st.cache_data(ttl=300)
def load_personeelsfiche_json():
    host, port, username, password, base_dir = get_ftp_cfg()
    remote_path = join_remote(base_dir, "personeelsficheGB.json")
    txt = ftp_download_text(host, port, username, password, remote_path)
    return json.loads(txt)


def find_person_record(data, personeelnummer: str):
    """
    Zoek record in JSON op personeelnummer/personeelsnummer.
    Ondersteunt list/dict/nested structuren.
    """
    target = normalize_pnr(personeelnummer)

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                for key in ["personeelnummer", "personeelsnummer", "pnr", "PNR", "Personeelnummer", "Personeelsnummer"]:
                    if key in item and normalize_pnr(item.get(key)) == target:
                        return item
        return None

    if isinstance(data, dict):
        # dict keyed by nummer
        if target in data and isinstance(data[target], (dict, list, str, int, float)):
            return data[target] if isinstance(data[target], dict) else {"waarde": data[target]}

        # scan nested
        for v in data.values():
            rec = find_person_record(v, target)
            if rec:
                return rec
        return None

    return None


# ---------- 1B) Dienst van vandaag: steekkaart/yyyymmdd*.xlsx (sheet Dienstlijst) ----------

@st.cache_data(ttl=120)
def load_dienst_vandaag_df() -> pd.DataFrame:
    host, port, username, password, base_dir = get_ftp_cfg()

    # map steekkaart onder base_dir
    steekkaart_dir = join_remote(base_dir, "steekkaart")

    today_prefix = date.today().strftime("%Y%m%d")  # yyyymmdd
    files = ftp_list_files(host, port, username, password, steekkaart_dir)

    # kies excel die start met yyyymmdd
    matches = [f for f in files if f.startswith(today_prefix) and f.lower().endswith((".xlsx", ".xls"))]
    if not matches:
        raise FileNotFoundError(f"Geen dienstbestand gevonden in '{steekkaart_dir}' dat start met {today_prefix}")

    matches.sort()
    filename = matches[0]
    remote_path = f"{steekkaart_dir.rstrip('/')}/{filename}"

    b = ftp_download_bytes(host, port, username, password, remote_path)

    df = pd.read_excel(BytesIO(b), sheet_name="Dienstlijst", engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]

    # We filteren in deze excel op 'personeelnummer' (zonder s)
    wanted = [
        "personeelnummer",
        "naam",
        "Dienstadres",
        "Uur",
        "Plaats",
        "richting",
        "Loop",
        "Lijn",
        "voertuig",
        "wissel",
        "door appel",
        "chauffeur appel",
    ]

    # case-insensitive selectie
    col_map = {c.lower(): c for c in df.columns}
    selected = []
    missing = []
    for w in wanted:
        k = w.lower()
        if k in col_map:
            selected.append(col_map[k])
        else:
            missing.append(w)

    if not selected:
        raise KeyError(f"Geen verwachte kolommen gevonden in Dienstlijst. Kolommen: {list(df.columns)}")

    df = df[selected].copy()
    df.attrs["missing_columns"] = missing
    df.attrs["source_file"] = filename
    return df


# ---------- 1C) Schade: schade met macro.xlsm (sheet BRON) ----------

@st.cache_data(ttl=300)
def load_schade_bron_df() -> pd.DataFrame:
    host, port, username, password, base_dir = get_ftp_cfg()

    remote_path = join_remote(base_dir, "schade met macro.xlsm")
    b = ftp_download_bytes(host, port, username, password, remote_path)

    df = pd.read_excel(BytesIO(b), sheet_name="BRON", engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]

    wanted = ["personeelsnummer", "Datum", "Link", "Locatie", "voertuig", "Bus/tram", "Type"]

    # case-insensitive selectie
    col_map = {c.lower(): c for c in df.columns}
    selected = []
    missing = []
    for w in wanted:
        k = w.lower()
        if k in col_map:
            selected.append(col_map[k])
        else:
            missing.append(w)

    if not selected:
        raise KeyError(f"Geen verwachte kolommen gevonden in BRON. Kolommen: {list(df.columns)}")

    out = df[selected].copy()
    out.attrs["missing_columns"] = missing

    # datum parseren als aanwezig
    if "Datum" in out.columns:
        out["Datum"] = pd.to_datetime(out["Datum"], errors="coerce")

    return out


# ============================================================
# 2) UI: TITEL + ZOEKBALK (bovenaan)
# ============================================================

st.title("🚍 Chauffeur Dashboard")

with st.sidebar:
    st.header("Data")
    if st.button("🔄 Herlaad alles (cache leegmaken)"):
        st.cache_data.clear()

# Zoekbalk bovenaan
pnr_input = st.text_input("Zoek op personeelsnummer", placeholder="bv. 12345")
if not pnr_input.strip():
    st.info("Geef een personeelsnummer in om de fiche, dienst en schade te tonen.")
    st.stop()

pnr = normalize_pnr(pnr_input)

# ============================================================
# 3) UI: PERSOONLIJKE GEGEVENS (JSON)
# ============================================================

st.header("Persoonlijke gegevens")

try:
    data_json = load_personeelsfiche_json()
    person = find_person_record(data_json, pnr)

    if person:
        df_person = pd.DataFrame([{"Veld": k, "Waarde": v} for k, v in person.items()])
        st.dataframe(df_person, use_container_width=True, hide_index=True)
    else:
        st.warning("Geen persoonlijke fiche gevonden voor dit personeelsnummer.")

except Exception as e:
    st.error(f"Fout bij laden personeelsficheGB.json: {e}")

st.divider()

# ============================================================
# 4) UI: DIENST VAN VANDAAG (steekkaart)
# ============================================================

st.header("Dienst van vandaag")

try:
    dienst_df = load_dienst_vandaag_df()

    # waarschuwing voor ontbrekende kolommen
    missing = dienst_df.attrs.get("missing_columns", [])
    if missing:
        st.warning(f"Ontbrekende kolommen in Dienstlijst (niet getoond): {', '.join(missing)}")

    # filter op personeelnummer (zonder s)
    if "personeelnummer" not in dienst_df.columns:
        st.error(f"Kolom 'personeelnummer' ontbreekt. Gevonden: {list(dienst_df.columns)}")
        st.stop()

    dienst_df["personeelnummer"] = dienst_df["personeelnummer"].astype(str).map(normalize_pnr)
    dienst_rows = dienst_df[dienst_df["personeelnummer"] == pnr].copy()

    source_file = dienst_df.attrs.get("source_file", "")
    if source_file:
        st.caption(f"Bronbestand: {source_file}")

    if dienst_rows.empty:
        st.info("Geen dienst gevonden voor vandaag voor dit personeelnummer.")
    else:
        st.dataframe(dienst_rows, use_container_width=True, hide_index=True)

except FileNotFoundError as e:
    st.error(str(e))
except Exception as e:
    st.error(f"Fout bij laden dienst: {e}")

st.divider()

# ============================================================
# 5) UI: SCHADE (ONDERAAN) - schade met macro.xlsm / BRON
# ============================================================

st.header("Schade (BRON)")

try:
    schade_df = load_schade_bron_df()

    missing = schade_df.attrs.get("missing_columns", [])
    if missing:
        st.warning(f"Ontbrekende kolommen in BRON (niet getoond): {', '.join(missing)}")

    if "personeelsnummer" not in schade_df.columns:
        st.error(f"Kolom 'personeelsnummer' ontbreekt in BRON. Gevonden: {list(schade_df.columns)}")
        st.stop()

    schade_df["personeelsnummer"] = schade_df["personeelsnummer"].astype(str).map(normalize_pnr)
    rows = schade_df[schade_df["personeelsnummer"] == pnr].copy()

    # sorteer op datum (nieuwste eerst) als Datum aanwezig is
    if "Datum" in rows.columns:
        rows = rows.sort_values("Datum", ascending=False)

    if rows.empty:
        st.info("Geen schades gevonden voor dit personeelsnummer.")
    else:
        # Link klikbaar tonen
        if "Link" in rows.columns:
            rows["Link"] = rows["Link"].astype(str)
            st.dataframe(
                rows,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Link": st.column_config.LinkColumn("Link", display_text="Open"),
                },
            )
        else:
            st.dataframe(rows, use_container_width=True, hide_index=True)

except Exception as e:
    st.error(f"Fout bij laden schade (BRON): {e}")
