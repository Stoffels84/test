from __future__ import annotations

import streamlit as st
import pandas as pd
from io import BytesIO
from ftp_storage import ftp_download_bytes

st.set_page_config(page_title="Chauffeur Dashboard", layout="wide")

@st.cache_data(ttl=300)  # cache 5 min, zodat FTP niet bij elke klik wordt aangeroepen
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
        df = pd.read_excel(BytesIO(data), engine="openpyxl")
        dfs[fname] = df

    return dfs

st.title("🚍 Chauffeur Dashboard (FTP data)")

with st.sidebar:
    st.header("Data")
    if st.button("🔄 Herlaad data (cache leegmaken)"):
        st.cache_data.clear()

dfs = load_excels_from_ftp()

st.subheader("Ingeladen bestanden")
st.write({name: df.shape for name, df in dfs.items()})

# ---- KIES WELK BESTAND DE 'CHAUFFEUR'-BRON IS ----
# Vervang "bestand1.xlsx" door het Excel dat je chauffeurkolom bevat.
main_name = st.selectbox("Kies dataset voor chauffeur-dashboard", list(dfs.keys()))
df = dfs[main_name].copy()

# ---- PAS DEZE KOLOMNAMEN AAN NAAR JOUW EXCEL ----
# Voorbeeld: jouw eerdere projecten gebruikten o.a. 'volledige naam' en 'Datum'
CHAUFFEUR_COL = "volledige naam"
DATE_COL = "Datum"

if CHAUFFEUR_COL not in df.columns:
    st.error(f"Kolom '{CHAUFFEUR_COL}' niet gevonden in {main_name}. "
             f"Beschikbare kolommen: {list(df.columns)}")
    st.stop()

# Datumkolom is optioneel; als hij bestaat, gebruiken we periodefilter
if DATE_COL in df.columns:
    df[DATE_COL] = pd.to_datetime(df[DATE_COL], errors="coerce")

chauffeurs = sorted(df[CHAUFFEUR_COL].dropna().astype(str).unique())
chauffeur = st.sidebar.selectbox("Chauffeur", chauffeurs)

filtered = df[df[CHAUFFEUR_COL].astype(str) == str(chauffeur)].copy()

if DATE_COL in df.columns and filtered[DATE_COL].notna().any():
    min_d = filtered[DATE_COL].min()
    max_d = filtered[DATE_COL].max()
    start_date, end_date = st.sidebar.date_input(
        "Periode",
        value=(min_d.date(), max_d.date()),
    )
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date) + pd.Timedelta(days=1)
    filtered = filtered[(filtered[DATE_COL] >= start) & (filtered[DATE_COL] < end)]

# ---- Dashboard blokken ----
c1, c2, c3 = st.columns(3)
c1.metric("Records", len(filtered))
c2.metric("Unieke waarden (kolom 1)", filtered.iloc[:, 0].nunique() if len(filtered.columns) else 0)
c3.metric("Unieke waarden (kolom 2)", filtered.iloc[:, 1].nunique() if len(filtered.columns) > 1 else 0)

st.divider()
st.subheader(f"Details voor: {chauffeur}")
st.dataframe(filtered, use_container_width=True)
