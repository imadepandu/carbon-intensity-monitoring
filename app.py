"""Carbon Intensity Dashboard.

Dashboard monokrom yang menyatukan data, EDA, EWMA dan CEP dalam satu alur
pemantauan. Simpan berkas ini bersama data.py, eda.py, ewma.py dan cep.py,
lalu jalankan `streamlit run app.py`.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
import zipfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.data import DataError, get_data, quality_report
from engine.eda import MIN_REFERENCE_ROWS, autocorrelation, compute_baseline, hourly_profile, split_reference
from engine.ewma import DEFAULT_ALPHA, DEFAULT_LAMBDA, EWMA, Normalizer, calibrate_limit, run as run_ewma
from engine.cep import GAP, LEVEL, REPEAT, STEPS, WINDOW, CEP, detect, summarize

# ---------------------------------------------------------------------------
# Konfigurasi dan keadaan awal
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA_CACHE = ROOT / "results" / "history.csv"

st.set_page_config(
    page_title="Carbon Intensity Dashboard",
    page_icon="C",
    layout="wide",
    initial_sidebar_state="expanded",
)

DEFAULT_STATE = {
    "page": "Home",
    "data_days": 30,
    "source_mode": "auto",
    "ewma_lambda": float(DEFAULT_LAMBDA),
    "ewma_alpha": float(DEFAULT_ALPHA),
    "cep_window": int(WINDOW),
    "theme": "Terang",
    "nav_open": True,
    "group_open": True,
    "flash": "",
    "home_chart_mode": "Galat",
    "home_range": "Seluruh periode",
    "pattern_focus": "Alarm EWMA",
    "ledger_filter": "Alarm dan pola",
    "search_q": "",
}
for _key, _value in DEFAULT_STATE.items():
    if _key not in st.session_state:
        st.session_state[_key] = _value

DARK = st.session_state.theme == "Gelap"
if DARK:
    C = {
        "bg": "#0B0B0D", "panel": "#151518", "tile": "#1E1E22", "ink": "#F4F4F5",
        "muted": "#9A9AA3", "line": "#2A2A2F", "soft": "#242429", "mid": "#6E6E76",
        "contrast": "#111113", "shadow": "rgba(0,0,0,.35)", "hero": "#2A2A2F",
        "rail_a": "#1C1C20", "rail_b": "#131315", "subactive": "#2B2B31",
    }
else:
    C = {
        "bg": "#E9E9EC", "panel": "#FFFFFF", "tile": "#F4F4F5", "ink": "#18181B",
        "muted": "#8A8A93", "line": "#E9E9EC", "soft": "#EFEFF1", "mid": "#B4B4BB",
        "contrast": "#FFFFFF", "shadow": "rgba(24,24,27,.07)", "hero": "#1C1C1F",
        "rail_a": "#FBFBFC", "rail_b": "#EDEDF0", "subactive": "#FFFFFF",
    }

PATTERN_LABELS = {
    "ewma_alarm": "Alarm EWMA", "repetition": "Repetition", "trend_up": "Tren naik",
    "trend_down": "Tren turun", "absence": "Absence",
}
PATTERN_ORDER = ["Alarm EWMA", "Repetition", "Tren naik", "Tren turun", "Absence"]
PATTERN_ICON = {
    "Alarm EWMA": "alert", "Repetition": "repeat", "Tren naik": "trend-up",
    "Tren turun": "trend-down", "Absence": "clock",
}
PATTERN_SYMBOL = {
    "Alarm EWMA": "circle", "Repetition": "square", "Tren naik": "triangle-up",
    "Tren turun": "triangle-down", "Absence": "diamond",
}
RANGE_DAYS = {
    "Seluruh periode": None, "14 hari terakhir": 14, "7 hari terakhir": 7,
    "3 hari terakhir": 3, "24 jam terakhir": 1,
}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

# ---------------------------------------------------------------------------
# Ikon garis (gaya Feather) untuk sidebar, kartu dan daftar
# ---------------------------------------------------------------------------
ICONS = {
    "home": '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    "layers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
    "activity": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "bars": '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    "sliders": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "info": '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    "sun": '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
    "chevrons-left": '<polyline points="11 17 6 12 11 7"/><polyline points="18 17 13 12 18 7"/>',
    "chevrons-right": '<polyline points="13 17 18 12 13 7"/><polyline points="6 17 11 12 6 7"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "refresh": '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
    "alert": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "repeat": '<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
    "trend-up": '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>',
    "trend-down": '<polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "wave": '<path d="M8 6c3 3 3 9 0 12"/><path d="M12 4c5 5 5 11 0 16"/><path d="M16 2c7 7 7 13 0 20"/>',
}


def svg(name: str, size: int = 20, stroke: float = 1.7, color: str = "currentColor") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round" style="display:block">{ICONS[name]}</svg>'
    )


def svg_uri(name: str, color: str, stroke: float = 1.7) -> str:
    return 'url("data:image/svg+xml;charset=utf-8,' + quote(svg(name, 24, stroke, color)) + '")'


# ---------------------------------------------------------------------------
# Gaya tampilan
# ---------------------------------------------------------------------------
def css_variables() -> str:
    names = ["bg", "panel", "tile", "ink", "muted", "line", "soft", "mid", "contrast", "shadow", "hero",
             "rail_a", "rail_b", "subactive"]
    body = ";".join(f"--{n.replace('_', '-')}:{C[n]}" for n in names)
    return ":root{" + body + ";color-scheme:" + ("dark" if DARK else "light") + ";}"


BASE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
html, body, .stApp, button, input, textarea, select, [class*="css"] { font-family: 'DM Sans', sans-serif; }
.stApp { background: var(--bg); color: var(--ink); }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer { visibility: hidden; }
[data-testid="stMainBlockContainer"], .block-container {
  max-width: 1480px; width: calc(100% - 28px); margin: 18px auto; padding: 1.35rem 2rem 1.8rem;
  background: var(--panel); border: 1px solid var(--line); border-radius: 30px; box-shadow: 0 12px 40px var(--shadow);
}
h1, h2, h3, h4, p, label, li { color: var(--ink); }
h1 { font-size: 1.6rem !important; font-weight: 700 !important; letter-spacing: -.045em !important; }
h2 { font-size: 1.12rem !important; font-weight: 700 !important; letter-spacing: -.03em !important; }
h3 { font-size: 1rem !important; font-weight: 700 !important; letter-spacing: -.02em !important; }
hr { border-color: var(--line); }
small, .muted { color: var(--muted); }

/* ---- sidebar: panel melayang ---- */
[data-testid="stSidebar"] { background: transparent !important; border-right: 0 !important; padding: 14px 0 14px 14px; box-sizing: border-box; }
[data-testid="stSidebar"] > div:first-child {
  background: linear-gradient(180deg, var(--rail-a), var(--rail-b)) !important; border: 1px solid var(--line);
  border-radius: 30px; width: 100% !important; height: 100% !important; box-shadow: 0 12px 40px var(--shadow); overflow: hidden;
}
[data-testid="stSidebarHeader"], [data-testid="stSidebarCollapseButton"], [data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"], [data-testid="stSidebarResizeHandle"] { display: none !important; }
[data-testid="stSidebarContent"] { position: relative; height: 100%; padding: 1.1rem .9rem .9rem; overflow-x: hidden; }
[data-testid="stSidebarUserContent"] { padding: 0 !important; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .4rem; }
.brand { display: flex; align-items: center; gap: .7rem; }
.brand-name { font-weight: 700; font-size: 1.15rem; letter-spacing: -.03em; color: var(--ink); }
.sidebar-mark { width: 40px; height: 40px; border-radius: 14px; display: flex; justify-content: center; align-items: center;
  background: var(--ink); color: var(--contrast); font-weight: 700; font-size: 1.05rem; letter-spacing: -.05em; flex: 0 0 40px; }
.sidebar-rule { height: 1px; background: var(--line); margin: .55rem .2rem .7rem; }
.sidebar-center { display: flex; justify-content: center; margin-bottom: .55rem; }

/* ---- tombol navigasi: bentuk dasar ---- */
[class*="st-key-nav_"] button {
  height: 48px !important; min-height: 48px !important; border-radius: 999px !important; border: 1px solid transparent !important;
  background: transparent !important; color: var(--ink) !important; box-shadow: none !important; display: flex !important;
  align-items: center !important; position: relative; transform: none !important; transition: background .15s ease;
}
[class*="st-key-nav_"] button:hover { background: var(--soft) !important; }
[class*="st-key-nav_"] button::before { content: ""; width: 22px; height: 22px; flex: 0 0 22px; background-size: contain;
  background-repeat: no-repeat; background-position: center; }
[class*="st-key-nav_"] button > div { flex: 1; display: flex; align-items: center; justify-content: flex-start; text-align: left; }
[class*="st-key-nav_"] button p { margin: 0; text-align: left; font-size: .93rem; font-weight: 500;
  white-space: nowrap; color: inherit !important; }
.st-key-nav_toggle button { width: 40px !important; height: 40px !important; min-height: 40px !important; padding: 0 !important;
  justify-content: center !important; border: 1px solid var(--line) !important; }
.st-key-nav_toggle button > div { display: none !important; }
.st-key-nav_toggle button::before { margin: 0 !important; width: 18px; height: 18px; flex: 0 0 18px; }

/* ---- kontrol di panel utama ---- */
button[kind="secondary"], button[kind="secondaryFormSubmit"] {
  border: 1px solid var(--line); border-radius: 12px; font-weight: 600; background: var(--tile); color: var(--ink);
  transition: transform .15s ease, box-shadow .15s ease;
}
button[kind="secondary"]:hover, button[kind="secondaryFormSubmit"]:hover {
  transform: translateY(-1px); box-shadow: 0 6px 16px var(--shadow); border-color: var(--mid); color: var(--ink);
}
button[kind="primary"], button[kind="primaryFormSubmit"] {
  background: var(--ink) !important; color: var(--contrast) !important; border: 1px solid var(--ink) !important; border-radius: 12px; font-weight: 600;
}
button[kind="primary"] p, button[kind="primaryFormSubmit"] p { color: var(--contrast) !important; }
[data-testid="stDownloadButton"] button { width: 100%; }
.st-key-btn_refresh button { width: 44px !important; height: 44px !important; min-height: 44px !important; padding: 0 !important;
  border-radius: 50% !important; display: flex !important; align-items: center; justify-content: center; }
.st-key-btn_refresh button > div { display: none !important; }
.st-key-btn_refresh button::before { content: ""; width: 18px; height: 18px; background-size: contain; background-repeat: no-repeat; background-position: center; }
div[data-baseweb="input"], div[data-baseweb="select"] > div, div[data-baseweb="base-input"],
[data-testid="stTextInputRootElement"], [data-testid="stSelectbox"] [role="group"] {
  background: var(--tile) !important; border-radius: 12px !important; border: 1px solid var(--line) !important; color: var(--ink) !important;
}
div[data-baseweb="input"] input, div[data-baseweb="select"] div, div[data-baseweb="select"] span,
[data-testid="stTextInputRootElement"] input, [data-testid="stSelectbox"] input {
  color: var(--ink) !important; -webkit-text-fill-color: var(--ink) !important; background: transparent !important;
}
[data-testid="stSelectbox"] [role="group"] button { color: var(--ink) !important; background: transparent !important; }
input::placeholder { color: var(--muted) !important; -webkit-text-fill-color: var(--muted) !important; }
div[data-baseweb="popover"] > div, ul[role="listbox"], li[role="option"], [data-testid="stSelectboxVirtualDropdown"],
[data-testid="stSelectboxVirtualDropdown"] [role="option"] { background: var(--panel) !important; color: var(--ink) !important; }
[data-testid="stSelectboxVirtualDropdown"] [role="option"] div { color: var(--ink) !important; }
li[role="option"]:hover, [role="option"]:hover, [role="option"][data-focused="true"] { background: var(--soft) !important; }
div[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
div[data-testid="stMetric"] { background: var(--tile); border-radius: 16px; padding: 12px 14px; }
div[data-testid="stMetricLabel"] { color: var(--muted); }
div[data-testid="stMetricValue"] { color: var(--ink); }
div[data-testid="stExpander"] { background: var(--tile); border: 0; border-radius: 16px; }
div[data-testid="stForm"] { border: 1px solid var(--line); border-radius: 20px; background: transparent; }
[data-testid="stAlert"] { border-radius: 14px; }

/* ---- radio sebagai tab / pil ---- */
.st-key-home_chart_mode [role="radiogroup"] { gap: 1.4rem; }
.st-key-home_chart_mode label[data-baseweb="radio"] > div:first-child, .st-key-pattern_focus label[data-baseweb="radio"] > div:first-child,
.st-key-home_chart_mode label[data-testid="stRadioOption"] > div > div:first-child, .st-key-pattern_focus label[data-testid="stRadioOption"] > div > div:first-child { display: none !important; }
.st-key-home_chart_mode label { padding: 0 !important; margin: 0 !important; cursor: pointer; }
.st-key-home_chart_mode label p { font-size: 1.28rem !important; font-weight: 700 !important; letter-spacing: -.035em; color: var(--mid) !important; }
.st-key-home_chart_mode label:has(input:checked) p { color: var(--ink) !important; }
.st-key-pattern_focus [role="radiogroup"] { gap: .4rem; flex-wrap: wrap; }
.st-key-pattern_focus label { background: var(--tile); border-radius: 999px; padding: .32rem .8rem !important; margin: 0 !important; cursor: pointer; border: 1px solid transparent; }
.st-key-pattern_focus label p { font-size: .76rem !important; font-weight: 600; color: var(--muted) !important; }
.st-key-pattern_focus label:has(input:checked) { background: var(--ink); }
.st-key-pattern_focus label:has(input:checked) p { color: var(--contrast) !important; }

/* ---- komponen HTML ---- */
.page-title { font-size: 1.12rem; font-weight: 700; letter-spacing: -.03em; color: var(--ink); }
.page-sub { font-size: .76rem; color: var(--muted); margin-top: .1rem; }
.head-rule { height: 1px; background: var(--line); margin: .6rem -2rem 1.2rem; }
.sec-head { display: flex; align-items: center; justify-content: space-between; margin: .1rem 0 .85rem; }
.sec-title { font-size: 1.1rem; font-weight: 700; letter-spacing: -.03em; color: var(--ink); }
.sec-sub { font-size: .74rem; color: var(--muted); }
.pill { display: inline-flex; align-items: center; background: var(--tile); color: var(--ink); border-radius: 999px; padding: .32rem .7rem; font-size: .72rem; font-weight: 600; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ink); margin-right: .45rem; display: inline-block; }
.tile { background: var(--tile); border-radius: 18px; padding: .95rem 1.05rem; height: 100%; min-height: 96px; }
.tile-label { font-size: .72rem; color: var(--muted); }
.tile-value { font-size: 1.4rem; line-height: 1.15; font-weight: 700; letter-spacing: -.05em; margin: .3rem 0 .2rem; overflow-wrap: anywhere; color: var(--ink); }
.tile-foot { font-size: .68rem; line-height: 1.45; color: var(--muted); }
.panel { border: 1px solid var(--line); border-radius: 20px; padding: 1.05rem 1.2rem; height: 100%; }
.panel-title { font-weight: 700; font-size: .98rem; margin-bottom: .2rem; color: var(--ink); }
.panel-note { font-size: .76rem; line-height: 1.55; color: var(--muted); margin-bottom: .8rem; }
.legend-row { display: flex; align-items: center; justify-content: space-between; gap: .65rem; padding: .6rem 0; border-bottom: 1px solid var(--line); font-size: .8rem; color: var(--ink); }
.legend-row:last-child { border-bottom: 0; }
.tiny-label { font-size: .68rem; letter-spacing: .13em; text-transform: uppercase; color: var(--muted); font-weight: 700; }
[data-testid="stColumn"]:has(.divider-mark), [data-testid="column"]:has(.divider-mark) { border-left: 1px solid var(--line); padding-left: 1.7rem; }

/* kartu gelap */
.hero { position: relative; overflow: hidden; background: var(--hero); border-radius: 24px; padding: 1.25rem 1.4rem 1.1rem; min-height: 238px; color: #F4F4F5; }
.hero-decor { position: absolute; top: 0; right: 0; height: 100%; width: 58%; pointer-events: none; }
.hero-body { position: relative; z-index: 1; display: flex; flex-direction: column; justify-content: space-between; min-height: 200px; gap: .8rem; }
.hero-label { font-size: .72rem; color: #A1A1AA; letter-spacing: .04em; }
.hero-value { font-size: 2.15rem; font-weight: 700; letter-spacing: -.055em; line-height: 1.1; color: #FFFFFF; }
.hero-value span { font-size: .78rem; font-weight: 500; color: #A1A1AA; letter-spacing: 0; margin-left: .45rem; }
.hero-chip-row { display: flex; align-items: center; gap: .6rem; color: #8B8B92; }
.hero-chip { width: 40px; height: 30px; border-radius: 7px; background: linear-gradient(135deg, #9A9AA2, #5B5B62); position: relative; }
.hero-chip::after { content: ""; position: absolute; inset: 7px 9px; border: 1px solid rgba(0,0,0,.28); border-radius: 3px; }
.hero-nums { display: flex; gap: 1.6rem; }
.hero-nums i { display: block; font-style: normal; font-size: .62rem; letter-spacing: .14em; text-transform: uppercase; color: #8B8B92; margin-bottom: .15rem; }
.hero-nums b { font-size: .98rem; font-weight: 600; letter-spacing: .04em; color: #F4F4F5; font-variant-numeric: tabular-nums; }
.hero-foot { display: flex; justify-content: space-between; align-items: flex-end; font-size: .68rem; color: #8B8B92; letter-spacing: .06em; }
.hero-foot b { font-size: 1.05rem; letter-spacing: -.02em; color: #8B8B92; font-weight: 700; }

/* daftar aktivitas */
.act-row { display: flex; align-items: center; gap: .9rem; padding: .8rem 0; border-bottom: 1px solid var(--line); }
.act-row:last-child { border-bottom: 0; }
.act-ico { width: 40px; height: 40px; border-radius: 50%; border: 1.5px solid var(--ink); display: flex; align-items: center; justify-content: center; flex: 0 0 40px; color: var(--ink); }
.act-main { flex: 1; min-width: 0; }
.act-title { font-weight: 600; font-size: .88rem; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.act-sub { font-size: .72rem; color: var(--muted); margin-top: .1rem; }
.act-val { font-weight: 700; font-size: .95rem; letter-spacing: -.02em; font-variant-numeric: tabular-nums; color: var(--ink); text-align: right; }
.act-val small { display: block; font-weight: 500; font-size: .66rem; color: var(--muted); letter-spacing: 0; }
.empty-note { background: var(--tile); border-radius: 16px; padding: 1rem 1.1rem; font-size: .82rem; color: var(--muted); }

/* kartu pola */
.pcard { background: var(--tile); border-radius: 18px; padding: .9rem .85rem; height: 100%; color: var(--muted); transition: background .15s ease; }
.pcard.on { background: var(--hero); color: #A1A1AA; }
.pcard-ico { height: 22px; }
.pcard-label { font-size: .76rem; margin: .75rem 0 .2rem; }
.pcard-val { font-size: 1.5rem; font-weight: 700; letter-spacing: -.05em; color: var(--ink); line-height: 1.1; }
.pcard.on .pcard-val { color: #FFFFFF; }
.pcard-val span { font-size: .68rem; font-weight: 500; letter-spacing: 0; color: var(--muted); margin-left: .25rem; }
.pcard.on .pcard-val span { color: #A1A1AA; }
.pcard-foot { font-size: .68rem; margin-top: .3rem; }

/* tahapan halaman Tentang */
.step { display: flex; gap: 1rem; align-items: flex-start; background: var(--tile); border-radius: 18px; padding: 1rem 1.1rem; margin: .55rem 0; }
.step-no { font-weight: 700; font-size: .78rem; background: var(--ink); color: var(--contrast); width: 34px; height: 34px; border-radius: 11px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.step-name { font-weight: 700; margin-bottom: .18rem; color: var(--ink); }
.step-text { color: var(--muted); font-size: .82rem; line-height: 1.55; }
@media (max-width: 850px) { .block-container { padding: 1rem .8rem; } .hero-value { font-size: 1.7rem; } }
"""


def nav_css(badge: int | None) -> str:
    """Gaya per tombol navigasi: ikon, keadaan aktif, lebar rail atau menu penuh."""
    open_ = bool(st.session_state.nav_open)
    page = st.session_state.page
    group_active = page in ("CEP", "EDA")
    width = 292 if open_ else 90
    rules = [
        f'[data-testid="stSidebar"] {{ width:{width}px !important; min-width:{width}px !important; max-width:{width}px !important; }}',
    ]
    if open_:
        rules.append('[class*="st-key-nav_"], [class*="st-key-nav_"] [data-testid="stButton"] { width:100% !important; }')
        rules.append('[class*="st-key-nav_"] button { width:100% !important; justify-content:flex-start !important; padding:0 16px !important; }')
        rules.append('[class*="st-key-nav_"] button::before { margin-right:14px; }')
        rules.append('.st-key-nav_cep, .st-key-nav_eda { margin-left:24px; padding-left:12px; border-left:1.5px solid var(--line); }')
        rules.append('.st-key-nav_cep button, .st-key-nav_eda button { height:44px !important; min-height:44px !important; }')
        rules.append('.st-key-nav_theme { position:absolute; left:16px; right:16px; bottom:18px; width:auto !important; }')
        rules.append(
            '.st-key-nav_group button::after { content:"' + ("\u2212" if st.session_state.group_open else "+") +
            '"; margin-left:auto; font-size:1.25rem; font-weight:500; }'
        )
    else:
        rules.append('[class*="st-key-nav_"] { display:flex !important; justify-content:center !important; }')
        rules.append('[class*="st-key-nav_"] button { width:48px !important; justify-content:center !important; padding:0 !important; }')
        rules.append('[class*="st-key-nav_"] button > div { display:none !important; }')
        rules.append('[class*="st-key-nav_"] button::before { margin:0; }')
        rules.append('.st-key-nav_cep button, .st-key-nav_eda button { width:42px !important; height:42px !important; min-height:42px !important; }')
        rules.append('.st-key-nav_theme { position:absolute; left:0; right:0; bottom:18px; width:auto !important; }')
    if badge:
        text = "99+" if badge > 99 else str(badge)
        if open_:
            rules.append(
                '.st-key-nav_cep button::after { content:"' + text + '"; margin-left:auto; min-width:24px; height:24px; padding:0 7px; '
                'border-radius:999px; background:var(--ink); color:var(--contrast); font-size:.72rem; font-weight:700; '
                'display:flex; align-items:center; justify-content:center; }'
            )
        else:
            rules.append(
                '.st-key-nav_cep button::after { content:""; position:absolute; top:5px; right:5px; width:9px; height:9px; '
                'border-radius:50%; background:var(--ink); border:2px solid var(--rail-a); }'
            )
    items = [
        ("nav_home", "Home", "home", False), ("nav_group", None, "layers", False),
        ("nav_cep", "CEP", "activity", True), ("nav_eda", "EDA", "bars", True),
        ("nav_settings", "Settings", "sliders", False), ("nav_download", "Download", "download", False),
        ("nav_about", "About", "info", False),
    ]
    for key, target, icon, sub in items:
        active = group_active if target is None else page == target
        if active and not sub:
            fg, bg, shadow = C["contrast"], C["ink"], "0 8px 18px var(--shadow)"
        elif active and sub:
            fg, bg, shadow = C["ink"], C["subactive"], "0 4px 14px var(--shadow)"
        else:
            fg, bg, shadow = C["ink"], "transparent", "none"
        rules.append(
            f'.st-key-{key} button {{ background:{bg} !important; color:{fg} !important; box-shadow:{shadow} !important; }}'
        )
        if active and not sub:
            rules.append(f'.st-key-{key} button:hover {{ background:{bg} !important; }}')
        rules.append(f'.st-key-{key} button::before {{ background-image:{svg_uri(icon, fg)}; }}')
    rules.append(f'.st-key-nav_toggle button::before {{ background-image:{svg_uri("chevrons-left" if open_ else "chevrons-right", C["ink"])}; }}')
    rules.append(f'.st-key-nav_theme button::before {{ background-image:{svg_uri("sun" if DARK else "moon", C["ink"])}; }}')
    rules.append(f'.st-key-btn_refresh button::before {{ background-image:{svg_uri("refresh", C["ink"])}; }}')
    return "\n".join(rules)


DARK_CSS = """
[data-testid="stDataFrame"] { filter: invert(.9) hue-rotate(180deg); }
[data-testid="stSlider"] p, [data-testid="stSlider"] div { color: var(--ink); }
"""


def inject_css(badge: int | None) -> None:
    extra = DARK_CSS if DARK else ""
    st.markdown(f"<style>{BASE_CSS}\n{css_variables()}\n{nav_css(badge)}\n{extra}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Fungsi bantu umum
# ---------------------------------------------------------------------------
def fmt(value: object, digits: int = 2) -> str:
    try:
        number = float(value)
        return f"{number:,.{digits}f}" if np.isfinite(number) else "—"
    except (TypeError, ValueError):
        return "—"


def sfmt(value: object, digits: int = 2) -> str:
    try:
        number = float(value)
        return f"{number:+,.{digits}f}" if np.isfinite(number) else "—"
    except (TypeError, ValueError):
        return "—"


def id_date(ts: pd.Timestamp, time: bool = False, year: bool = False) -> str:
    text = f"{ts.day} {MONTHS[ts.month - 1]}"
    if year:
        text += f" {ts.year}"
    if time:
        text += f", {ts:%H:%M}"
    return text


def tile(label: str, value: str, foot: str = "") -> str:
    return (
        f'<div class="tile"><div class="tile-label">{label}</div>'
        f'<div class="tile-value">{value}</div><div class="tile-foot">{foot}</div></div>'
    )


def sec_head(title: str, sub: str = "") -> str:
    right = f'<div class="sec-sub">{sub}</div>' if sub else ""
    return f'<div class="sec-head"><div class="sec-title">{title}</div>{right}</div>'


def md(html: str) -> None:
    st.markdown(html, unsafe_allow_html=True)


def pattern_text(row) -> str:
    labels: list[str] = []
    if bool(row.get("alarm", False)):
        labels.append("Alarm EWMA")
    if bool(row.get("repetition", False)):
        labels.append("Repetition")
    trend = int(row.get("trend", 0) or 0)
    if trend > 0:
        labels.append("Tren naik")
    elif trend < 0:
        labels.append("Tren turun")
    if bool(row.get("absence", False)):
        labels.append("Absence")
    return " · ".join(labels) if labels else "—"


def pattern_icon(row) -> str:
    if bool(row.get("absence", False)):
        return "clock"
    trend = int(row.get("trend", 0) or 0)
    if trend > 0:
        return "trend-up"
    if trend < 0:
        return "trend-down"
    if bool(row.get("repetition", False)):
        return "repeat"
    return "alert"


def pattern_mask(df: pd.DataFrame, label: str) -> pd.Series:
    if label == "Alarm EWMA":
        return df["alarm"].astype(bool)
    if label == "Repetition":
        return df["repetition"].astype(bool)
    if label == "Tren naik":
        return df["trend"] == 1
    if label == "Tren turun":
        return df["trend"] == -1
    if label == "Absence":
        return df["absence"].astype(bool)
    return (
        df["alarm"].astype(bool) | df["repetition"].astype(bool) | (df["trend"] != 0) | df["absence"].astype(bool)
    )


def build_export_zip(frame: pd.DataFrame, analysis: pd.DataFrame, summary: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("carbon_intensity_data.csv", frame.to_csv(index=False))
        archive.writestr("carbon_intensity_analysis.csv", analysis.to_csv(index=False))
        archive.writestr("carbon_intensity_cep_summary.csv", summary.to_csv(index=False))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Data dan mesin analisis (logika inti tidak diubah)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=300, max_entries=8)
def load_dataset(days: int, mode: str, path: str) -> tuple[pd.DataFrame, str]:
    dataset = get_data(days=days, mode=mode, path=Path(path))
    return dataset.frame, dataset.source


@st.cache_data(show_spinner=False, max_entries=12)
def run_engines(frame: pd.DataFrame, reference_days: int, lam: float, alpha: float, cep_window: int):
    """Menjalankan engine asli dengan lambda/alpha EWMA dan window CEP yang dapat diatur."""
    frame = frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["forecast"] = pd.to_numeric(frame["forecast"], errors="coerce")
    frame["actual"] = pd.to_numeric(frame["actual"], errors="coerce")
    frame["error"] = frame["actual"] - frame["forecast"]
    frame = frame.dropna(subset=["timestamp"]).drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)

    reference, monitoring = split_reference(frame, reference_days)
    if len(reference) < MIN_REFERENCE_ROWS:
        raise ValueError(
            f"Data referensi hanya memiliki {len(reference)} observasi lengkap; core engine membutuhkan minimal {MIN_REFERENCE_ROWS}."
        )
    if monitoring.empty:
        raise ValueError("Periode monitoring kosong. Pilih rentang data yang lebih panjang.")

    baseline = compute_baseline(reference)
    if not np.isfinite(baseline.robust_std) or baseline.robust_std <= 0:
        raise ValueError("Variasi galat referensi tidak cukup untuk membentuk batas kendali yang valid.")

    normalizer = Normalizer.global_(baseline)
    limit = calibrate_limit(normalizer, reference, lam, alpha)
    complete = pd.concat([reference, monitoring], ignore_index=True).sort_values("timestamp").reset_index(drop=True)

    signals = run_ewma(EWMA(normalizer, lam, limit), complete)
    cep_engine = CEP(
        EWMA(normalizer, lam, limit),
        repeat=REPEAT,
        window=cep_window,
        steps=STEPS,
        level=LEVEL,
        gap=GAP,
    )
    detections = detect(cep_engine, complete)
    quality = quality_report(frame)

    analysis = complete.merge(signals, on="timestamp", how="left").merge(
        detections[["timestamp", "streak", "repetition", "trend", "absence"]],
        on="timestamp", how="left",
    )
    analysis["ewma_error"] = baseline.mean + analysis["z"] * baseline.robust_std
    analysis["ewma_upper_error"] = baseline.mean + analysis["ucl"] * baseline.robust_std
    analysis["ewma_lower_error"] = baseline.mean + analysis["lcl"] * baseline.robust_std
    return frame, reference, monitoring, complete, baseline, signals, detections, analysis, quality, limit


def compute_view() -> SimpleNamespace:
    days = int(st.session_state.data_days)
    raw_frame, source_name = load_dataset(days, str(st.session_state.source_mode), str(DATA_CACHE))
    if not {"timestamp", "forecast", "actual"}.issubset(raw_frame.columns):
        raise ValueError("Dataset memerlukan kolom timestamp, forecast, dan actual.")
    (frame, reference, monitoring, complete, baseline, signals, detections, analysis, quality, limit) = run_engines(
        raw_frame,
        max(3, min(days - 1, round(days * 0.7))),
        float(st.session_state.ewma_lambda),
        float(st.session_state.ewma_alpha),
        int(st.session_state.cep_window),
    )
    monitor_start = monitoring["timestamp"].min()
    monitor_analysis = analysis[analysis["timestamp"] >= monitor_start].copy()
    monitor_detections = detections[detections["timestamp"] >= monitor_start].copy()
    summary = summarize(detections, monitor_start)
    summary["pattern"] = summary["pattern"].map(PATTERN_LABELS).fillna(summary["pattern"])
    tmax = analysis["timestamp"].max()
    recent = monitor_analysis[monitor_analysis["timestamp"] > tmax - pd.Timedelta(hours=24)]
    return SimpleNamespace(
        source_name=source_name, frame=frame, reference=reference, monitoring=monitoring, complete=complete,
        baseline=baseline, signals=signals, detections=detections, analysis=analysis, quality=quality, limit=limit,
        monitor_start=monitor_start, monitor_analysis=monitor_analysis, monitor_detections=monitor_detections,
        summary=summary, latest=complete.iloc[-1], latest_row=analysis.iloc[-1], tmax=tmax,
        alarm_count=int(monitor_detections["alarm"].sum()),
        flagged=int(pattern_mask(monitor_analysis, "Semua").sum()),
        recent_alarms=int(recent["alarm"].astype(bool).sum()),
    )


def pattern_stat(view: SimpleNamespace, label: str, column: str) -> float:
    try:
        return float(view.summary.set_index("pattern").at[label, column])
    except (KeyError, ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Navigasi: sidebar dan header
# ---------------------------------------------------------------------------
def goto(page: str) -> None:
    st.session_state.page = page


def toggle(key: str) -> None:
    st.session_state[key] = not st.session_state[key]


def toggle_theme() -> None:
    st.session_state.theme = "Terang" if st.session_state.theme == "Gelap" else "Gelap"


def build_sidebar() -> None:
    sb = st.sidebar
    open_ = bool(st.session_state.nav_open)
    if open_:
        left, right = sb.columns([4, 1], vertical_alignment="center")
        left.markdown('<div class="brand"><div class="sidebar-mark">C</div><span class="brand-name">Menu</span></div>', unsafe_allow_html=True)
        right.button("Ciutkan", key="nav_toggle", help="Ciutkan menu", on_click=toggle, args=("nav_open",))
    else:
        sb.markdown('<div class="sidebar-center"><div class="sidebar-mark">C</div></div>', unsafe_allow_html=True)
        sb.button("Perluas", key="nav_toggle", help="Perluas menu", on_click=toggle, args=("nav_open",))
    sb.markdown('<div class="sidebar-rule"></div>', unsafe_allow_html=True)

    def tip(text: str) -> str | None:
        return None if open_ else text

    sb.button("Dashboard", key="nav_home", help=tip("Dashboard"), on_click=goto, args=("Home",))
    sb.button("Analisis", key="nav_group", help=tip("Analisis"), on_click=toggle, args=("group_open",))
    if st.session_state.group_open:
        sb.button("Pola CEP", key="nav_cep", help="Alarm EWMA dalam 24 jam terakhir" if not open_ else None, on_click=goto, args=("CEP",))
        sb.button("EDA dan kualitas data", key="nav_eda", help=tip("EDA dan kualitas data"), on_click=goto, args=("EDA",))
    sb.button("Pengaturan", key="nav_settings", help=tip("Pengaturan"), on_click=goto, args=("Settings",))
    sb.button("Unduh data", key="nav_download", help=tip("Unduh data"), on_click=goto, args=("Download",))
    sb.button("Tentang", key="nav_about", help=tip("Tentang dashboard"), on_click=goto, args=("About",))
    sb.button(
        "Mode terang" if DARK else "Mode gelap", key="nav_theme",
        help=tip("Ganti tema"), on_click=toggle_theme,
    )


PAGE_TITLES = {
    "Home": ("Dashboard", "Pemantauan galat prakiraan intensitas karbon"),
    "CEP": ("Pola CEP", "Alarm, pengulangan, tren dan jeda data"),
    "EDA": ("EDA dan kualitas data", "Konteks galat normal pada periode referensi"),
    "Settings": ("Pengaturan", "Periode data dan parameter deteksi"),
    "Download": ("Unduh data", "Ekspor data dan hasil deteksi"),
    "About": ("Tentang dashboard", "Cara empat engine bekerja bersama"),
}


def build_header(view: SimpleNamespace | None) -> None:
    page = st.session_state.page
    title, sub = PAGE_TITLES[page]
    show_tools = page in ("Home", "CEP", "EDA", "Download")
    if page == "Home":
        c1, c2, c3 = st.columns([4, 2.4, 0.5], vertical_alignment="center")
        with c2:
            st.text_input("Cari", key="search_q", placeholder="Cari tanggal atau pola", label_visibility="collapsed")
        with c3:
            if st.button("Perbarui", key="btn_refresh", help="Ambil ulang data dari sumber yang dipilih"):
                load_dataset.clear()
                run_engines.clear()
                st.rerun()
    elif show_tools:
        c1, c3 = st.columns([8, 0.5], vertical_alignment="center")
        with c3:
            if st.button("Perbarui", key="btn_refresh", help="Ambil ulang data dari sumber yang dipilih"):
                load_dataset.clear()
                run_engines.clear()
                st.rerun()
    else:
        (c1,) = st.columns(1)
    with c1:
        pill = (
            f'<span class="pill"><span class="status-dot"></span>{st.session_state.data_days} hari · '
            f'λ {st.session_state.ewma_lambda:.2f} · α {st.session_state.ewma_alpha:.0%}</span>'
            if page == "Home" else ""
        )
        md(f'<div style="display:flex;align-items:center;gap:.9rem"><div><div class="page-title">{title}</div>'
           f'<div class="page-sub">{sub}</div></div>{pill}</div>')
    md('<div class="head-rule"></div>')


# ---------------------------------------------------------------------------
# Grafik utama
# ---------------------------------------------------------------------------
def chart_theme(fig: go.Figure, height: int) -> None:
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="DM Sans, sans-serif", color=C["muted"], size=11),
        margin=dict(l=8, r=8, t=26, b=58), height=height, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, bgcolor="rgba(0,0,0,0)", font=dict(size=11, color=C["muted"])),
        hoverlabel=dict(bgcolor=C["hero"], bordercolor=C["hero"], font=dict(color="#F4F4F5", family="DM Sans, sans-serif")),
        uirevision="carbon-intensity",
    )
    fig.update_xaxes(showgrid=False, zeroline=False, showline=False, tickfont=dict(size=11))
    fig.update_yaxes(gridcolor=C["line"], gridwidth=1, zeroline=False, showline=False, tickfont=dict(size=11))


def show_chart(fig: go.Figure) -> None:
    st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False, "scrollZoom": False, "displayModeBar": False})


def soft_chart(fig: go.Figure, height: int = 300) -> None:
    chart_theme(fig, height)
    fig.update_layout(margin=dict(l=8, r=8, t=26, b=34))
    show_chart(fig)


def time_ticks(t0: pd.Timestamp, t1: pd.Timestamp, skip: pd.Timestamp | None, count: int = 6):
    span = t1 - t0
    points = pd.date_range(t0, t1, periods=count + 2)[1:-1]
    values, texts = [], []
    for ts in points:
        if skip is not None and abs(ts - skip) < span * 0.075:
            continue
        values.append(ts.to_pydatetime())
        texts.append(id_date(ts) if span >= pd.Timedelta(days=2) else f"{ts:%H:%M}")
    return values, texts


PLOT_PX = 364.0   # tinggi area plot perkiraan (px) untuk menghitung posisi lingkaran tanggal
PAD_PX = 48.0     # baris bawah grafik untuk lingkaran tanggal dan label sumbu waktu


def nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    raw = max((hi - lo) / count, 1e-9)
    magnitude = 10 ** np.floor(np.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    first = np.ceil(lo / step) * step
    return [float(v) for v in np.arange(first, hi + step / 2, step)]


def add_highlight(fig, ts, y, axis_lo, total, t0, t1, value_text, label_text):
    """Titik sorot: garis putus ke bawah, lingkaran tanggal, dan gelembung nilai."""
    span = t1 - t0
    pill_y = axis_lo + total * (24.0 / PLOT_PX)
    line_end = pill_y + total * (20.0 / PLOT_PX)
    fig.add_shape(type="line", x0=ts, x1=ts, y0=y, y1=line_end, line=dict(color=C["mid"], width=1.2, dash="dot"), layer="below")
    fig.add_trace(go.Scatter(
        x=[ts], y=[y], mode="markers", showlegend=False, hoverinfo="skip",
        marker=dict(size=13, color=C["ink"], line=dict(color=C["panel"], width=3)),
    ))
    pill_text = f"{ts.day}<br>{MONTHS[ts.month - 1]}" if span >= pd.Timedelta(days=2) else f"{ts:%H:%M}"
    fig.add_trace(go.Scatter(
        x=[ts], y=[pill_y], mode="markers+text", showlegend=False, hoverinfo="skip",
        marker=dict(size=40, color=C["ink"]), text=[pill_text], textposition="middle center",
        textfont=dict(color=C["contrast"], size=10, family="DM Sans, sans-serif"),
    ))
    frac = (ts - t0) / span if span > pd.Timedelta(0) else 0.5
    anchor = "left" if frac < 0.12 else "right" if frac > 0.88 else "center"
    fig.add_annotation(
        x=ts, y=y, xanchor=anchor, yanchor="bottom", yshift=16, showarrow=False, align="center",
        text=f"<b>{value_text}</b><br>{label_text}", font=dict(size=12, color=C["ink"], family="DM Sans, sans-serif"),
    )


def main_chart(view: SimpleNamespace, mode: str, days: int | None, focus: str) -> go.Figure:
    analysis = view.analysis
    t1 = analysis["timestamp"].max()
    shown = analysis if days is None else analysis[analysis["timestamp"] >= t1 - pd.Timedelta(days=days)]
    shown = shown.reset_index(drop=True)
    t0 = shown["timestamp"].min()
    fig = go.Figure()

    if mode == "Galat":
        fig.add_trace(go.Scatter(
            x=shown["timestamp"], y=shown["error"], mode="lines", name="Galat aktual",
            line=dict(color=C["mid"], width=1), opacity=0.7,
        ))
        fig.add_trace(go.Scatter(
            x=shown["timestamp"], y=shown["ewma_upper_error"], mode="lines", name="Batas kendali",
            line=dict(color=C["muted"], width=1.1, dash="dash"), legendgroup="limit",
        ))
        fig.add_trace(go.Scatter(
            x=shown["timestamp"], y=shown["ewma_lower_error"], mode="lines", name="Batas bawah", showlegend=False,
            line=dict(color=C["muted"], width=1.1, dash="dash"), legendgroup="limit",
            fill="tonexty", fillcolor="rgba(130,130,140,.08)",
        ))
        fig.add_trace(go.Scatter(
            x=shown["timestamp"], y=shown["ewma_error"], mode="lines", name="EWMA galat",
            line=dict(color=C["ink"], width=2.4, shape="spline", smoothing=0.6),
        ))
        fig.add_trace(go.Scatter(
            x=[t0, t1], y=[view.baseline.mean, view.baseline.mean], mode="lines", name="Rata-rata referensi",
            line=dict(color=C["mid"], width=1, dash="dot"), hoverinfo="skip",
        ))
        marker_rows = shown[shown["timestamp"] >= view.monitor_start]
        labels = PATTERN_ORDER if focus == "Semua" else [focus]
        for label in labels:
            rows = marker_rows[pattern_mask(marker_rows, label)]
            if rows.empty:
                continue
            fig.add_trace(go.Scatter(
                x=rows["timestamp"], y=rows["ewma_error"], mode="markers", name=label,
                marker=dict(symbol=PATTERN_SYMBOL[label], size=9, color=C["ink"] if label in ("Alarm EWMA", "Tren naik") else C["muted"],
                            line=dict(color=C["panel"], width=1.2)),
                customdata=np.column_stack([rows["error"], rows["z"]]),
                hovertemplate="%{x|%d %b %Y %H:%M} UTC<br>EWMA galat: %{y:.2f}<br>Galat mentah: %{customdata[0]:.2f}"
                              "<br>Statistik EWMA: %{customdata[1]:.3f}<extra>" + label + "</extra>",
            ))
        series = pd.concat([shown["ewma_error"], shown["ewma_upper_error"], shown["ewma_lower_error"]]).dropna()
        raw = shown["error"].dropna()
        lo = float(min(series.min(), raw.quantile(0.01))) if len(raw) else float(series.min())
        hi = float(max(series.max(), raw.quantile(0.99))) if len(raw) else float(series.max())
        candidates = marker_rows[pattern_mask(marker_rows, focus)] if len(marker_rows) else marker_rows
        if len(candidates):
            pick = candidates.loc[candidates["z"].abs().idxmax()]
            label_text = focus if focus != "Semua" else pattern_text(pick).split(" · ")[0]
        else:
            deviation = (shown["ewma_error"] - view.baseline.mean).abs()
            pick = shown.loc[deviation.idxmax()]
            label_text = "Puncak galat"
        highlight_y, highlight_value = float(pick["ewma_error"]), sfmt(pick["ewma_error"])
        yaxis_title = "Galat (gCO₂/kWh)"
    else:
        fig.add_trace(go.Scatter(
            x=shown["timestamp"], y=shown["forecast"], mode="lines", name="Forecast",
            line=dict(color=C["mid"], width=1.8, shape="spline", smoothing=0.6),
        ))
        fig.add_trace(go.Scatter(
            x=shown["timestamp"], y=shown["actual"], mode="lines", name="Aktual",
            line=dict(color=C["ink"], width=2.4, shape="spline", smoothing=0.6),
            fill="tonexty", fillcolor="rgba(130,130,140,.10)",
        ))
        lo = float(min(shown["forecast"].min(), shown["actual"].min()))
        hi = float(max(shown["forecast"].max(), shown["actual"].max()))
        pick = shown.loc[shown["actual"].idxmax()]
        highlight_y, highlight_value = float(pick["actual"]), fmt(pick["actual"], 1)
        label_text = "Puncak aktual"
        yaxis_title = "Intensitas karbon (gCO₂/kWh)"

    span_y = max(hi - lo, 1e-6)
    data_lo, data_hi = lo - 0.06 * span_y, hi + 0.28 * span_y
    total = (data_hi - data_lo) / (1 - PAD_PX / PLOT_PX)
    axis_lo = data_hi - total
    chart_theme(fig, 400)
    fig.update_layout(margin=dict(l=8, r=22, t=26, b=10))
    ticks, texts = time_ticks(t0, t1, pick["timestamp"])
    fig.update_xaxes(tickmode="array", tickvals=ticks, ticktext=texts, range=[t0, t1], ticklabelposition="inside")
    fig.update_yaxes(range=[axis_lo, data_hi], tickmode="array", tickvals=nice_ticks(data_lo, data_hi),
                     title_text=yaxis_title, title_font=dict(size=11))
    add_highlight(fig, pick["timestamp"], highlight_y, axis_lo, total, t0, t1, highlight_value, label_text)
    if mode == "Galat" and t0 < view.monitor_start < t1:
        fig.add_shape(type="line", x0=view.monitor_start, x1=view.monitor_start, y0=0, y1=1, yref="paper",
                      line=dict(color=C["mid"], width=1, dash="dot"))
        fig.add_annotation(x=view.monitor_start, y=1, yref="paper", xanchor="left", yanchor="top", showarrow=False,
                           text="  Mulai monitoring", font=dict(size=10, color=C["muted"]))
    return fig


# ---------------------------------------------------------------------------
# Komponen halaman Home
# ---------------------------------------------------------------------------
HERO_DOTS = [(176, 72, 4), (192, 86, 3), (168, 94, 5), (186, 106, 2.5), (204, 98, 3.5), (176, 124, 3), (194, 136, 5),
             (166, 146, 2.5), (210, 122, 2.5), (186, 160, 3.5), (202, 174, 2.5), (176, 182, 4), (216, 152, 3), (196, 202, 3)]
HERO_DECOR = (
    '<svg class="hero-decor" viewBox="0 0 260 240" preserveAspectRatio="xMaxYMin meet" xmlns="http://www.w3.org/2000/svg">'
    '<path d="M150 0H260V124C242 76 204 56 180 46C160 38 142 22 150 0Z" fill="#F4F4F5"/>'
    + "".join(f'<circle cx="{x}" cy="{y}" r="{r}" fill="#8B8B92"/>' for x, y, r in HERO_DOTS)
    + '</svg>'
)


def hero_html(view: SimpleNamespace) -> str:
    latest = view.latest
    return (
        '<div class="hero">' + HERO_DECOR + '<div class="hero-body">'
        '<div><div class="hero-label">Aktual terakhir</div>'
        f'<div class="hero-value">{fmt(latest["actual"], 1)}<span>gCO₂/kWh</span></div></div>'
        f'<div class="hero-chip-row"><div class="hero-chip"></div>{svg("wave", 22, 1.6)}</div>'
        '<div class="hero-nums">'
        f'<div><i>Forecast</i><b>{fmt(latest["forecast"], 1)}</b></div>'
        f'<div><i>Galat</i><b>{sfmt(latest["error"])}</b></div>'
        f'<div><i>Z EWMA</i><b>{sfmt(view.latest_row["z"], 2)}</b></div></div>'
        f'<div class="hero-foot"><span>{id_date(latest["timestamp"], time=True, year=True).upper()} UTC</span><b>NESO · GB</b></div>'
        '</div></div>'
    )


def ledger_rows(view: SimpleNamespace, mode: str, query: str) -> pd.DataFrame:
    df = view.monitor_analysis
    if mode == "Alarm EWMA saja":
        df = df[pattern_mask(df, "Alarm EWMA")]
    elif mode == "Pola CEP saja":
        df = df[pattern_mask(df, "Repetition") | pattern_mask(df, "Tren naik") | pattern_mask(df, "Tren turun") | pattern_mask(df, "Absence")]
    elif mode == "Alarm dan pola":
        df = df[pattern_mask(df, "Semua")]
    df = df.sort_values("timestamp", ascending=False).copy()
    if query and not df.empty:
        text = df.apply(lambda r: f"{id_date(r['timestamp'], True, True)} {r['timestamp']:%Y-%m-%d %H:%M} {pattern_text(r)}".lower(), axis=1)
        df = df[text.str.contains(query.lower(), regex=False)]
    return df


def activity_html(rows: pd.DataFrame) -> str:
    out = []
    for rec in rows.to_dict("records"):
        out.append(
            '<div class="act-row">'
            f'<div class="act-ico">{svg(pattern_icon(rec), 18, 1.7)}</div>'
            f'<div class="act-main"><div class="act-title">{pattern_text(rec)}</div>'
            f'<div class="act-sub">{id_date(rec["timestamp"], True)} UTC</div></div>'
            f'<div class="act-val">{sfmt(rec["error"])}<small>z {sfmt(rec["z"], 2)}</small></div></div>'
        )
    return "".join(out)


def pattern_cards(view: SimpleNamespace, focus: str) -> None:
    columns = st.columns(len(PATTERN_ORDER), gap="small")
    for column, label in zip(columns, PATTERN_ORDER):
        count = int(pattern_stat(view, label, "monitoring_events"))
        rate = pattern_stat(view, label, "monitoring_per_day")
        with column:
            md(
                f'<div class="pcard{" on" if focus == label else ""}"><div class="pcard-ico">{svg(PATTERN_ICON[label], 22, 1.6)}</div>'
                f'<div class="pcard-label">{label}</div><div class="pcard-val">{count:,}<span>kejadian</span></div>'
                f'<div class="pcard-foot">{rate:,.2f} / hari</div></div>'
            )


def render_home(view: SimpleNamespace) -> None:
    left, right = st.columns([1, 1.5], gap="large")
    latest_alarm = bool(view.latest_row["alarm"])

    with left:
        md(sec_head("Status terbaru", f'<span class="pill"><span class="status-dot"></span>{"Alarm" if latest_alarm else "Normal"}</span>'))
        md(hero_html(view))
        md('<div style="height:.8rem"></div>')
        monitor_errors = view.monitoring["error"].dropna()
        mean_error = monitor_errors.mean() if not monitor_errors.empty else np.nan
        error_var = monitor_errors.var() if len(monitor_errors) > 1 else np.nan
        span_days = max(0.0, (view.complete["timestamp"].max() - view.complete["timestamp"].min()).total_seconds() / 86400)
        k1, k2, k3 = st.columns(3, gap="small")
        with k1:
            md(tile("Rata-rata galat", fmt(mean_error), "Monitoring"))
        with k2:
            md(tile("Varians galat", fmt(error_var), "Monitoring · satuan²"))
        with k3:
            md(tile("Periode data", f"{span_days:.1f} hari", f"{len(view.complete):,} observasi"))
        md('<div style="height:1.3rem"></div>')
        h1, h2 = st.columns([1.2, 1], vertical_alignment="center")
        with h1:
            md(sec_head("Aktivitas terbaru"))
        with h2:
            mode = st.selectbox("Tampilkan", ["Alarm dan pola", "Alarm EWMA saja", "Pola CEP saja", "Semua monitoring"],
                                label_visibility="collapsed", key="ledger_filter")
        rows = ledger_rows(view, mode, str(st.session_state.search_q).strip())
        if rows.empty:
            md('<div class="empty-note">Tidak ada catatan untuk filter atau kata kunci ini.</div>')
        else:
            md(activity_html(rows.head(5)))
            md(f'<div class="sec-sub" style="margin-top:.6rem">Menampilkan {min(5, len(rows))} dari {len(rows):,} catatan · lengkapnya di halaman Pola CEP</div>')

    with right:
        md('<div class="divider-mark"></div>')
        t1, t2 = st.columns([2.2, 1], vertical_alignment="center")
        with t1:
            st.radio("Grafik", ["Galat", "Intensitas"], horizontal=True, label_visibility="collapsed", key="home_chart_mode")
        with t2:
            st.selectbox("Rentang", list(RANGE_DAYS), label_visibility="collapsed", key="home_range")
        mode_chart = st.session_state.home_chart_mode
        show_chart(main_chart(view, mode_chart, RANGE_DAYS[st.session_state.home_range], st.session_state.pattern_focus if mode_chart == "Galat" else "Alarm EWMA"))
        md(
            f'<div class="sec-sub" style="margin:-.2rem 0 .9rem">Waktu dalam UTC · garis putus-putus vertikal menandai awal monitoring ({id_date(view.monitor_start, year=True)}). '
            + ("Batas kendali dipetakan ke skala galat asli memakai baseline referensi." if mode_chart == "Galat" else "Area abu-abu adalah selisih antara aktual dan forecast.")
            + '</div>'
        )
        md(sec_head("Pola kejadian", "Periode monitoring"))
        st.radio("Tandai pada grafik", ["Semua"] + PATTERN_ORDER, horizontal=True, label_visibility="collapsed",
                 key="pattern_focus", disabled=mode_chart != "Galat")
        md('<div style="height:.2rem"></div>')
        pattern_cards(view, st.session_state.pattern_focus)


# ---------------------------------------------------------------------------
# Halaman lain
# ---------------------------------------------------------------------------
def render_cep(view: SimpleNamespace) -> None:
    k1, k2, k3, k4 = st.columns(4, gap="medium")
    monitor_days = max(1.0, (view.monitoring["timestamp"].max() - view.monitoring["timestamp"].min()).total_seconds() / 86400)
    with k1:
        md(tile("Alarm EWMA", f"{view.alarm_count:,}", "Periode monitoring"))
    with k2:
        md(tile("Alarm 24 jam terakhir", f"{view.recent_alarms:,}", "Dihitung dari observasi terakhir"))
    with k3:
        md(tile("Observasi bertanda CEP", f"{view.flagged:,}", "Alarm, repetition, tren atau absence"))
    with k4:
        md(tile("Laju alarm", f"{view.alarm_count / monitor_days:,.2f} / hari", "Rata-rata periode monitoring"))
    md('<div style="height:1.2rem"></div>')
    left, right = st.columns([1.25, 1.1], gap="large")
    with left:
        md(sec_head("Alarm per hari", "Gelap = monitoring · abu-abu = referensi"))
        day = view.analysis["timestamp"].dt.floor("D")
        daily = view.analysis.assign(day=day).groupby("day")["alarm"].apply(lambda s: int(s.astype(bool).sum()))
        colors = [C["ink"] if d >= view.monitor_start.floor("D") else C["mid"] for d in daily.index]
        fig = go.Figure(go.Bar(x=daily.index, y=daily.values, marker_color=colors, name="Alarm"))
        fig.update_layout(bargap=0.35)
        fig.update_yaxes(title_text="Jumlah alarm")
        tick_values, tick_texts = time_ticks(daily.index.min(), daily.index.max(), None, 5)
        fig.update_xaxes(tickmode="array", tickvals=tick_values, ticktext=tick_texts)
        soft_chart(fig, 320)
        md('<div class="sec-sub">Hari pertama dan terakhir bisa belum penuh 48 observasi.</div>')
    with right:
        md(sec_head("Perbandingan laju kejadian", "per hari"))
        shown = view.summary.rename(columns={
            "pattern": "Pola", "reference_events": "Ref.", "reference_per_day": "Ref./hari",
            "monitoring_events": "Mon.", "monitoring_per_day": "Mon./hari",
        })
        st.dataframe(shown, use_container_width=True, hide_index=True,
                     column_config={"Ref./hari": st.column_config.NumberColumn(format="%.2f"),
                                    "Mon./hari": st.column_config.NumberColumn(format="%.2f")})
        md('<div class="sec-sub" style="margin-top:.5rem">Ref. = periode referensi, Mon. = periode monitoring. Durasinya berbeda, jadi bandingkan laju per hari.</div>')
    md('<div style="height:1.2rem"></div>')
    h1, h2 = st.columns([3, 1], vertical_alignment="center")
    with h1:
        md(sec_head("Catatan alarm dan pola", "Setiap baris menghubungkan data, statistik EWMA, batas kendali dan pola CEP"))
    with h2:
        mode = st.selectbox("Filter", ["Alarm dan pola", "Alarm EWMA saja", "Pola CEP saja", "Semua monitoring"],
                            label_visibility="collapsed", key="ledger_filter")
    rows = ledger_rows(view, mode, "")
    if rows.empty:
        md('<div class="empty-note">Tidak ada catatan untuk filter ini.</div>')
        return
    rows = rows.head(200).copy()
    rows["Pola / status"] = rows.apply(pattern_text, axis=1)
    rows = rows.rename(columns={
        "timestamp": "Waktu (UTC)", "forecast": "Forecast", "actual": "Aktual", "error": "Galat",
        "z": "Statistik EWMA", "ewma_lower_error": "Batas bawah", "ewma_upper_error": "Batas atas",
    })
    st.dataframe(
        rows[["Waktu (UTC)", "Forecast", "Aktual", "Galat", "Statistik EWMA", "Batas bawah", "Batas atas", "Pola / status"]],
        use_container_width=True, hide_index=True,
        column_config={
            "Waktu (UTC)": st.column_config.DatetimeColumn(format="DD MMM YYYY, HH:mm"),
            "Forecast": st.column_config.NumberColumn(format="%.2f"),
            "Aktual": st.column_config.NumberColumn(format="%.2f"),
            "Galat": st.column_config.NumberColumn(format="%+.2f"),
            "Statistik EWMA": st.column_config.NumberColumn(format="%.3f"),
            "Batas bawah": st.column_config.NumberColumn(format="%+.2f"),
            "Batas atas": st.column_config.NumberColumn(format="%+.2f"),
        },
    )
    md(f'<div class="sec-sub" style="margin-top:.5rem">Menampilkan {len(rows):,} catatan terbaru · sumber {view.source_name} · λ={st.session_state.ewma_lambda:.2f} · α={st.session_state.ewma_alpha:.0%} · window={st.session_state.cep_window}</div>')


def render_eda(view: SimpleNamespace) -> None:
    q = view.quality
    k1, k2, k3, k4 = st.columns(4, gap="medium")
    with k1:
        md(tile("Baris data", f"{q.get('rows', 0):,}", "Setelah pembersihan"))
    with k2:
        md(tile("Periode hilang", f"{q.get('missing_periods', 0):,}", "Interval 30 menit tanpa data"))
    with k3:
        md(tile("Forecast kosong", f"{q.get('null_forecast', 0):,}", "Nilai tidak tersedia"))
    with k4:
        md(tile("Aktual kosong", f"{q.get('null_actual', 0):,}", "Nilai tidak tersedia"))
    md('<div style="height:1.3rem"></div>')
    e1, e2 = st.columns(2, gap="large")
    with e1:
        md(sec_head("Sebaran galat", "Referensi dan monitoring"))
        hist = go.Figure()
        hist.add_trace(go.Histogram(x=view.reference["error"], nbinsx=32, name="Referensi", marker_color=C["mid"], opacity=0.85))
        hist.add_trace(go.Histogram(x=view.monitoring["error"], nbinsx=32, name="Monitoring", marker_color=C["ink"], opacity=0.55))
        hist.update_layout(barmode="overlay", bargap=0.06)
        hist.update_xaxes(title_text="Galat (aktual − forecast)")
        hist.update_yaxes(title_text="Observasi")
        soft_chart(hist, 300)
    with e2:
        md(sec_head("Variasi galat per jam", "Simpangan baku · UTC"))
        hourly = hourly_profile(view.reference).reset_index()
        bars = go.Figure(go.Bar(x=hourly["hour_utc"], y=hourly["std"], marker_color=C["ink"], name="Simpangan baku"))
        bars.update_layout(bargap=0.3)
        bars.update_xaxes(title_text="Jam UTC")
        bars.update_yaxes(title_text="Simpangan baku galat")
        soft_chart(bars, 300)
    md('<div style="height:1rem"></div>')
    md(sec_head("Baseline referensi", "Dihitung dari periode referensi saja"))
    b1, b2, b3, b4 = st.columns(4, gap="medium")
    with b1:
        md(tile("Observasi referensi", f"{view.baseline.n:,}", "Observasi lengkap"))
    with b2:
        md(tile("Rata-rata", fmt(view.baseline.mean), "gCO₂/kWh"))
    with b3:
        md(tile("Median", fmt(view.baseline.median), "gCO₂/kWh"))
    with b4:
        md(tile("Robust σ", fmt(view.baseline.robust_std), "Skala untuk normalisasi EWMA"))
    md('<div style="height:1rem"></div>')
    md(sec_head("Autokorelasi galat", "Lag dalam satuan 30 menit"))
    acf = autocorrelation(view.reference["error"], lags=(1, 2, 4, 48))
    table = acf.rename_axis("Lag (30 menit)").reset_index(name="Autokorelasi")
    a1, a2 = st.columns([1, 1.4], gap="large")
    with a1:
        st.dataframe(table, use_container_width=True, hide_index=True)
    with a2:
        fig = go.Figure(go.Bar(x=table["Lag (30 menit)"].astype(str), y=table["Autokorelasi"], marker_color=C["ink"]))
        fig.update_layout(bargap=0.5)
        fig.update_yaxes(title_text="Autokorelasi", zeroline=True, zerolinecolor=C["mid"])
        soft_chart(fig, 230)


def render_settings() -> None:
    if st.session_state.flash:
        st.success(st.session_state.flash)
        st.session_state.flash = ""
    md('<div class="panel-note">Atur periode data dan tiga parameter yang berpengaruh langsung pada hasil deteksi. Perubahan diterapkan setelah tombol ditekan.</div>')
    with st.form("carbon_settings_form", border=True):
        s1, s2 = st.columns(2, gap="large")
        with s1:
            setting_days = st.select_slider(
                "Rentang data", options=[7, 14, 30, 60, 90],
                value=int(st.session_state.data_days), format_func=lambda x: f"{x} hari",
            )
            setting_mode_label = st.selectbox(
                "Sumber data", ["Otomatis (API + cache)", "API langsung", "Cache CSV"],
                index={"auto": 0, "live": 1, "csv": 2}.get(st.session_state.source_mode, 0),
            )
            setting_theme = st.radio("Tema tampilan", ["Terang", "Gelap"], horizontal=True, index=0 if st.session_state.theme == "Terang" else 1)
        with s2:
            setting_lambda = st.select_slider(
                "Lambda (λ)", options=[0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.75, 1.00],
                value=float(st.session_state.ewma_lambda), format_func=lambda x: f"{x:.2f}",
            )
            setting_alpha = st.select_slider(
                "Alpha (α)", options=[0.01, 0.02, 0.05, 0.10],
                value=float(st.session_state.ewma_alpha), format_func=lambda x: f"{x:.0%}",
            )
            setting_window = st.slider(
                "Window CEP", min_value=2, max_value=12, value=int(st.session_state.cep_window), step=1,
                help="Jumlah periode 30 menit pada rata-rata galat bergerak. Window 4 berarti dua jam.",
            )
        md('<div class="tiny-label" style="margin:.6rem 0 .5rem">Arti parameter</div>')
        i1, i2, i3 = st.columns(3)
        with i1:
            md('<div class="tile"><b>λ · Respons EWMA</b><div class="tile-foot" style="margin-top:.3rem">Nilai lebih tinggi memberi bobot lebih besar pada observasi terbaru. Nilai rendah membuat sinyal lebih halus.</div></div>')
        with i2:
            md('<div class="tile"><b>α · Batas alarm</b><div class="tile-foot" style="margin-top:.3rem">Target proporsi alarm pada data referensi yang digunakan untuk kalibrasi. Alpha lebih rendah umumnya menghasilkan batas lebih ketat.</div></div>')
        with i3:
            md('<div class="tile"><b>Window · Pola CEP</b><div class="tile-foot" style="margin-top:.3rem">Jumlah periode yang dirata-ratakan untuk membaca tren galat. Satu periode mewakili 30 menit.</div></div>')
        md('<div style="height:.6rem"></div>')
        submitted = st.form_submit_button("Terapkan pengaturan", type="primary", use_container_width=True)
    if submitted:
        mode_map = {"Otomatis (API + cache)": "auto", "API langsung": "live", "Cache CSV": "csv"}
        st.session_state.data_days = int(setting_days)
        st.session_state.source_mode = mode_map[setting_mode_label]
        st.session_state.ewma_lambda = float(setting_lambda)
        st.session_state.ewma_alpha = float(setting_alpha)
        st.session_state.cep_window = int(setting_window)
        st.session_state.theme = setting_theme
        st.session_state.flash = "Pengaturan diterapkan. Buka Dashboard untuk melihat hasil dengan parameter terbaru."
        st.rerun()
    md(sec_head("Pengaturan aktif"))
    a1, a2, a3, a4 = st.columns(4)
    with a1:
        md(tile("Rentang data", f"{st.session_state.data_days} hari"))
    with a2:
        md(tile("Lambda (λ)", f"{st.session_state.ewma_lambda:.2f}"))
    with a3:
        md(tile("Alpha (α)", f"{st.session_state.ewma_alpha:.0%}"))
    with a4:
        md(tile("Window CEP", f"{st.session_state.cep_window} periode"))


def render_about() -> None:
    md(
        '<div class="tile" style="padding:1.3rem 1.4rem"><div class="tiny-label">Tujuan</div>'
        '<div style="font-size:1.35rem;font-weight:700;letter-spacing:-.04em;margin:.5rem 0;color:var(--ink)">Membaca perubahan galat, bukan sekadar melihat angka.</div>'
        '<div class="panel-note" style="font-size:.87rem;margin:0">Dashboard ini memantau selisih antara intensitas karbon aktual dan forecast dari UK Carbon Intensity API. EDA membangun konteks galat normal; EWMA menandai perubahan statistik; CEP mencari pola dari urutan sinyal tersebut.</div></div>'
    )
    md('<div style="height:1.1rem"></div>')
    md(sec_head("Bagaimana empat engine bergabung"))
    stages = [
        ("01", "Data", "Mengambil data per 30 menit, menyelaraskan timestamp, menyimpan cache, dan memeriksa periode yang hilang."),
        ("02", "EDA", "Membagi data menjadi referensi dan monitoring, lalu mengukur rata-rata, median, simpangan baku robust, autokorelasi, dan pola per jam."),
        ("03", "EWMA", "Menghaluskan galat yang telah dinormalisasi dan membandingkannya dengan batas kendali yang dikalibrasi pada periode referensi."),
        ("04", "CEP", "Membaca sinyal secara berurutan untuk menandai alarm berulang, tren naik/turun, dan jeda kedatangan data."),
    ]
    for number, name, description in stages:
        md(f'<div class="step"><div class="step-no">{number}</div><div><div class="step-name">{name}</div><div class="step-text">{description}</div></div></div>')
    md('<div style="height:.8rem"></div>')
    md(sec_head("Panduan membaca hasil"))
    g1, g2, g3 = st.columns(3, gap="medium")
    with g1:
        md('<div class="tile"><b>Alarm EWMA</b><div class="tile-foot" style="margin-top:.35rem">Nilai statistik EWMA melewati batas kendali. Alarm tidak otomatis berarti ada kesalahan pada sumber data; alarm menunjukkan perubahan pada pola galat yang perlu diperiksa.</div></div>')
    with g2:
        md('<div class="tile"><b>Pola CEP</b><div class="tile-foot" style="margin-top:.35rem">Repetition berarti alarm berlangsung beruntun. Tren naik/turun berarti rata-rata galat bergerak konsisten melewati aturan internal. Absence berarti ada jeda kedatangan observasi yang lebih panjang dari ambang CEP.</div></div>')
    with g3:
        md('<div class="tile"><b>Referensi dan monitoring</b><div class="tile-foot" style="margin-top:.35rem">Bagian awal data dipakai sebagai referensi untuk menghitung baseline dan mengalibrasi batas. Bagian setelahnya menjadi periode monitoring. Karena durasinya berbeda, bandingkan laju kejadian per hari.</div></div>')


def render_download(view: SimpleNamespace) -> None:
    export_summary = summarize(view.detections, view.monitoring["timestamp"].min())
    full_zip = build_export_zip(view.frame, view.analysis, export_summary)
    d1, d2 = st.columns(2, gap="large")
    with d1:
        md('<div class="tile" style="margin-bottom:.5rem"><div class="panel-title">Paket analisis lengkap</div><div class="panel-note" style="margin:0">Satu ZIP berisi data terolah, statistik EWMA, batas kendali, status alarm, dan pola CEP.</div></div>')
        st.download_button("Unduh semua (.zip)", full_zip, "carbon_intensity_analysis.zip", "application/zip", use_container_width=True, type="primary")
        md('<div class="tile" style="margin:1rem 0 .5rem"><div class="panel-title">Dataset</div><div class="panel-note" style="margin:0">Forecast, aktual, galat, dan timestamp.</div></div>')
        st.download_button("Unduh dataset (.csv)", view.frame.to_csv(index=False).encode("utf-8"), "carbon_intensity_data.csv", "text/csv", use_container_width=True)
    with d2:
        md('<div class="tile" style="margin-bottom:.5rem"><div class="panel-title">Hasil EWMA + CEP</div><div class="panel-note" style="margin:0">Dataset digabung dengan statistik EWMA, batas kendali, alarm, streak, repetition, tren, dan absence.</div></div>')
        st.download_button("Unduh hasil gabungan (.csv)", view.analysis.to_csv(index=False).encode("utf-8"), "carbon_intensity_analysis.csv", "text/csv", use_container_width=True)
        md('<div class="tile" style="margin:1rem 0 .5rem"><div class="panel-title">Ringkasan pola</div><div class="panel-note" style="margin:0">Jumlah kejadian dan laju kejadian per hari pada referensi dan monitoring.</div></div>')
        st.download_button("Unduh ringkasan CEP (.csv)", export_summary.to_csv(index=False).encode("utf-8"), "carbon_intensity_cep_summary.csv", "text/csv", use_container_width=True)
    md('<div style="height:1.2rem"></div>')
    md(sec_head("Ikhtisar ekspor", "Semua waktu dalam UTC"))
    s1, s2, s3, s4 = st.columns(4, gap="medium")
    with s1:
        md(tile("Baris sumber", f"{len(view.frame):,}"))
    with s2:
        md(tile("Observasi lengkap", f"{len(view.complete):,}"))
    with s3:
        md(tile("Periode referensi", f"{len(view.reference):,}"))
    with s4:
        md(tile("Periode monitoring", f"{len(view.monitoring):,}"))


# ---------------------------------------------------------------------------
# Alur utama
# ---------------------------------------------------------------------------
page = st.session_state.page
DATA_PAGES = ("Home", "CEP", "EDA", "Download")

view: SimpleNamespace | None = None
failure: Exception | None = None
if page in DATA_PAGES:
    try:
        with st.spinner("Menyiapkan data dan menjalankan analisis..."):
            view = compute_view()
    except Exception as exc:  # ditampilkan sebagai pesan, bukan menghentikan seluruh dashboard
        failure = exc
else:
    try:
        view = compute_view()
    except Exception:
        view = None

inject_css(view.recent_alarms if view is not None else None)
build_sidebar()
build_header(view)

if failure is not None:
    if isinstance(failure, (DataError, ValueError, KeyError, ZeroDivisionError)):
        st.error(f"Analisis belum dapat dijalankan: {failure}")
        md('<div class="panel-note">Periksa koneksi API atau file <code>results/history.csv</code> untuk mode cache. Data referensi harus memiliki sedikitnya 100 observasi lengkap dan harus ada data monitoring setelahnya. Buka halaman Pengaturan untuk mengubah rentang data.</div>')
    else:
        st.error("Terjadi kesalahan ketika core engine dijalankan.")
        with st.expander("Detail teknis"):
            st.exception(failure)
elif page == "Home":
    render_home(view)
elif page == "CEP":
    render_cep(view)
elif page == "EDA":
    render_eda(view)
elif page == "Download":
    render_download(view)
elif page == "Settings":
    render_settings()
else:
    render_about()

md(
    f'<div style="display:flex;justify-content:space-between;gap:1rem;color:var(--muted);font-size:.7rem;margin-top:1.6rem;padding-top:.9rem;border-top:1px solid var(--line)">'
    f'<span>CARBON INTENSITY DASHBOARD</span><span>{"Gelap" if DARK else "Terang"} · Sumber: '
    f'{"Otomatis" if st.session_state.source_mode == "auto" else "API" if st.session_state.source_mode == "live" else "Cache CSV"} · UTC / 30 menit</span></div>'
)