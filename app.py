from __future__ import annotations

import json
import streamlit as st
import pandas as pd
from ftp_storage import ftp_download_text

st.set_page_config(page_title="Chauffeur Dashboard", layout="wide")

@st.cache_data(ttl=300)
def load_personeelsfiche_json():
    cfg = st.secrets["FTP"]
    host = cfg["host"]
    port = int(cfg.get("port", 21))
    username = cfg["username"]
    password = cfg["password"]
    base_dir = cfg["base_dir"].rstrip("/")

    remote_path = f"{base_dir}/personeelsficheGB.json"
    txt = ftp_download_text(host, port, username, password, remote_path)
    return json.loads(txt)

def normalize_pnr(x) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s

def find_person_record(data, personeelsnummer: str):
    """
    Probeert een record te vinden voor personeelsnummer in verschillende JSON vormen:
    - list[dict]
    - dict keyed by pnr
    - nested dict/list structuren
    """
    target = normalize_pnr(personeelsnummer)

    if isinstance(data, dict):
        if target in data and isinstance(data[target], dict):
            return data[target]
        for v in data.values():
            rec = find_person_record(v, target)
            if rec:
                return rec
        return None

    if isinstance(data, list):
        keys = ["personeelsnummer", "Personeelsnummer", "pnr", "PNR", "personnelNumber", "matricule"]
        for item in data:
            if isinstance(item, dict):
                for k in keys:
                    if k in item and normalize_pnr(item.get(k)) == target:
                        return item
        return None

    return None


st.title("🚍 Chauffeur Dashboard")

with st.sidebar:
    if st.button("🔄 Herlaad JSON"):
        st.cache_data.clear()

data = load_personeelsfiche_json()

# Zoekbalk bovenaan
pnr_input = st.text_input("Zoek op personeelsnummer", placeholder="bv. 12345")

if not pnr_input.strip():
    st.info("Geef een personeelsnummer in om de personeelsfiche te tonen.")
    st.stop()

pnr = normalize_pnr(pnr_input)
record = find_person_record(data, pnr)

st.header("Persoonlijke gegevens")

if record:
    # Mooie key/value weergave
    df = pd.DataFrame([{"Veld": k, "Waarde": v} for k, v in record.items()])
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.error("Geen record gevonden voor dit personeelsnummer in personeelsficheGB.json.")
