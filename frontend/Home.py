"""
AgriGuard Landing Page — Backend-aware
"""

import os
import sys
import streamlit as st
import requests

# Home.py lives in frontend/, so this makes `from style import inject_style`
# resolve regardless of the working directory Streamlit was launched from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from style import inject_style

st.set_page_config(page_title="AgriGuard", page_icon="🌾", layout="wide")
inject_style()

BASE_URL = "http://localhost:8000"

def backend_alive(base: str) -> bool:
    try:
        r = requests.get(f"{base}/health", timeout=4)
        return r.status_code == 200
    except Exception:
        return False

online = backend_alive(BASE_URL)

st.markdown("""
<style>
.hero-title { text-align:center; font-size:3rem; font-weight:700; color:#00cc66; }
.hero-sub   { text-align:center; color:#aaa; font-size:1.1rem; margin-top:-10px; }
.feat-card  { background:#0d1f0d; border:1px solid #00cc6633; border-radius:12px;
              padding:20px; margin-bottom:10px; }
.feat-card h4 { color:#00cc66; margin-top:0; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="hero-title">🌾 AgriGuard</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-sub">Agricultural Intelligence System for Uganda</div>', unsafe_allow_html=True)
st.markdown("<br>", unsafe_allow_html=True)

if online:
    st.success("✅ Backend is online — all features are live.")
else:
    st.warning("⚠️ Backend appears offline. Start it with: `uvicorn backend.app.main:app --reload`")

st.divider()

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("""<div class="feat-card">
    <h4>🌽 Price Forecasting</h4>
    <p>Prophet + XGBoost ML pipeline predicting crop prices across Ugandan markets using WFP data.</p>
    </div>""", unsafe_allow_html=True)
with c2:
    st.markdown("""<div class="feat-card">
    <h4>🌦️ Weather Insights</h4>
    <p>Historical and 14-day forecast weather data across Ugandan markets, conveyed alongside price signals.</p>
    </div>""", unsafe_allow_html=True)
with c3:
    st.markdown("""<div class="feat-card">
    <h4>📊 Market Intelligence</h4>
    <p>Cross-market comparisons, arbitrage signals, price movers and national summaries.</p>
    </div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🚀 Open Full Dashboard", type="primary", use_container_width=True):
    st.switch_page("pages/dashboard.py")

st.divider()
st.caption("AgriGuard • Built by Keith Ndiema Kissa (2025/BCS/101/PS), Mbarara University of Science and Technology, 2026")
