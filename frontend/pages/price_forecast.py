"""
AgriGuard — Price Forecast page
Calls GET /forecasts/{commodity}?market=...&horizon=... (horizon in days, 1-90)
"""

import os
import sys
import requests
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# frontend/pages/ -> frontend/, so `from style import inject_style` resolves
# regardless of the working directory Streamlit was launched from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from style import inject_style
from errors import humanize_response_error, humanize_exception

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(
    page_title="Price Forecast | AgriGuard",
    page_icon="📈",
    layout="wide",
)
inject_style()
st.title("📈 Crop Price Forecast")
st.caption("ML-powered price predictions up to 90 days ahead")
st_autorefresh(interval=15 * 60 * 1000, key="price_forecast_live_refresh")


# ── helpers ────────────────────────────────────────────────────────────────

def api_get(path: str, params: dict = None, timeout: int = 60):
    try:
        r = requests.get(f"{BACKEND_URL}{path}", params=params, timeout=timeout)
        if r.status_code in (200, 201):
            return r.json()
        # Show the backend's own friendly `detail` message (see
        # frontend/errors.py) instead of requests' technical HTTP summary.
        st.error(humanize_response_error(r))
        return None
    except requests.exceptions.Timeout:
        st.error(
            "The forecast is taking longer than expected to train (first request "
            "for a commodity/market combo fits Prophet + XGBoost from scratch, "
            "which can take a while). Try again — it's cached after the first run."
        )
        return None
    except Exception as e:
        st.error(humanize_exception(e))
        return None


@st.cache_data(ttl=300)
def get_commodities_and_markets():
    data = api_get("/forecasts/commodities")
    if data:
        return data["commodities"], data["markets"]
    return (
        ["Maize", "Beans", "Rice", "Cassava", "Sorghum", "Sweet Potato", "Groundnuts", "Millet"],
        ["Kampala", "Gulu", "Mbarara", "Jinja", "Mbale", "Lira", "Arua", "Fort Portal"],
    )


@st.cache_data(ttl=120)
def get_forecast(commodity: str, market: str, horizon: int):
    return api_get(
        f"/forecasts/{commodity}",
        params={"market": market, "horizon": horizon},
    )


@st.cache_data(ttl=120)
def get_history(commodity: str, market: str, days: int):
    return api_get(
        f"/forecasts/history/{commodity}",
        params={"market": market, "days": days},
    )


def get_data_status():
    return api_get("/forecasts/data-status", timeout=15)


# ── sidebar controls ───────────────────────────────────────────────────────
with st.sidebar:
    st.header("Forecast Settings")
    commodities, markets = get_commodities_and_markets()

    commodity = st.selectbox("Commodity", commodities)
    market    = st.selectbox("Market",    markets)
    horizon   = st.slider("Horizon (days)", 1, 90, 14)
    run       = st.button("Run Forecast", type="primary", use_container_width=True)
    refresh   = st.button("Refresh live feeds now", use_container_width=True)

    st.divider()
    st.caption("Sources refresh automatically; prices show the exact latest observation date.")

if refresh:
    with st.spinner("Refreshing WFP, FEWS NET, and weather feeds…"):
        refreshed = api_get("/forecasts/sync/all", timeout=120)
    if refreshed:
        st.success("Live feed refresh completed.")
        st.cache_data.clear()
        st.rerun()


# ── main area ──────────────────────────────────────────────────────────────
if run or True:   # auto-run on page load
    with st.spinner(f"Forecasting {commodity} in {market}… (first run per combo can take a bit)"):
        forecast_data = get_forecast(commodity, market, horizon)
        history_data  = get_history(commodity, market, days=365)
        data_status   = get_data_status()

    if forecast_data is None:
        st.warning(
            "⚠️ Backend not reachable. "
            f"Make sure the API is running at `{BACKEND_URL}`."
        )
        st.stop()

    forecast = forecast_data.get("forecast", [])
    if not forecast:
        st.error("No forecast data returned. Check that models are trained.")
        st.stop()

    df_fc = pd.DataFrame(forecast)
    df_fc["date"] = pd.to_datetime(df_fc["date"])

    # ── metrics row ────────────────────────────────────────────────────────
    first_pred  = df_fc["predicted_price"].iloc[0]
    last_pred   = df_fc["predicted_price"].iloc[-1]
    pct_change  = forecast_data.get("pct_change", 0.0)
    trend       = forecast_data.get("trend", "stable")
    unit        = forecast_data.get("unit", "kg")
    currency    = forecast_data.get("currency", "UGX")
    model_used  = forecast_data.get("model_used", "N/A")
    alert       = forecast_data.get("alert")
    freshness   = forecast_data.get("freshness_status", "unknown")
    latest_date = forecast_data.get("latest_observation_date", "unknown")
    source      = forecast_data.get("price_source", "unknown")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Tomorrow",             f"{currency} {first_pred:,.0f}/{unit}")
    m2.metric(f"Day {horizon}",       f"{currency} {last_pred:,.0f}/{unit}", delta=f"{pct_change:+.1f}%")
    m3.metric("Trend",                trend.capitalize())
    m4.metric("Model",                model_used)

    if alert:
        st.warning(f"⚠️ {alert}")
    if freshness == "stale":
        st.error(
            f"⚠️ Latest observed {commodity} price for {market} is from "
            f"{latest_date} ({source}), not today. The upstream feed has not "
            "published a newer market observation yet; the forecast is not a live quote."
        )
    else:
        st.caption(f"Price data as of {latest_date} · source: {source} · freshness: {freshness}")
    if data_status and data_status.get("weather_forecast_through"):
        st.caption(f"Weather feed refreshed through {data_status['weather_forecast_through']}.")

    st.divider()

    # ── combined history + forecast chart ─────────────────────────────────
    fig = go.Figure()

    if history_data and history_data.get("history"):
        df_hist = pd.DataFrame(history_data["history"])
        df_hist["date"] = pd.to_datetime(df_hist["date"])
        fig.add_trace(go.Scatter(
            x=df_hist["date"], y=df_hist["price"],
            name="Historical Price",
            mode="lines",
            line=dict(color="#3498db", width=2),
        ))

    # confidence band
    fig.add_trace(go.Scatter(
        x=pd.concat([df_fc["date"], df_fc["date"][::-1]]),
        y=pd.concat([df_fc["upper_bound"], df_fc["lower_bound"][::-1]]),
        fill="toself",
        fillcolor="rgba(46,204,113,0.15)",
        line=dict(color="rgba(0,0,0,0)"),
        name="90% Confidence Band",
        showlegend=True,
    ))

    # forecast line
    fig.add_trace(go.Scatter(
        x=df_fc["date"], y=df_fc["predicted_price"],
        name="Forecast",
        mode="lines+markers",
        line=dict(color="#2ecc71", width=2.5, dash="dot"),
        marker=dict(size=7, symbol="circle"),
    ))

    fig.update_layout(
        title=f"{commodity} — {market}",
        xaxis_title="Date",
        yaxis_title=f"Price ({currency}/{unit})",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left"),
        height=430,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── forecast table ─────────────────────────────────────────────────────
    with st.expander("📋 Forecast Data Table"):
        display_df = df_fc[["date", "predicted_price", "lower_bound", "upper_bound", "confidence"]].copy()
        display_df.columns = ["Date", f"Forecast ({currency})", "Lower Bound", "Upper Bound", "Confidence"]
        display_df["Date"] = display_df["Date"].dt.strftime("%d %b %Y")
        st.dataframe(display_df, use_container_width=True, hide_index=True)

    # ── interpretation ─────────────────────────────────────────────────────
    st.info(
        f"**Outlook:** {commodity} prices in {market} are forecast to **{trend}** "
        f"by **{abs(pct_change):.1f}%** over the next {horizon} days. "
        f"Model used: `{model_used}`, trained on {forecast_data.get('observations_used', 'N/A')} observations. "
        f"Price data is as of {latest_date} from {source}."
    )