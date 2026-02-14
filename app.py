# app.py
# ============================================================
# CHAUFFEUR DASHBOARD (Enterprise structuur + FTP + LOGIN)
# ------------------------------------------------------------
# ✅ Login via FTP Excel + logout + cache clear
# ✅ Batch-load: alles in 1 keer (parallel)
# ✅ Mega bus-animatie tijdens laden (fullscreen overlay)
# ✅ Datumfix: Europe/Brussels
# ✅ Persoonlijke gegevens: st.table() => geen scroll/slider
# ✅ Dienst: gisteren + vandaag + morgen (today -1 / today / today +1)
# ✅ Dienst-optimalisatie: 3× list_files() -> 1× list_files() (grote winst)
# ✅ Geen "Geen dienstbestand gevonden" -> "Er is nog geen steekkaart beschikbaar..."
# ✅ Coaching: Coachingslijst.xlsx 1x downloaden, 2 sheets lezen
# ✅ Geen mutaties op gecachte DF's (altijd .copy() in UI)
# ✅ find_person_record deduplicated
# ✅ Subtitels Gisteren/Vandaag/Morgen gecentreerd
# ✅ Dienst-blokken (titel + caption + tabel) gecentreerd + smaller
# ============================================================

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
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

    h1, h2, h3 {
        color: var(--fluo-green) !important;
        text-shadow: 0 0 8px rgba(57,255,20,0.35);
    }

    .centerSubTitle {
        text-align: center;
        font-weight: 900;
        font-size: 34px;
        margin: 10px 0 6px 0;
        color: var(--fluo-green);
        text-shadow: 0 0 10px rgba(57,255,20,0.25);
        letter-spacing: 0.2px;
    }

    .centerCaption {
        text-align: center;
        font-size: 14px;
        opacity: 0.9;
        margin-top: 6px;
        margin-bottom: 8px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

BRUSSELS = ZoneInfo("Europe/Brussels")


# ============================================================
# 0B) HELPERS
# ============================================================

def normalize_pnr(x) -> str:
    """Normaliseer personeelsnr: strip, en 123.0 -> 123."""
    if x is None:
        return ""
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def pick_col(df: pd.DataFrame, wanted_name: str) -> Optional[str]:
    """Zoek een kolom case-insensitive en return de echte kolomnaam."""
    w = wanted_name.strip().lower()
    for c in df.columns:
        if str(c).strip().lower() == w:
            return c
    return None


def find_person_record(data, personeelnummer: str):
    """Zoek een personeelsrecord in JSON (list/dict), op meerdere mogelijke keys."""
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


def centered_subtitle(text: str) -> None:
    st.markdown(f"<div class='centerSubTitle'>{text}</div>", unsafe_allow_html=True)


def centered_caption(text: str) -> None:
    import html as _html
    st.markdown(f"<div class='centerCaption'>{_html.escape(text)}</div>", unsafe_allow_html=True)


def center_block_columns():
    return st.columns([1, 6, 1])


# bcrypt (optioneel)
try:
    from passlib.context import CryptContext
    _PWD_CONTEXT = CryptContext(schemes=["bcrypt"], deprecated="auto")
except Exception:
    _PWD_CONTEXT = None


# ============================================================
# 🚍 0C) LOADING ANIMATIE
# ============================================================

def loading_bus(message: str = "Dashboard wordt geladen..."):
    html = f"""
    <div class="busOverlay">
      <div class="busBox">
        <div class="busTitle">{message}</div>
        <div class="busRoad">
          <div class="busVehicle">🚌</div>
        </div>
        <div class="busHint">Even geduld…</div>
      </div>
    </div>

    <style>
      .busOverlay {{
        position: fixed;
        inset: 0;
        width: 100vw;
        height: 100vh;
        background: rgba(13,17,23,0.92);
        backdrop-filter: blur(8px);
        z-index: 999999;
        display: flex;
        align-items: center;
        justify-content: center;
      }}
      .busBox {{
        width: min(1100px, 92vw);
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 22px;
        background: rgba(22,27,34,0.65);
        padding: 26px 24px;
        box-shadow: 0 10px 40px rgba(0,0,0,0.35);
      }}
      .busTitle {{
        font-weight: 900;
        font-size: 42px;
        letter-spacing: 0.3px;
        margin-bottom: 18px;
        color: #39FF14;
        text-shadow: 0 0 12px rgba(57,255,20,0.25);
      }}
      .busHint {{
        margin-top: 14px;
        font-size: 15px;
        color: #E6EDF3;
        opacity: 0.9;
      }}
      .busRoad {{
        position: relative;
        height: 260px;
        border-radius: 18px;
        overflow: hidden;
        background: rgba(0,0,0,0.22);
        border: 1px solid rgba(255,255,255,0.10);
      }}
      .busRoad::after {{
        content: "";
        position: absolute;
        left: 0;
        right: 0;
        top: 50%;
        height: 3px;
        transform: translateY(-50%);
        background: repeating-linear-gradient(
          to right,
          rgba(255,255,255,0.25) 0,
          rgba(255,255,255,0.25) 28px,
          rgba(255,255,255,0) 28px,
          rgba(255,255,255,0) 52px
        );
        opacity: 0.95;
      }}
      .busVehicle {{
        position: absolute;
        left: -800px;
        top: 50%;
        transform: translateY(-50%) scaleX(-1);
        font-size: 220px;
        filter: drop-shadow(0 0 18px rgba(57,255,20,0.28));
        animation: busDrive 2.8s linear infinite;
        will-change: left;
      }}
      @keyframes busDrive {{
        0%   {{ left: -800px; }}
        100% {{ left: calc(100% + 800px); }}
      }}
      @media (prefers-reduced-motion: reduce) {{
        .busVehicle {{ animation: none; left: 40px; }}
      }}
    </style>
    """
    components.html(html, height=520, scrolling=False)


# ============================================================
# 1) FTP SECRETS + FTP MANAGER
# ============================================================

def require_ftp_secrets() -> dict:
    cfg = st.secrets.get("FTP")
    if cfg is None:
        st.error("FTP configuratie ontbreekt. Voeg een [FTP]-sectie toe in Streamlit secrets.")
        st.write("Beschikbare secret keys:", list(st.secrets.keys()))
        st.stop()

    for k in ["host", "username", "password"]:
        if k not in cfg:
            st.error("FTP secret mist key(s). Verwacht: host, port (opt), username, password, base_dir (opt).")
            st.write("Gevonden FTP keys:", list(cfg.keys()))
            st.stop()
    return cfg


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
# 2) LOGIN (FTP Excel)
# ============================================================

LOGIN_FILE = "toegestaan_gebruik.xlsx"


@st.cache_data(ttl=300)
def load_login_df() -> pd.DataFrame:
    ftp = get_ftp_manager()
    b = ftp.download_bytes(ftp.join(LOGIN_FILE))

    try:
        df = pd.read_excel(BytesIO(b), sheet_name="Blad1", engine="openpyxl")
    except Exception:
        xl = pd.ExcelFile(BytesIO(b), engine="openpyxl")
        df = pd.read_excel(xl, sheet_name=xl.sheet_names[0])

    df.columns = [str(c).strip() for c in df.columns]

    required = ["Naam", "paswoord", "paswoord_hash"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"Loginbestand mist kolommen: {missing}. Gevonden: {list(df.columns)}")

    for c in required:
        df[c] = df[c].fillna("").astype(str)

    return df


def verify_password(plain: str, stored_plain: str, stored_hash: str) -> bool:
    plain = (plain or "").strip()
    stored_plain = (stored_plain or "").strip()
    stored_hash = (stored_hash or "").strip()

    if stored_plain and plain == stored_plain:
        return True

    if stored_hash.startswith("$2") and _PWD_CONTEXT is not None:
        try:
            return _PWD_CONTEXT.verify(plain, stored_hash)
        except Exception:
            pass

    is_hex_64 = len(stored_hash) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in stored_hash)
    if is_hex_64:
        return hashlib.sha256(plain.encode("utf-8")).hexdigest().lower() == stored_hash.lower()

    return False


def login_gate() -> None:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
        st.session_state.user_name = None

    if st.session_state.authenticated:
        return

    st.title("🔐 Dashboard Chauffeur OT Gent")

    try:
        df = load_login_df()
    except Exception as e:
        st.error(f"Kan loginbestand niet laden ({LOGIN_FILE}): {e}")
        st.stop()

    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Naam")
        password = st.text_input("Paswoord", type="password")
        submitted = st.form_submit_button("Inloggen")

    if submitted:
        u = (username or "").strip()
        p = (password or "").strip()

        if not u or not p:
            st.warning("Vul naam en paswoord in.")
        else:
            match = df[df["Naam"].astype(str).str.strip().str.casefold() == u.casefold()]
            if match.empty:
                st.error("Onjuiste login.")
            else:
                row = match.iloc[0]
                ok = verify_password(p, row["paswoord"], row["paswoord_hash"])
                if not ok:
                    st.error("Onjuiste login.")
                else:
                    st.session_state.authenticated = True
                    st.session_state.user_name = u
                    st.rerun()

    st.stop()


login_gate()

with st.sidebar:
    st.write(f"Ingelogd als: **{st.session_state.get('user_name','')}**")
    if st.button("Uitloggen"):
        st.session_state.authenticated = False
        st.session_state.user_name = None
        st.rerun()

    st.divider()
    if st.button("🔄 Herlaad alles (cache leegmaken)"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()


# ============================================================
# 3) DATA LOADERS (FTP)
# ============================================================

@st.cache_data(ttl=300)
def load_personeelsfiche_json():
    ftp = get_ftp_manager()
    txt = ftp.download_text(ftp.join("personeelsficheGB.json"))
    return json.loads(txt)


# ----------------------------
# ✅ Dienst-optimalisatie (1× list_files)
# ----------------------------

DIENST_WANTED = [
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


@st.cache_data(ttl=120)
def load_steekkaart_filelist() -> list[str]:
    ftp = get_ftp_manager()
    steekkaart_dir = ftp.join("steekkaart")
    return ftp.list_files(steekkaart_dir)


def _pick_latest_file_for_prefix(files: list[str], prefix: str) -> Optional[str]:
    matches = [f for f in files if f.startswith(prefix) and f.lower().endswith((".xlsx", ".xls"))]
    if not matches:
        return None
    matches.sort()
    return matches[-1]


@st.cache_data(ttl=120)
def load_dienst_df_for_filename(filename: str) -> pd.DataFrame:
    ftp = get_ftp_manager()
    steekkaart_dir = ftp.join("steekkaart")
    remote_path = f"{steekkaart_dir.rstrip('/')}/{filename}"
    b = ftp.download_bytes(remote_path)

    df = pd.read_excel(BytesIO(b), sheet_name="Dienstlijst", engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]

    col_map = {str(c).strip().lower(): c for c in df.columns}
    selected, missing = [], []
    for w in DIENST_WANTED:
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


@st.cache_data(ttl=300)
def load_schade_bron_df() -> pd.DataFrame:
    ftp = get_ftp_manager()
    b = ftp.download_bytes(ftp.join("schade met macro.xlsm"))

    df = pd.read_excel(BytesIO(b), sheet_name="BRON", engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]

    if "personeelsnr" in df.columns and "personeelsnummer" not in df.columns:
        df = df.rename(columns={"personeelsnr": "personeelsnummer"})

    wanted = ["personeelsnummer", "Datum", "Link", "Locatie", "voertuig", "Bus/tram", "Type"]
    col_map = {str(c).strip().lower(): c for c in df.columns}

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


@st.cache_data(ttl=300)
def load_coachings_bytes() -> bytes:
    ftp = get_ftp_manager()
    return ftp.download_bytes(ftp.join("Coachingslijst.xlsx"))


@st.cache_data(ttl=300)
def load_coachings_dfs() -> dict:
    b = load_coachings_bytes()

    df_gepland = pd.read_excel(BytesIO(b), sheet_name="Coaching", engine="openpyxl")
    df_gepland.columns = [str(c).strip() for c in df_gepland.columns]

    wanted1 = ["aanvraagsdatum", "P-nr", "Volledige naam", "Opmerkingen"]
    col_map1 = {str(c).strip().lower(): c for c in df_gepland.columns}
    selected1, missing1 = [], []
    for w in wanted1:
        k = w.lower()
        if k in col_map1:
            selected1.append(col_map1[k])
        else:
            missing1.append(w)
    if not selected1:
        raise KeyError(f"Geen verwachte kolommen gevonden in tabblad 'Coaching'. Kolommen: {list(df_gepland.columns)}")

    gepland = df_gepland[selected1].copy()
    gepland.attrs["missing_columns"] = missing1

    c = pick_col(gepland, "aanvraagsdatum")
    if c:
        gepland[c] = pd.to_datetime(gepland[c], errors="coerce").dt.date

    df_voltooid = pd.read_excel(BytesIO(b), sheet_name="Voltooide coachings", engine="openpyxl")
    df_voltooid.columns = [str(c).strip() for c in df_voltooid.columns]

    wanted2 = ["P-nr", "Volledige naam", "Opmerking", "Instructeur", "DAtum coaching"]
    col_map2 = {str(c).strip().lower(): c for c in df_voltooid.columns}
    selected2, missing2 = [], []
    for w in wanted2:
        k = w.lower()
        if k in col_map2:
            selected2.append(col_map2[k])
        else:
            missing2.append(w)
    if not selected2:
        raise KeyError(f"Geen verwachte kolommen gevonden in tabblad 'Voltooide coachings'. Kolommen: {list(df_voltooid.columns)}")

    voltooid = df_voltooid[selected2].copy()
    voltooid.attrs["missing_columns"] = missing2

    for c in voltooid.columns:
        if "datum" in c.lower() and "coach" in c.lower():
            voltooid[c] = pd.to_datetime(voltooid[c], errors="coerce").dt.date

    return {"gepland": gepland, "voltooid": voltooid}


@st.cache_data(ttl=300)
def load_gesprekken_df() -> pd.DataFrame:
    ftp = get_ftp_manager()
    b = ftp.download_bytes(ftp.join("Overzicht gesprekken (aangepast).xlsx"))

    df = pd.read_excel(BytesIO(b), sheet_name="gesprekken per thema", engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]

    wanted = ["nummer", "Chauffeurnaam", "Onderwerp", "Datum", "Info"]
    col_map = {str(c).strip().lower(): c for c in df.columns}

    selected, missing = [], []
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


# ============================================================
# 3G) BATCH LOAD
# ============================================================

@st.cache_data(ttl=120)
def load_all_data() -> dict:
    today = datetime.now(BRUSSELS).date()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)

    prefixes = {
        "yesterday": yesterday.strftime("%Y%m%d"),
        "today": today.strftime("%Y%m%d"),
        "tomorrow": tomorrow.strftime("%Y%m%d"),
    }

    results: dict = {}
    errors: dict = {}

    # ✅ 1× list_files()
    try:
        files = load_steekkaart_filelist()
    except Exception as e:
        files = []
        errors["dienst_list"] = str(e)

    chosen = {
        "dienst_gisteren_file": _pick_latest_file_for_prefix(files, prefixes["yesterday"]),
        "dienst_vandaag_file": _pick_latest_file_for_prefix(files, prefixes["today"]),
        "dienst_morgen_file": _pick_latest_file_for_prefix(files, prefixes["tomorrow"]),
    }

    tasks = {
        "person_json": load_personeelsfiche_json,
        "schade_df": load_schade_bron_df,
        "coachings": load_coachings_dfs,
        "gesprekken_df": load_gesprekken_df,
    }

    if "dienst_list" not in errors:
        if chosen["dienst_gisteren_file"]:
            tasks["dienst_gisteren_df"] = lambda f=chosen["dienst_gisteren_file"]: load_dienst_df_for_filename(f)
        if chosen["dienst_vandaag_file"]:
            tasks["dienst_vandaag_df"] = lambda f=chosen["dienst_vandaag_file"]: load_dienst_df_for_filename(f)
        if chosen["dienst_morgen_file"]:
            tasks["dienst_morgen_df"] = lambda f=chosen["dienst_morgen_file"]: load_dienst_df_for_filename(f)

    with ThreadPoolExecutor(max_workers=max(4, len(tasks))) as ex:
        fut_to_key = {ex.submit(fn): key for key, fn in tasks.items()}
        for fut in as_completed(fut_to_key):
            key = fut_to_key[fut]
            try:
                results[key] = fut.result()
            except Exception as e:
                errors[key] = str(e)

    results["_errors"] = errors
    results["_meta"] = {
        "yesterday": yesterday.isoformat(),
        "today": today.isoformat(),
        "tomorrow": tomorrow.isoformat(),
        "yesterday_prefix": prefixes["yesterday"],
        "today_prefix": prefixes["today"],
        "tomorrow_prefix": prefixes["tomorrow"],
        "dienst_files": chosen,
    }
    return results


# ============================================================
# 4) UI: TITEL + ZOEKBALK
# ============================================================

st.title("🚍 Chauffeur Dashboard")

pnr_input = st.text_input("Zoek op personeelsnummer", placeholder="bv. 12345")
if not pnr_input.strip():
    st.info("Geef een personeelsnummer in om alle data te tonen.")
    st.stop()

pnr = normalize_pnr(pnr_input)


# ============================================================
# 4B) 1 KEER LADEN + MEGA BUS
# ============================================================

loader_slot = st.empty()
with loader_slot:
    loading_bus("Alles wordt geladen...")

bundle = load_all_data()

loader_slot.empty()

errors = bundle.get("_errors", {})
meta = bundle.get("_meta", {})

data_json = bundle.get("person_json")
dienst_gisteren_df = bundle.get("dienst_gisteren_df")
dienst_vandaag_df = bundle.get("dienst_vandaag_df")
dienst_morgen_df = bundle.get("dienst_morgen_df")
schade_df = bundle.get("schade_df")

coachings = bundle.get("coachings") or {}
gepland = coachings.get("gepland")
voltooid = coachings.get("voltooid")

gesprekken_df = bundle.get("gesprekken_df")


# ============================================================
# 5) UI: PERSOONLIJKE GEGEVENS
# ============================================================

st.header("Persoonlijke gegevens")

if "person_json" in errors:
    st.error(f"Fout bij laden personeelsficheGB.json: {errors['person_json']}")
else:
    person = find_person_record(data_json, pnr) if data_json is not None else None
    if person:
        df_person = pd.DataFrame([{"Veld": k, "Waarde": v} for k, v in person.items()])
        st.table(df_person)
    else:
        st.warning("Geen persoonlijke fiche gevonden voor dit personeelsnummer.")

st.divider()


# ============================================================
# 6) UI: DIENST (GISTEREN / VANDAAG / MORGEN)
# ============================================================

st.header("Dienst van gisteren, vandaag en morgen")
st.caption(
    f"Gisteren: {meta.get('yesterday','')}  —  "
    f"Vandaag: {meta.get('today','')}  —  "
    f"Morgen: {meta.get('tomorrow','')}"
)

def render_dienst_block(label: str, df: Optional[pd.DataFrame], caption_prefix: str):
    _, mid, _ = center_block_columns()
    with mid:
        centered_subtitle(label)

        if "dienst_list" in errors:
            st.error(f"Fout bij ophalen steekkaart-lijst: {errors['dienst_list']}")
            return

        if df is None:
            st.info("Er is nog geen steekkaart beschikbaar...")
            return

        missing = df.attrs.get("missing_columns", [])
        if missing:
            st.warning(f"Ontbrekende kolommen in Dienstlijst ({label.lower()}, niet getoond): {', '.join(missing)}")

        if "personeelnummer" not in df.columns:
            st.error(f"Kolom 'personeelnummer' ontbreekt ({label.lower()}). Gevonden: {list(df.columns)}")
            return

        local = df.copy()
        local["personeelnummer"] = local["personeelnummer"].astype(str).map(normalize_pnr)
        rows = local[local["personeelnummer"] == pnr].copy()

        source_file = df.attrs.get("source_file", "")
        if source_file:
            centered_caption(f"{caption_prefix}: {source_file}")

        if rows.empty:
            st.info(f"Geen dienst gevonden voor {label.lower()} voor dit personeelnummer.")
        else:
            st.dataframe(rows, use_container_width=True, hide_index=True)


# ✅ Gisteren komt boven Vandaag
if "dienst_gisteren_df" in errors:
    st.error(f"Fout bij laden steekkaart (gisteren): {errors['dienst_gisteren_df']}")
render_dienst_block("Gisteren", dienst_gisteren_df, "Bronbestand (gisteren)")

st.divider()

if "dienst_vandaag_df" in errors:
    st.error(f"Fout bij laden steekkaart (vandaag): {errors['dienst_vandaag_df']}")
render_dienst_block("Vandaag", dienst_vandaag_df, "Bronbestand (vandaag)")

st.divider()

if "dienst_morgen_df" in errors:
    st.error(f"Fout bij laden steekkaart (morgen): {errors['dienst_morgen_df']}")
render_dienst_block("Morgen", dienst_morgen_df, "Bronbestand (morgen)")

st.divider()


# ============================================================
# 7) UI: SCHADE (EAF)
# ============================================================

st.header("Schade (EAF)")

if "schade_df" in errors:
    st.error(f"Fout bij laden schade (BRON): {errors['schade_df']}")
elif schade_df is None:
    st.error("Schade (BRON) niet geladen.")
else:
    missing = schade_df.attrs.get("missing_columns", [])
    if missing:
        st.warning(f"Ontbrekende kolommen in BRON (niet getoond): {', '.join(missing)}")

    if "personeelsnummer" not in schade_df.columns:
        st.error(f"Kolom 'personeelsnummer' ontbreekt in BRON. Gevonden: {list(schade_df.columns)}")
    else:
        df_s = schade_df.copy()
        df_s["personeelsnummer"] = df_s["personeelsnummer"].astype(str).map(normalize_pnr)
        rows = df_s[df_s["personeelsnummer"] == pnr].copy()

        if "Datum" in rows.columns:
            rows = rows.sort_values("Datum", ascending=False)

        if rows.empty:
            st.info("Geen schades gevonden voor dit personeelsnummer.")
        else:
            if "Link" in rows.columns:
                rows["Link"] = rows["Link"].fillna("").astype(str)
                rows["Link"] = rows["Link"].where(rows["Link"].str.startswith(("http://", "https://")), "")

                st.dataframe(
                    rows,
                    use_container_width=True,
                    hide_index=True,
                    column_config={"Link": st.column_config.LinkColumn("Link", display_text="Open")},
                )
            else:
                st.dataframe(rows, use_container_width=True, hide_index=True)

st.divider()


# ============================================================
# 8) UI: COACHING
# ============================================================

st.header("Coaching")

if "coachings" in errors:
    st.error(f"Fout bij laden coaching: {errors['coachings']}")
else:
    st.subheader("Geplande coaching")
    if gepland is None:
        st.info("Geplande coaching niet beschikbaar.")
    else:
        miss = gepland.attrs.get("missing_columns", [])
        if miss:
            st.warning(f"Ontbrekende kolommen in 'Coaching' (niet getoond): {', '.join(miss)}")

        gepl = gepland.copy()
        pcol = pick_col(gepl, "P-nr")
        if pcol is None:
            st.error(f"Kolom 'P-nr' ontbreekt in tabblad 'Coaching'. Gevonden: {list(gepl.columns)}")
        else:
            gepl[pcol] = gepl[pcol].astype(str).map(normalize_pnr)
            gepl_rows = gepl[gepl[pcol] == pnr].copy()

            if gepl_rows.empty:
                st.info("Geen geplande coaching gevonden.")
            else:
                st.dataframe(gepl_rows, use_container_width=True, hide_index=True)

    st.divider()

    st.subheader("Voltooide coaching")
    if voltooid is None:
        st.info("Voltooide coaching niet beschikbaar.")
    else:
        volt = voltooid.copy()
        miss2 = volt.attrs.get("missing_columns", [])
        if miss2:
            st.warning(f"Ontbrekende kolommen in 'Voltooide coachings' (niet getoond): {', '.join(miss2)}")

        pcol2 = pick_col(volt, "P-nr")
        if pcol2 is None:
            st.error(f"Kolom 'P-nr' ontbreekt in tabblad 'Voltooide coachings'. Gevonden: {list(volt.columns)}")
        else:
            volt[pcol2] = volt[pcol2].astype(str).map(normalize_pnr)
            volt_rows = volt[volt[pcol2] == pnr].copy()

            date_cols = [c for c in volt_rows.columns if "datum" in c.lower() and "coach" in c.lower()]
            if date_cols:
                volt_rows = volt_rows.sort_values(date_cols[0], ascending=False)

            if volt_rows.empty:
                st.info("Geen voltooide coaching gevonden.")
            else:
                st.dataframe(volt_rows, use_container_width=True, hide_index=True)

st.divider()


# ============================================================
# 9) UI: GESPREKKEN (teksterugloop via components.html)
# ============================================================

st.header("Gesprekken")

if "gesprekken_df" in errors:
    st.error(f"Fout bij laden gesprekken: {errors['gesprekken_df']}")
elif gesprekken_df is None:
    st.error("Gesprekken niet geladen.")
else:
    missing = gesprekken_df.attrs.get("missing_columns", [])
    if missing:
        st.warning(f"Ontbrekende kolommen in Gesprekken (niet getoond): {', '.join(missing)}")

    if "nummer" not in gesprekken_df.columns:
        st.error(f"Kolom 'nummer' ontbreekt. Gevonden: {list(gesprekken_df.columns)}")
    else:
        g = gesprekken_df.copy()
        g["nummer"] = g["nummer"].astype(str).map(normalize_pnr)
        rows = g[g["nummer"] == pnr].copy()

        if "Datum" in rows.columns:
            rows = rows.sort_values("Datum", ascending=False)

        if rows.empty:
            st.info("Geen gesprekken gevonden voor dit personeelsnummer.")
        else:
            import html as _html

            def esc(x) -> str:
                return _html.escape("" if x is None else str(x))

            desired_cols = ["nummer", "Chauffeurnaam", "Onderwerp", "Datum", "Info"]
            show_cols = [c for c in desired_cols if c in rows.columns]
            show = rows[show_cols].copy()

            for c in [c for c in ["nummer", "Chauffeurnaam", "Onderwerp", "Info"] if c in show.columns]:
                show[c] = show[c].fillna("").astype(str)
            if "Datum" in show.columns:
                show["Datum"] = show["Datum"].fillna("").astype(str)

            body = []
            for _, r in show.iterrows():
                body.append(
                    f"""
                    <tr>
                      <td class="nowrap">{esc(r.get('nummer',''))}</td>
                      <td class="nowrap">{esc(r.get('Chauffeurnaam',''))}</td>
                      <td class="topic">{esc(r.get('Onderwerp',''))}</td>
                      <td class="nowrap">{esc(r.get('Datum',''))}</td>
                      <td class="info">{esc(r.get('Info',''))}</td>
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
                  white-space: normal;
                  word-break: break-word;
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
            components.html(html_doc, height=680, scrolling=False)
