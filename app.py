from __future__ import annotations

import json
import streamlit as st
import pandas as pd
from ftp_storage import ftp_download_bytes, ftp_download_text

st.set_page_config(page_title="Chauffeur Dashboard", layout="wide")

@st.cache_data(ttl=300)
def load_excels_from_ftp() -> dict[str, pd.DataFrame]:
    cfg = st.secrets["FTP"]
    host = cfg["host"]
    port = int(cfg.get("port", 21))
    username = cfg["username"]
    password = cfg["password"]
    base_dir = cfg["base_dir"].rstrip("/")
    files = cfg["files"]

    dfs: dict[str, pd.DataFrame] = {}
    for fname in files:
        remote_path = f"{base_dir}/{fname}"
        data = ftp_download_bytes(host, port, username, password, remote_path)
        df = pd.read_excel(pd.io.common.BytesIO(data), engine="openpyxl")
        dfs[fname] = df
    return dfs

@st.cache_data(ttl=300)
def load_personeelsfiche_json() -> object:
    cfg = st.secrets["FTP"]
    host = cfg["host"]
    port = int(cfg.get("port", 21))
    username = cfg["username"]
    password = cfg["password"]
    base_dir = cfg["base_dir"].rstrip("/")

    remote_path = f"{base_dir}/personeelsficheGB.json"
    txt = ftp_download_text(host, port, username, password, remote_path)
    return json.loads(txt)

def normalize_pnr(value) -> str:
    # Zorgt dat 00123 en 123 consistent worden behandeld
    if value is None:
        return ""
    s = str(value).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s

def find_pnr_in_all_excels(dfs: dict[str, pd.DataFrame], personeelsnummer: str) -> dict:
    """
    Zoekt personeelsnummer in alle Excels.
    Return: dict met per bestand: kolommen waarin match gevonden werd + aantal rijen.
    """
    target = normalize_pnr(personeelsnummer)
    results = {}

    for name, df in dfs.items():
        found_cols = []
        hits = 0

        # Check elke kolom: converteer naar string en vergelijk exact (snel + betrouwbaar)
        for col in df.columns:
            series = df[col].astype(str).map(normalize_pnr)
            mask = series == target
            c_hits = int(mask.sum())
            if c_hits > 0:
                found_cols.append((col, c_hits))
                hits += c_hits

        if hits > 0:
            results[name] = {
                "total_hits": hits,
                "columns": found_cols,
            }

    return results

def find_person_in_json(data: object, personeelsnummer: str) -> dict | None:
    """
    Probeert een record te vinden in personeelsficheGB.json.
    Ondersteunt:
      - lijst van dicts
      - dict keyed op personeelsnummer
      - dict met nested lijsten
    """
    target = normalize_pnr(personeelsnummer)

    # Case 1: dict keyed by pnr
    if isinstance(data, dict):
        if target in data and isinstance(data[target], dict):
            return data[target]

        # Case 2: scan values als lijsten/dicts
        for v in data.values():
            rec = find_person_in_json(v, target)
            if rec:
                return rec
        return None

    # Case 3: list of dicts
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                # probeer veel voorkomende sleutelvarianten
                for key in ["personeelsnummer", "Personeelsnummer", "pnr", "PNR", "personnelNumber", "matricule"]:
                    if key in item and normalize_pnr(item.get(key)) == target:
                        return item
        return None

    return None


# =========================
# UI
# =========================
st.title("🚍 Chauffeur Dashboard")

with st.sidebar:
    st.header("Data")
    if st.button("🔄 Herlaad data (cache leegmaken)"):
        st.cache_data.clear()

dfs = load_excels_from_ftp()
pers_json = load_personeelsfiche_json()

# ---- TOP ZOEKBALK ----
pnr_input = st.text_input("Zoek op personeelsnummer", placeholder="bv. 12345", key="pnr_search")

if not pnr_input.strip():
    st.info("Geef een personeelsnummer in om te zoeken in alle Excel-bestanden en de personeelsfiche te tonen.")
    st.stop()

pnr = normalize_pnr(pnr_input)

# Resultaten uit Excel
excel_hits = find_pnr_in_all_excels(dfs, pnr)

# Persoonsgegevens uit JSON
person = find_person_in_json(pers_json, pnr)

# ---- OVERVIEW BLOK ----
c1, c2 = st.columns([1, 2])
with c1:
    st.subheader("Resultaat")
    st.metric("Personeelsnummer", pnr)
    st.metric("Excel-bestanden met hits", len(excel_hits))

with c2:
    if excel_hits:
        st.write("**Gevonden in:**")
        for fname, info in excel_hits.items():
            cols_txt = ", ".join([f"{col} ({cnt})" for col, cnt in info["columns"]])
            st.write(f"- {fname}: {info['total_hits']} hits → {cols_txt}")
    else:
        st.warning("Geen matches gevonden in de Excel-bestanden voor dit personeelsnummer.")

st.divider()

# =========================
# 1) TITEL: Persoonlijke gegevens
# =========================
st.header("Persoonlijke gegevens")

if person:
    # Toon “mooi” als key/value tabel
    # (Je kan dit later mappen naar vaste velden en een strakke layout maken)
    pretty = pd.DataFrame(
        [{"Veld": k, "Waarde": v} for k, v in person.items()]
    )
    st.dataframe(pretty, use_container_width=True, hide_index=True)
else:
    st.error("Geen record gevonden in personeelsficheGB.json voor dit personeelsnummer.")
