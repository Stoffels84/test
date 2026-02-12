from __future__ import annotations

import streamlit as st
import pandas as pd
import json

from datetime import date
from io import BytesIO

# jouw FTP helpers
from ftp_storage import (
    ftp_download_text,
    ftp_download_bytes,
    ftp_list_files,
)

# =========================
# BASIS CONFIG
# =========================

st.set_page_config(
    page_title="Chauffeur Dashboard",
    layout="wide"
)

# =========================
# HELPERS
# =========================

def normalize_pnr(x) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


# =========================
# FTP CONFIG
# =========================

def get_ftp_cfg():
    cfg = st.secrets["FTP"]

    host = cfg["host"]
    port = int(cfg.get("port", 21))
    username = cfg["username"]
    password = cfg["password"]
    base_dir = str(cfg.get("base_dir", "")).strip()

    return host, port, username, password, base_dir


# =========================
# LOAD JSON
# =========================

@st.cache_data(ttl=300)
def load_personeelsfiche_json():

    host, port, username, password, base_dir = get_ftp_cfg()

    if base_dir:
        path = f"{base_dir.rstrip('/')}/personeelsficheGB.json"
    else:
        path = "personeelsficheGB.json"

    txt = ftp_download_text(host, port, username, password, path)

    return json.loads(txt)


# =========================
# FIND PERSON IN JSON
# =========================

def find_person_record(data, personeelsnummer):

    target = normalize_pnr(personeelsnummer)

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                for key in [
                    "personeelsnummer",
                    "Personeelsnummer",
                    "pnr",
                    "PNR"
                ]:
                    if key in item and normalize_pnr(item[key]) == target:
                        return item

    if isinstance(data, dict):

        if target in data:
            return data[target]

        for v in data.values():
            result = find_person_record(v, target)
            if result:
                return result

    return None


# =========================
# LOAD DIENST VAN VANDAAG
# =========================

@st.cache_data(ttl=120)
def load_dienst_vandaag_df():

    host, port, username, password, base_dir = get_ftp_cfg()

    if base_dir:
        steekkaart_dir = f"{base_dir.rstrip('/')}/steekkaart"
    else:
        steekkaart_dir = "steekkaart"

    today_prefix = date.today().strftime("%Y%m%d")

    files = ftp_list_files(
        host,
        port,
        username,
        password,
        steekkaart_dir
    )

    matches = [
        f for f in files
        if f.startswith(today_prefix)
        and f.lower().endswith((".xlsx", ".xls"))
    ]

    if not matches:
        raise FileNotFoundError(
            f"Geen dienstbestand gevonden voor vandaag ({today_prefix})"
        )

    matches.sort()

    filename = matches[0]

    remote_path = f"{steekkaart_dir}/{filename}"

    b = ftp_download_bytes(
        host,
        port,
        username,
        password,
        remote_path
    )

    df = pd.read_excel(
        BytesIO(b),
        sheet_name="Dienstlijst",
        engine="openpyxl"
    )

    df.columns = [str(c).strip() for c in df.columns]

    wanted = [
        "personeelsnummer",
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
        "chauffeur appel"
    ]

    col_map = {c.lower(): c for c in df.columns}

    selected_cols = []

    for w in wanted:
        key = w.lower()
        if key in col_map:
            selected_cols.append(col_map[key])

    df = df[selected_cols]

    return df


# =========================
# UI
# =========================

st.title("🚍 Chauffeur Dashboard")

# TOP ZOEKBALK
pnr_input = st.text_input(
    "Zoek op personeelsnummer",
    placeholder="bv. 12345"
)

if not pnr_input.strip():
    st.info("Geef een personeelsnummer in.")
    st.stop()

pnr = normalize_pnr(pnr_input)

# =========================
# 1) PERSOONLIJKE GEGEVENS
# =========================

st.header("Persoonlijke gegevens")

try:

    data = load_personeelsfiche_json()

    person = find_person_record(data, pnr)

    if person:

        df_person = pd.DataFrame([
            {"Veld": k, "Waarde": v}
            for k, v in person.items()
        ])

        st.dataframe(
            df_person,
            use_container_width=True,
            hide_index=True
        )

    else:
        st.warning("Geen persoonlijke fiche gevonden.")

except Exception as e:
    st.error(f"Fout bij laden personeelsfiche: {e}")


# =========================
# 2) DIENST VAN VANDAAG
# =========================

st.header("Dienst van vandaag")

try:

    dienst_df = load_dienst_vandaag_df()

    dienst_df["personeelsnummer"] = (
        dienst_df["personeelsnummer"]
        .astype(str)
        .map(normalize_pnr)
    )

    dienst_rows = dienst_df[
        dienst_df["personeelsnummer"] == pnr
    ]

    if len(dienst_rows) == 0:
        st.info("Geen dienst gevonden voor vandaag.")
    else:
        st.dataframe(
            dienst_rows,
            use_container_width=True,
            hide_index=True
        )

except Exception as e:
    st.error(f"Fout bij laden dienst: {e}")
