"""
frontend/pages/ussd_simulator.py — AgriGuard USSD Simulator
=============================================================
Simulates the SMS/USSD experience that Ugandan farmers use
to query crop prices without internet access.

USSD flow:
  *183*7# → Welcome → Select crop → Select region → Get price + advice

This page demonstrates:
  - How the system works on feature phones (Nokia, Tecno)
  - The UGX price + recommendation a farmer receives
  - Why USSD matters (only 23% of Uganda has internet — NITA-U 2024)

For Ministry of ICT Showcase: run this alongside the dashboard.
"""

import os
import sys
import streamlit as st
import requests
from datetime import datetime

# frontend/pages/ -> frontend/, so `from style import inject_style` resolves
# regardless of the working directory Streamlit was launched from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from style import inject_style

st.set_page_config(
    page_title="AgriGuard — USSD Simulator",
    page_icon="📱",
    layout="centered",
)
inject_style()

BASE_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# ─────────────────────────────────────────────
# STYLE — mimics a Nokia feature-phone screen
# ─────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap');

.phone-shell {
    background: #1a1a1a;
    border-radius: 28px;
    padding: 12px;
    max-width: 320px;
    margin: 0 auto;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05);
}
.phone-screen {
    background: #c8d8a0;
    border-radius: 6px;
    padding: 14px;
    font-family: 'Space Mono', monospace;
    font-size: 0.78rem;
    color: #1a1a1a;
    min-height: 200px;
    white-space: pre-wrap;
    line-height: 1.5;
    border: 2px solid #7a8a4a;
}
.phone-header {
    font-family: 'Space Mono', monospace;
    font-size: 0.65rem;
    color: #4a5a2a;
    text-align: right;
    margin-bottom: 6px;
}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
CROPS = [
    "Maize", "Beans", "Rice", "Cassava",
    "Coffee (Robusta)", "Bananas (Matoke)", "Sorghum", "Groundnuts",
]

REGIONS = [
    "Kampala", "Mbarara", "Gulu", "Mbale",
    "Jinja", "Fort Portal", "Arua", "Lira",
]

USSD_CODE = "*183*7#"

# ─────────────────────────────────────────────
# SESSION STATE — tracks USSD dialog step
# ─────────────────────────────────────────────
if "ussd_step"   not in st.session_state: st.session_state.ussd_step   = 0
if "ussd_crop"   not in st.session_state: st.session_state.ussd_crop   = None
if "ussd_region" not in st.session_state: st.session_state.ussd_region = None
if "ussd_result" not in st.session_state: st.session_state.ussd_result = None
if "ussd_log"    not in st.session_state: st.session_state.ussd_log    = []

def reset():
    st.session_state.ussd_step   = 0
    st.session_state.ussd_crop   = None
    st.session_state.ussd_region = None
    st.session_state.ussd_result = None
    st.session_state.ussd_log    = []

def append_log(screen_text: str):
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.ussd_log.append(f"[{ts}]\n{screen_text}")

def build_screen(text: str) -> str:
    """Wrap text in phone-screen HTML."""
    now = datetime.now().strftime("%H:%M")
    header = f'<div class="phone-header">MTN UG &nbsp;|&nbsp; {now}</div>'
    return (
        f'<div class="phone-shell">'
        f'{header}'
        f'<div class="phone-screen">{text}</div>'
        f'</div>'
    )

def fetch_prediction(crop: str, region: str) -> dict:
    try:
        r = requests.post(
            f"{BASE_URL}/api/v1/predict",
            json={"crop": crop.lower(), "region": region, "date": datetime.today().strftime("%Y-%m-%d")},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    # Demo fallback when backend is offline
    fallback_prices = {
        "Maize": 900, "Beans": 3200, "Rice": 4500, "Cassava": 600,
        "Coffee (Robusta)": 12000, "Bananas (Matoke)": 1500,
        "Sorghum": 800, "Groundnuts": 5500,
    }
    price = fallback_prices.get(crop, 1200)
    return {
        "predicted_price": price,
        "trend":           "stable",
        "recommendation":  "HOLD",
        "confidence":      0.72,
        "currency":        "UGX",
        "_demo_mode":      True,
    }

# ─────────────────────────────────────────────
# PAGE HEADER
# ─────────────────────────────────────────────
st.title("📱 USSD Simulator")
st.caption(
    "Simulates how Ugandan farmers query AgriGuard on feature phones. "
    f"Dial **{USSD_CODE}** to start."
)
st.divider()

col_phone, col_info = st.columns([1, 1], gap="large")

with col_phone:
    step = st.session_state.ussd_step

    # ── STEP 0: Idle / dial screen ──
    if step == 0:
        screen_text = (
            "AgriGuard\n"
            "Agricultural Prices\n"
            "─────────────────\n"
            f"Dial {USSD_CODE}\n"
            "to check crop prices\n"
            "in your area.\n"
            "\n"
            "Free on MTN & Airtel"
        )
        st.markdown(build_screen(screen_text), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button(f"📲 Dial {USSD_CODE}", type="primary", use_container_width=True):
            st.session_state.ussd_step = 1
            st.rerun()

    # ── STEP 1: Select crop ──
    elif step == 1:
        screen_text = (
            "AgriGuard\n"
            "─────────────────\n"
            "Select crop:\n"
            + "\n".join(f"{i+1}. {c}" for i, c in enumerate(CROPS))
            + "\n\n0. Cancel"
        )
        st.markdown(build_screen(screen_text), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        append_log(screen_text)

        choice = st.selectbox("Enter option", ["— select —"] + [f"{i+1}" for i in range(len(CROPS))])
        if st.button("Send", use_container_width=True) and choice != "— select —":
            idx = int(choice) - 1
            st.session_state.ussd_crop = CROPS[idx]
            st.session_state.ussd_step = 2
            st.rerun()
        if st.button("Cancel", use_container_width=True):
            reset(); st.rerun()

    # ── STEP 2: Select region ──
    elif step == 2:
        screen_text = (
            f"Crop: {st.session_state.ussd_crop}\n"
            "─────────────────\n"
            "Select your market:\n"
            + "\n".join(f"{i+1}. {r}" for i, r in enumerate(REGIONS))
            + "\n\n0. Back"
        )
        st.markdown(build_screen(screen_text), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        append_log(screen_text)

        choice = st.selectbox("Enter option", ["— select —"] + [f"{i+1}" for i in range(len(REGIONS))])
        if st.button("Send", use_container_width=True) and choice != "— select —":
            idx = int(choice) - 1
            st.session_state.ussd_region = REGIONS[idx]
            with st.spinner("Fetching price…"):
                st.session_state.ussd_result = fetch_prediction(
                    st.session_state.ussd_crop,
                    st.session_state.ussd_region,
                )
            st.session_state.ussd_step = 3
            st.rerun()
        if st.button("Back", use_container_width=True):
            st.session_state.ussd_step = 1; st.rerun()

    # ── STEP 3: Result ──
    elif step == 3:
        res    = st.session_state.ussd_result
        crop   = st.session_state.ussd_crop
        region = st.session_state.ussd_region

        price  = res.get("predicted_price", 0)
        trend  = res.get("trend", "stable").upper()
        action = res.get("recommendation", "HOLD")
        conf   = int(res.get("confidence", 0) * 100)
        demo   = res.get("_demo_mode", False)

        trend_arrow = {"UP": "↑", "DOWN": "↓", "STABLE": "→"}.get(trend, "→")
        advice_map  = {
            "SELL": "Good time to sell!",
            "HOLD": "Hold — prices rising",
            "STORE": "Store — prices falling",
        }

        screen_text = (
            f"AgriGuard Result\n"
            f"─────────────────\n"
            f"Crop  : {crop}\n"
            f"Market: {region}\n"
            f"─────────────────\n"
            f"Price : UGX {price:,.0f}/kg\n"
            f"Trend : {trend_arrow} {trend}\n"
            f"Advice: {action}\n"
            f"─────────────────\n"
            f"{advice_map.get(action, '')}\n"
            f"Confidence: {conf}%\n"
            + ("\n[DEMO MODE]" if demo else "")
        )
        st.markdown(build_screen(screen_text), unsafe_allow_html=True)
        append_log(screen_text)
        st.markdown("<br>", unsafe_allow_html=True)

        if st.button("🔄 New Query", type="primary", use_container_width=True):
            reset(); st.rerun()
        if st.button("🌾 Check another crop", use_container_width=True):
            st.session_state.ussd_step = 1
            st.session_state.ussd_crop = None
            st.rerun()

with col_info:
    st.markdown("### Why USSD matters")
    st.markdown("""
Only **23% of Ugandans** have internet access (NITA-U, 2024).
USSD works on any mobile phone — no data, no app, no smartphone needed.

**How a farmer uses AgriGuard:**
1. Dials `*183*7#` on any handset
2. Selects crop and nearest market
3. Gets price + advice in **<3 seconds**
4. Decides: sell today, store, or travel to a better market

**What they receive:**
- Current predicted price in UGX/kg
- Trend (rising / falling / stable)
- One-word action: **SELL / HOLD / STORE**

This works on a **UGX 15,000 Nokia** with no data plan.
    """)

    st.divider()
    st.markdown("### Session Log")
    if st.session_state.ussd_log:
        for entry in reversed(st.session_state.ussd_log[-5:]):
            with st.expander(entry.split("\n")[0]):
                st.code(entry, language=None)
    else:
        st.caption("Dial the USSD code to start a session.")

# ─────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────
st.divider()
st.caption(
    "AgriGuard USSD · Powered by MTN Uganda & Airtel Uganda · "
    "Built by Keith Ndiema Kissa · Mbarara University of Science and Technology, 2026"
)