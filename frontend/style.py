"""
frontend/style.py — Shared AgriGuard branding
================================================
Every page previously carried its own copy-pasted <style> block (or none
at all — price_forecast.py and ussd_simulator.py had no branding, so they
rendered as bare default Streamlit: red primary buttons, the Streamlit
hamburger menu, "Deploy" button, and "Made with Streamlit" footer all
visible). That inconsistency — some pages branded, some not — is what
makes the app read as a stitched-together prototype rather than a real
product.

Call inject_style() once, right after st.set_page_config(), on every page.
Page-specific extras (e.g. the USSD phone-shell mockup) still get their
own local <style> block on top of this.
"""

import streamlit as st

BRAND_GREEN = "#00cc66"


def inject_style() -> None:
    st.markdown(
        """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&display=swap');

    html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; }

    /* Hide Streamlit's default chrome (hamburger menu, "Deploy" button,
       "Made with Streamlit" footer) — it's the single biggest tell that
       a page is an unstyled Streamlit default rather than a real product. */
    #MainMenu, footer, div[data-testid="stDecoration"] { visibility: hidden; height: 0; }

    /* Buttons — brand green instead of Streamlit's default red */
    .stButton > button, .stDownloadButton > button {
        background: #00cc66 !important;
        color: #06170c !important;
        border: none !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
    }
    .stButton > button:hover, .stDownloadButton > button:hover {
        background: #00e673 !important;
        color: #06170c !important;
    }

    /* Sliders / selects pick up the brand accent instead of Streamlit red */
    div[data-baseweb="slider"] div[role="slider"] { background-color: #00cc66 !important; }
    div[data-baseweb="select"] > div { border-color: #00cc6655 !important; }

    /* Shared metric card used on the dashboard and forecast pages */
    .metric-card {
        background: linear-gradient(135deg, #0d1f0d 0%, #1a2e1a 100%);
        border: 1px solid #00cc6633;
        border-radius: 12px;
        padding: 18px 20px;
        margin-bottom: 8px;
    }
    .metric-val   { font-size: 1.9rem; font-weight: 700; color: #00cc66; }
    .metric-lbl   { font-size: 0.82rem; color: #aaa; text-transform: uppercase; letter-spacing: .08em; }
    .trend-up     { color: #00cc66; }
    .trend-down   { color: #ff4c4c; }
    .trend-stable { color: #f0a500; }

    /* Alert banners */
    .alert-high { background:#3d0000; border-left:4px solid #ff4c4c; padding:8px 12px; border-radius:4px; margin-bottom:6px; }
    .alert-med  { background:#2d1a00; border-left:4px solid #f0a500; padding:8px 12px; border-radius:4px; margin-bottom:6px; }
    .alert-low  { background:#002d1a; border-left:4px solid #00cc66; padding:8px 12px; border-radius:4px; margin-bottom:6px; }

    /* Backend status dot */
    .status-dot-ok  { display:inline-block; width:10px; height:10px; border-radius:50%; background:#00cc66; margin-right:6px; }
    .status-dot-err { display:inline-block; width:10px; height:10px; border-radius:50%; background:#ff4c4c; margin-right:6px; }
    </style>
    """,
        unsafe_allow_html=True,
    )
