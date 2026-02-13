# ============================================================
# CHAUFFEUR DASHBOARD (Enterprise structuur + FTP + LOGIN)
# ============================================================

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from io import BytesIO
from typing import Optional
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from ftp_client import FTPConfig, FTPManager


# ============================================================
# 0) CONFIG + GLOBAL STYLE
# ============================================================

st.set_page_config(page_title="Chauffeur Dashboard", layout="wide")

st.markdown(
    """
<style>
:root { --fluo-green: #39FF14; }
h1,h2,h3{
color:var(--fluo-green)!important;
text-shadow:0 0 8px rgba(57,255,20,0.35);
}
</style>
""",
    unsafe_allow_html=True,
)

# ============================================================
# HELPERS
# ============================================================

BRUSSELS = ZoneInfo("Europe/Brussels")


def normalize_pnr(x) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def pick_col(df: pd.DataFrame, wanted_name: str) -> Optional[str]:
    w = wanted_name.strip().lower()
    for c in df.columns:
        if str(c).strip().lower() == w:
            return c
    return None


# ============================================================
# 🚍 BUS LOADING ANIMATION
# ============================================================

def loading_bus(message="Alles wordt geladen..."):
    html = f"""
<div class="bus-loader">
<div class="title">{message}</div>
<div class="road">
<div class="bus">🚌</div>
</div>
</div>

<style>
.bus-loader {{padding:10px}}
.title {{font-weight:700;margin-bottom:8px}}
.road {{
position:relative;height:40px;
border-radius:10px;
background:rgba(0,0,0,0.2);
overflow:hidden;
}}
.bus {{
position:absolute;
left:-60px;
top:50%;
transform:translateY(-50%);
font-size:26px;
animation:move 2s linear infinite;
}}
@keyframes move {{
0%{{left:-60px}}
100%{{left:calc(100% + 60px)}}
}}
</style>
"""
    components.html(html, height=90)


# ============================================================
# FTP MANAGER
# ============================================================

def require_ftp_secrets():
    cfg = st.secrets.get("FTP")
    if cfg is None:
        st.error("FTP config ontbreekt.")
        st.stop()
    return cfg


@st.cache_resource
def get_ftp_manager():
    cfg = require_ftp_secrets()
    return FTPManager(
        FTPConfig(
            host=cfg["host"],
            port=int(cfg.get("port", 21)),
            username=cfg["username"],
            password=cfg["password"],
            base_dir=str(cfg.get("base_dir", "")).strip(),
        )
    )


# ============================================================
# DATA LOADERS
# ============================================================

@st.cache_data(ttl=300)
def load_personeelsfiche_json():
    ftp = get_ftp_manager()
    return json.loads(ftp.download_text(ftp.join("personeelsficheGB.json")))


@st.cache_data(ttl=120)
def load_dienst_vandaag_df():
    ftp = get_ftp_manager()

    today_prefix = datetime.now(BRUSSELS).strftime("%Y%m%d")

    files = ftp.list_files(ftp.join("steekkaart"))
    matches = [f for f in files if f.startswith(today_prefix)]

    if not matches:
        raise FileNotFoundError("Geen dienstbestand vandaag.")

    matches.sort()
    filename = matches[-1]  # laatste versie

    b = ftp.download_bytes(f"steekkaart/{filename}")
    df = pd.read_excel(BytesIO(b), sheet_name="Dienstlijst", engine="openpyxl")

    df.columns = [str(c).strip() for c in df.columns]
    return df


@st.cache_data(ttl=300)
def load_schade_bron_df():
    ftp = get_ftp_manager()
    b = ftp.download_bytes(ftp.join("schade met macro.xlsm"))
    df = pd.read_excel(BytesIO(b), sheet_name="BRON", engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]
    return df


@st.cache_data(ttl=300)
def load_coaching_gepland_df():
    ftp = get_ftp_manager()
    b = ftp.download_bytes(ftp.join("Coachingslijst.xlsx"))
    return pd.read_excel(BytesIO(b), sheet_name="Coaching", engine="openpyxl")


@st.cache_data(ttl=300)
def load_coaching_voltooid_df():
    ftp = get_ftp_manager()
    b = ftp.download_bytes(ftp.join("Coachingslijst.xlsx"))
    return pd.read_excel(BytesIO(b), sheet_name="Voltooide coachings", engine="openpyxl")


@st.cache_data(ttl=300)
def load_gesprekken_df():
    ftp = get_ftp_manager()
    b = ftp.download_bytes(ftp.join("Overzicht gesprekken (aangepast).xlsx"))
    return pd.read_excel(BytesIO(b), sheet_name="gesprekken per thema", engine="openpyxl")


# ============================================================
# 🚀 BATCH LOAD (ALLE DATA IN 1 KEER)
# ============================================================

@st.cache_data(ttl=120)
def load_all_data():

    tasks = {
        "person_json": load_personeelsfiche_json,
        "dienst_df": load_dienst_vandaag_df,
        "schade_df": load_schade_bron_df,
        "coaching_gepland": load_coaching_gepland_df,
        "coaching_voltooid": load_coaching_voltooid_df,
        "gesprekken_df": load_gesprekken_df,
    }

    results = {}

    with ThreadPoolExecutor(max_workers=6) as ex:
        fut_map = {ex.submit(fn): key for key, fn in tasks.items()}
        for fut in as_completed(fut_map):
            results[fut_map[fut]] = fut.result()

    return results


# ============================================================
# UI
# ============================================================

st.title("🚍 Chauffeur Dashboard")

pnr_input = st.text_input("Zoek op personeelsnummer")

if not pnr_input.strip():
    st.stop()

pnr = normalize_pnr(pnr_input)

# 🚍 bus animatie tijdens laden
loader = st.empty()

with loader:
    loading_bus("Dashboard wordt geladen...")

bundle = load_all_data()

loader.empty()

data_json = bundle["person_json"]
dienst_df = bundle["dienst_df"]
schade_df = bundle["schade_df"]
gepland = bundle["coaching_gepland"]
voltooid = bundle["coaching_voltooid"]
gesprekken_df = bundle["gesprekken_df"]

# ============================================================
# UI SECTIES
# ============================================================

st.header("Persoonlijke gegevens")
st.write(data_json)

st.header("Dienst vandaag")
st.dataframe(dienst_df)

st.header("Schade")
st.dataframe(schade_df)

st.header("Coaching gepland")
st.dataframe(gepland)

st.header("Coaching voltooid")
st.dataframe(voltooid)

st.header("Gesprekken")
st.dataframe(gesprekken_df)
