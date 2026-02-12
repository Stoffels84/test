# app.py
# ============================================================
# CHAUFFEUR DASHBOARD (Enterprise structuur)
# ------------------------------------------------------------
# SECTIES (van boven naar beneden):
#   0) CONFIG + HELPERS
#   1) FTP CONFIG + FTP MANAGER (één plek voor alle FTP)
#   2) DATA LOADERS
#      2A) JSON: personeelsficheGB.json
#      2B) Dienst vandaag: steekkaart/yyyymmdd*.xlsx (sheet Dienstlijst)
#      2C) Schade: schade met macro.xlsm (sheet BRON)
#   3) UI: Titel + zoekbalk
#   4) UI: Persoonlijke gegevens
#   5) UI: Dienst van vandaag
#   6) UI: Schade (onderaan)
# ============================================================

from __future__ import annotations

import json
from datetime import date
from io import BytesIO

import pandas as pd
import streamlit as st

from ftp_client import FTPConfig, FTPManager


# ============================================================
# 0) CONFIG + HELPERS
# ============================================================

st.set_page_config(page_title="Chauffeur Dashboard", layout="wide")


def normalize_pnr(x) -> str:
    """Normaliseer personeelsnummer: strip, en 123.0 -> 123."""
    if x is None:
        return ""
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def require_ftp_secrets() -> dict:
    """
    Zorgt dat secrets correct staan.
    Verwacht in Streamlit secrets:

    [FTP]
    host="..."
    port=21
    username="..."
    password="..."
    base_dir="/pad/naar/map"   # optioneel
    """
    cfg = st.secrets.get("FTP")
    if cfg is None:
        st.error("FTP configuratie ontbreekt. Voeg een [FTP]-sectie toe in Streamlit secrets.")
        st.write("Beschikbare secret keys:", list(st.secrets.keys()))
        st.stop()
    # minimale keys check
    for k in ["host", "username", "password"]:
        if k not in cfg:
            st.error(f"FTP secret mist key: '{k}'. Verwacht keys: host, port (opt), username, password, base_dir (opt).")
            st.write("Gevonden FTP keys:", list(cfg.keys()))
            st.stop()
    return cfg


# ============================================================
# 1) FTP CONFIG + FTP MANAGER
# ============================================================

@st.cache_resource
def get_ftp_manager() -> FTPManager:
    cfg = require_ftp_secrets()

    ftp_cfg = FTPConfig(
        host=cfg["host"],
        port=int(cfg.get("port", 21)),
        username=cfg["username"],
        password=cfg["password"],
        base_dir=str(cfg.get("base_dir", "")).strip(),
    )
    return FTPManager(ftp_cfg, timeout=30, passive=True)


# ============================================================
# 2) DATA LOADERS
# ============================================================

# -------------------------
# 2A) JSON: personeelsficheGB.json
# -------------------------

@st.cache_data(ttl=300)
def load_personeelsfiche_json():
    ftp = get_ftp_manager()
    remote_path = ftp.join("personeelsficheGB.json")
    txt = ftp.download_text(remote_path)
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
        if target in data and isinstance(data[target], dict):
            return data[target]
        for v in data.values():
            rec = find_person_record(v, target)
            if rec:
                return rec
        return None

    return None




@st.cache_data(ttl=300)
def load_gesprekken_df() -> pd.DataFrame:
    ftp = get_ftp_manager()

    remote_path = ftp.join("Overzicht gesprekken (aangepast).xlsx")
    b = ftp.download_bytes(remote_path)

    # ✅ juiste tabblad
    df = pd.read_excel(
        BytesIO(b),
        sheet_name="gesprekken per thema",
        engine="openpyxl"
    )

    df.columns = [str(c).strip() for c in df.columns]

    wanted = ["nummer", "Chauffeurnaam", "Onderwerp", "Datum", "Info"]

    # case-insensitive selectie
    col_map = {c.lower(): c for c in df.columns}
    selected, missing = [], []
    for w in wanted:
        k = w.lower()
        if k in col_map:
            selected.append(col_map[k])
        else:
            missing.append(w)

    if not selected:
        raise KeyError(f"Geen verwachte kolommen gevonden in tabblad 'gesprekken per thema'. Kolommen: {list(df.columns)}")

    out = df[selected].copy()
    out.attrs["missing_columns"] = missing

    # Datum netjes zonder uur
    if "Datum" in out.columns:
        out["Datum"] = pd.to_datetime(out["Datum"], errors="coerce").dt.date

    return out




# -------------------------
# 2B) Dienst van vandaag: steekkaart/yyyymmdd*.xlsx (sheet Dienstlijst)
# -------------------------

@st.cache_data(ttl=120)
def load_dienst_vandaag_df() -> pd.DataFrame:
    ftp = get_ftp_manager()

    steekkaart_dir = ftp.join("steekkaart")
    today_prefix = date.today().strftime("%Y%m%d")

    files = ftp.list_files(steekkaart_dir)

    matches = [f for f in files if f.startswith(today_prefix) and f.lower().endswith((".xlsx", ".xls"))]
    if not matches:
        raise FileNotFoundError(f"Geen dienstbestand gevonden in '{steekkaart_dir}' dat start met {today_prefix}")

    matches.sort()
    filename = matches[0]

    remote_path = f"{steekkaart_dir.rstrip('/')}/{filename}"
    b = ftp.download_bytes(remote_path)

    df = pd.read_excel(BytesIO(b), sheet_name="Dienstlijst", engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]

    # deze excel filteren op 'personeelnummer' (zonder s)
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

    col_map = {c.lower(): c for c in df.columns}
    selected, missing = [], []
    for w in wanted:
        k = w.lower()
        if k in col_map:
            selected.append(col_map[k])
        else:
            missing.append(w)

    if not selected:
        raise KeyError(f"Geen verwachte kolommen gevonden in Dienstlijst. Kolommen: {list(df.columns)}")

    out = df[selected].copy()
    out.attrs["missing_columns"] = missing
    out.attrs["source_file"] = filename
    return out


# -------------------------
# 2C) Schade: schade met macro.xlsm (sheet BRON)
# -------------------------

@st.cache_data(ttl=300)
def load_schade_bron_df() -> pd.DataFrame:
    ftp = get_ftp_manager()

    remote_path = ftp.join("schade met macro.xlsm")
    b = ftp.download_bytes(remote_path)

    df = pd.read_excel(BytesIO(b), sheet_name="BRON", engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]

    # In BRON heet de kolom 'personeelsnr' -> hernoem intern naar 'personeelsnummer'
    if "personeelsnr" in df.columns and "personeelsnummer" not in df.columns:
        df = df.rename(columns={"personeelsnr": "personeelsnummer"})

    wanted = ["personeelsnummer", "Datum", "Link", "Locatie", "voertuig", "Bus/tram", "Type"]

    col_map = {c.lower(): c for c in df.columns}
    selected, missing = [], []
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

    if "Datum" in out.columns:
        out["Datum"] = pd.to_datetime(out["Datum"], errors="coerce").dt.date


    return out


# ============================================================
# 3) UI: TITEL + ZOEKBALK
# ============================================================

st.title("🚍 Chauffeur Dashboard")

with st.sidebar:
    st.header("Data")
    if st.button("🔄 Herlaad alles (cache leegmaken)"):
        st.cache_data.clear()

pnr_input = st.text_input("Zoek op personeelsnummer", placeholder="bv. 12345")
if not pnr_input.strip():
    st.info("Geef een personeelsnummer in om de fiche, dienst en schade te tonen.")
    st.stop()

pnr = normalize_pnr(pnr_input)

# ============================================================
# 4) UI: PERSOONLIJKE GEGEVENS
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
# 5) UI: DIENST VAN VANDAAG
# ============================================================

st.header("Dienst van vandaag")

try:
    dienst_df = load_dienst_vandaag_df()

    missing = dienst_df.attrs.get("missing_columns", [])
    if missing:
        st.warning(f"Ontbrekende kolommen in Dienstlijst (niet getoond): {', '.join(missing)}")

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
# 6) UI: SCHADE (ONDERAAN)
# ============================================================

st.header("Schade")

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

    if "Datum" in rows.columns:
        rows = rows.sort_values("Datum", ascending=False)

    if rows.empty:
        st.info("Geen schades gevonden voor dit personeelsnummer.")
    else:
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



# ============================================================
# 7) UI: GESPREKKEN
# ============================================================
# ============================================================
# 7) UI: GESPREKKEN (TEKSTERUGLOOP via components.html)
# ============================================================

import streamlit.components.v1 as components

st.header("Gesprekken")

@st.cache_data(ttl=300)
def load_gesprekken_df() -> pd.DataFrame:
    ftp = get_ftp_manager()
    remote_path = ftp.join("Overzicht gesprekken (aangepast).xlsx")
    b = ftp.download_bytes(remote_path)

    df = pd.read_excel(
        BytesIO(b),
        sheet_name="gesprekken per thema",
        engine="openpyxl",
    )

    df.columns = [str(c).strip() for c in df.columns]

    wanted = ["nummer", "Chauffeurnaam", "Onderwerp", "Datum", "Info"]
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
        raise KeyError(
            "Geen verwachte kolommen gevonden in tabblad 'gesprekken per thema'. "
            f"Gevonden kolommen: {list(df.columns)}"
        )

    out = df[selected].copy()
    out.attrs["missing_columns"] = missing

    if "Datum" in out.columns:
        out["Datum"] = pd.to_datetime(out["Datum"], errors="coerce").dt.date

    return out


try:
    gesprekken_df = load_gesprekken_df()

    missing = gesprekken_df.attrs.get("missing_columns", [])
    if missing:
        st.warning(f"Ontbrekende kolommen in Gesprekken (niet getoond): {', '.join(missing)}")

    if "nummer" not in gesprekken_df.columns:
        st.error(f"Kolom 'nummer' ontbreekt. Gevonden kolommen: {list(gesprekken_df.columns)}")
        st.stop()

    gesprekken_df["nummer"] = gesprekken_df["nummer"].astype(str).map(normalize_pnr)
    rows = gesprekken_df[gesprekken_df["nummer"] == pnr].copy()

    if "Datum" in rows.columns:
        rows = rows.sort_values("Datum", ascending=False)

    if rows.empty:
        st.info("Geen gesprekken gevonden voor dit personeelsnummer.")
    else:
        # ✅ Test: als je hieronder NOG steeds "<b>" ziet als tekst, dan wordt components.html niet gebruikt.
        components.html(
            "<div style='padding:8px 12px; border-radius:10px; "
            "border:1px solid rgba(255,255,255,0.15); "
            "background: rgba(22,27,34,0.60); color:#E6EDF3;'>"
            "✅ HTML render test (dit moet als STIJLVAK zichtbaar zijn, niet als HTML-tekst)"
            "</div>",
            height=60,
            scrolling=False,
        )

        import html as _html

        def esc(x) -> str:
            return _html.escape("" if x is None else str(x))

        show = rows[["nummer", "Chauffeurnaam", "Onderwerp", "Datum", "Info"]].copy()
        for c in ["nummer", "Chauffeurnaam", "Onderwerp", "Info"]:
            show[c] = show[c].fillna("").astype(str)
        show["Datum"] = show["Datum"].fillna("").astype(str)

        body = []
        for _, r in show.iterrows():
            body.append(
                f"""
                <tr>
                  <td class="nowrap">{esc(r['nummer'])}</td>
                  <td class="nowrap">{esc(r['Chauffeurnaam'])}</td>
                  <td class="topic">{esc(r['Onderwerp'])}</td>
                  <td class="nowrap">{esc(r['Datum'])}</td>
                  <td class="info">{esc(r['Info'])}</td>
                </tr>
                """
            )

        html_doc = f"""
        <html>
        <head>
          <meta charset="utf-8" />
          <style>
            body {{
              margin: 0;
              padding: 0;
              font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
              color: #E6EDF3;
              background: transparent;
            }}
            .box {{
              max-height: 650px;
              overflow: auto;
              border: 1px solid rgba(255,255,255,0.10);
              border-radius: 12px;
              background: rgba(22,27,34,0.60);
            }}
            table {{
              width: 100%;
              border-collapse: collapse;
              table-layout: fixed;
            }}
            thead th {{
              position: sticky;
              top: 0;
              z-index: 2;
              text-align: left;
              font-weight: 700;
              padding: 10px 12px;
              background: rgba(22,27,34,0.95);
              border-bottom: 1px solid rgba(255,255,255,0.12);
            }}
            tbody td {{
              padding: 10px 12px;
              border-bottom: 1px solid rgba(255,255,255,0.08);
              vertical-align: top;
              font-size: 14px;
            }}
            th:nth-child(1), td:nth-child(1) {{ width: 110px; }}
            th:nth-child(2), td:nth-child(2) {{ width: 220px; }}
            th:nth-child(3), td:nth-child(3) {{ width: 220px; }}
            th:nth-child(4), td:nth-child(4) {{ width: 130px; }}
            .nowrap {{
              white-space: nowrap;
              overflow: hidden;
              text-overflow: ellipsis;
            }}
            .topic {{
              white-space: nowrap;
              overflow: hidden;
              text-overflow: ellipsis;
            }}
            .info {{
              white-space: normal;     /* <-- TEKSTERUGLOOP */
              word-break: break-word;  /* <-- breekt lange woorden */
              line-height: 1.35;
            }}
          </style>
        </head>
        <body>
          <div class="box">
            <table>
              <thead>
                <tr>
                  <th>nummer</th>
                  <th>Chauffeurnaam</th>
                  <th>Onderwerp</th>
                  <th>Datum</th>
                  <th>Info</th>
                </tr>
              </thead>
              <tbody>
                {''.join(body)}
              </tbody>
            </table>
          </div>
        </body>
        </html>
        """

        # ✅ Dit rendert echte HTML met teksterugloop
        components.html(html_doc, height=680, scrolling=True)

except Exception as e:
    st.error(f"Fout bij laden gesprekken: {e}")

    st.error(f"Fout bij laden gesprekken: {e}")
