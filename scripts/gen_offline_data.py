"""
scripts/gen_offline_data.py — Bundled offline snapshot for the mobile app
==========================================================================
Regenerates mobile/assets/data/agriguard_offline_data.json and
mobile/assets/data/agriguard_weather_data.json from AgriGuard's live,
auto-synced data — not a fixed CSV snapshot (see fix #4 below).

Why this exists: the mobile app ships without a live backend connection
by default, so it needs a static, pre-computed snapshot of forecasts,
market summaries, and weather baked into the APK as an asset. This
script produces that snapshot using the SAME food-only scope and
forecast-safety clamp that backend/app/routers/forecasts.py applies,
so the bundled data the mobile app shows never drifts from what the
live backend would return for the same commodity/market.

Run after a fresh sync (or just periodically — every run reads whatever
WFP/FEWS NET/weather last auto-synced to), then rebuild the Flutter app:
    python3 scripts/gen_offline_data.py

Fixes applied here vs. the previous generation pass (see AgriGuard
memory notes / git history for the backend-side equivalents):
  1. FOOD-ONLY SCOPE — previously every WFP commodity (including non-food
     items like Basin, Batteries, Hoe, Jerrycan, Nails, Panga, Sanitary
     Pads, Soap, Torch, ...) was forecast and bundled. Now uses the same
     services/food_scope.py filter routers/forecasts.py and
     routers/markets.py both import, instead of a third, independently
     drifting copy of the category allowlist.
  2. RUNAWAY-FORECAST CLAMP — the old linear-extrapolation pass had no
     equivalent of forecasts.py's _clamp_to_historical_range(), so a
     naive slope fit on sparse/monthly data routinely produced forecasts
     that decayed to a floor of 0 well before the end of a 14/28-day
     horizon (a -100% "price crash" that never happened in reality).
     This applies the identical historical-range clamp used server-side.
  3. CURRENT PRICE — each bundled forecast entry now carries an explicit
     current_price field (the latest real observed price) so the app can
     show "current vs predicted" without having to reach into the
     separate history array.
  4. LIVE SOURCES, NOT A STALE SNAPSHOT — this used to read
     data/raw/wfp_food_prices_uga.csv (fine — wfp_sync.py keeps that file
     itself live) but weather came from two hardcoded, date-stamped
     filenames (uganda_weather_{historical,forecast}_2026-06-14.csv) that
     stop existing the moment fetch_weather.py's next run writes a new
     date into the filename — this had been silently failing since. Price
     also never blended in FEWS NET's fresher feed the way the live
     backend does. Now: prices blend WFP + FEWS NET exactly like
     forecasts.py::load_price_data(), and weather reads straight from the
     `weather_readings` table backend/app/services/weather_sync.py keeps
     current — no filename to go stale. Each bundle also records the
     upstream sync timestamp(s) it was built from (`sync_status` /
     `last_synced_at`) so a stale bundle is visible, not silent.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # allow `python scripts/gen_offline_data.py` from repo root

from backend.app.services import food_scope, fews_net_sync, wfp_sync, weather_sync  # noqa: E402

WFP_CSV = ROOT / "data" / "raw" / "wfp_food_prices_uga.csv"
OUT_PRICES = ROOT / "mobile" / "assets" / "data" / "agriguard_offline_data.json"
OUT_WEATHER = ROOT / "mobile" / "assets" / "data" / "agriguard_weather_data.json"

HORIZONS = (7, 14, 28)
ALERT_THRESHOLD_PCT = 5.0
MIN_OBSERVATIONS = 6          # bundled snapshot: a bit more lenient than the
                               # live backend's MIN_OBSERVATIONS=10, since a
                               # sparse commodity/market pair is still better
                               # bundled with a naive-flat forecast than
                               # dropped from the app entirely.
TRAINING_WINDOW_DAYS = 730
HISTORY_POINTS = 24            # trailing observations kept for the sparkline


def _select_preferred_price_type(df: pd.DataFrame) -> pd.DataFrame:
    """Prefer retail; fall back to wholesale only for commodity/market combos
    with no retail rows at all. Same rule forecasts.py applies."""
    if "pricetype" not in df.columns:
        return df
    retail = df[df["pricetype"].str.lower() == "retail"]
    combos_with_retail = set(zip(retail["commodity"], retail["market"]))
    wholesale_only = df[
        (df["pricetype"].str.lower() == "wholesale")
        & ~df[["commodity", "market"]].apply(tuple, axis=1).isin(combos_with_retail)
    ]
    return pd.concat([retail, wholesale_only], ignore_index=True)


def load_wfp_prices() -> pd.DataFrame:
    """WFP's deep-history feed — the same file wfp_sync.py keeps live via
    its HDX metadata-poll + atomic swap, so this always reads whatever
    that background sync last installed."""
    df = pd.read_csv(WFP_CSV, low_memory=False)
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df.dropna(subset=["date", "price"], inplace=True)
    df = df[df["price"] > 0]
    df["commodity"] = df["commodity"].str.strip().str.title()
    df["market"] = df["market"].str.strip().str.title()
    df = _select_preferred_price_type(df)
    return food_scope.filter_food_only(df, "WFP")


def load_fews_net_prices() -> pd.DataFrame:
    """FEWS NET's fresher-cadence feed, blended on top of WFP below — see
    backend/app/services/fews_net_sync.py. Returns an empty frame (not an
    error) if it hasn't synced yet in this environment; WFP alone is a
    perfectly fine bundle on its own."""
    if not fews_net_sync.DATA_PATH.exists():
        return pd.DataFrame()

    df = pd.read_csv(fews_net_sync.DATA_PATH, low_memory=False)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df.rename(columns={"price_type": "pricetype"}, inplace=True)
    df = _select_preferred_price_type(df)
    return food_scope.filter_food_only(df, "FEWS NET")


def load_food_prices() -> pd.DataFrame:
    """
    WFP's deep history blended with FEWS NET's fresher feed — identical
    overlap-resolution rule to backend/app/routers/forecasts.py::load_price_data()
    (FEWS NET wins on a (market, commodity, date) collision, WFP fills
    every gap it doesn't cover). Both sources go through the shared
    services/food_scope.py filter rather than a locally duplicated
    category set, so this bundle can't quietly drift from what the live
    backend serves for the same commodity/market.
    """
    wfp_df = load_wfp_prices()
    fews_df = load_fews_net_prices()

    if fews_df.empty:
        df = wfp_df
    else:
        combined = pd.concat([fews_df, wfp_df], ignore_index=True)
        df = combined.drop_duplicates(subset=["market", "commodity", "date"], keep="first")
        print(
            f"Blended price data: {len(wfp_df)} WFP + {len(fews_df)} FEWS NET "
            f"-> {len(df)} observations after overlap resolution."
        )

    return df.sort_values("date").reset_index(drop=True)


def clamp_to_historical_range(yhat: np.ndarray, hist_prices: np.ndarray) -> np.ndarray:
    """Identical logic to forecasts.py::_clamp_to_historical_range."""
    hist_min, hist_max = float(hist_prices.min()), float(hist_prices.max())
    hist_range = hist_max - hist_min
    buffer = hist_range if hist_range > 0 else max(hist_max * 0.5, 1.0)
    floor = max(0.0, hist_min - buffer)
    ceiling = hist_max + buffer
    return np.clip(yhat, floor, ceiling)


def clamp_to_realistic_drift(yhat: np.ndarray, last_actual: float) -> np.ndarray:
    """Second, tighter layer on top of clamp_to_historical_range().

    The historical-range clamp alone (mirroring the live backend) still let
    a steep slope on sparse/volatile data ride all the way down to its
    floor within a single horizon — a "price crash to near-zero" that
    doesn't happen to real food commodities day-to-day. This caps how far
    a linear fit is allowed to drift from the last real observed price:
    at most ~2%/day, capped at 50% over any horizon — generous enough to
    show a real trend and its alert, tight enough to stop the runaway
    extrapolation that caused the original "abnormally wrong" forecasts.
    """
    days = np.arange(1, len(yhat) + 1)
    max_frac = np.minimum(0.02 * days, 0.5)
    lo = last_actual * (1 - max_frac)
    hi = last_actual * (1 + max_frac)
    return np.clip(yhat, lo, hi)


def forecast_one(prices: np.ndarray, dates: pd.Series, horizon: int) -> dict:
    """Linear-extrapolation forecast with the same historical-range clamp
    the live backend applies as defense-in-depth (see module docstring,
    fix #2), plus an additional realistic-drift cap (see
    clamp_to_realistic_drift) tuned specifically for this bundled,
    curated snapshot."""
    x = np.arange(len(prices))
    slope, intercept = np.polyfit(x, prices, 1)
    std_band = np.std(prices) * 0.10
    last_actual = float(prices[-1])

    today = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None)
    last_date = max(dates.max(), today)

    future_x = np.arange(len(prices) + 1, len(prices) + horizon + 1)
    yhat = intercept + slope * future_x
    yhat = clamp_to_historical_range(yhat, prices)
    yhat = clamp_to_realistic_drift(yhat, last_actual)
    lower = clamp_to_historical_range(yhat - std_band, prices)
    lower = np.minimum(lower, yhat)
    upper = yhat + std_band

    points = []
    for i in range(horizon):
        interval_width = upper[i] - lower[i]
        midpoint = abs(yhat[i]) or 1e-9
        confidence = round(max(0.0, min(1.0, 1.0 - interval_width / (2 * midpoint))), 3)
        points.append({
            "date": (last_date + timedelta(days=i + 1)).strftime("%Y-%m-%d"),
            "predicted_price": round(float(yhat[i]), 2),
            "lower_bound": round(float(lower[i]), 2),
            "upper_bound": round(float(upper[i]), 2),
            "confidence": confidence,
        })

    tail = yhat[-5:] if len(yhat) >= 5 else yhat
    if len(tail) >= 2:
        tail_slope = np.polyfit(range(len(tail)), tail, 1)[0]
        pct_slope = tail_slope / (np.mean(tail) or 1e-9)
        trend = "rising" if pct_slope > 0.005 else "falling" if pct_slope < -0.005 else "stable"
    else:
        trend = "stable"

    last_predicted = points[-1]["predicted_price"]
    pct_change = round(((last_predicted - last_actual) / last_actual) * 100, 2) if last_actual > 0 else 0.0

    alert = None
    if abs(pct_change) >= ALERT_THRESHOLD_PCT:
        direction = "rise" if trend == "rising" else "fall"
        emoji = "\U0001F4C8" if trend == "rising" else "\U0001F4C9"
        tip = "Consider selling soon." if trend == "rising" else "Good time to buy inputs."
        alert = f"{emoji} Prices are expected to {direction} by ~{abs(pct_change):.1f}% over the forecast period. {tip}"

    return {
        "horizon_days": horizon,
        "observations_used": len(prices),
        "current_price": round(last_actual, 2),
        "forecast": points,
        "trend": trend,
        "pct_change": pct_change,
        "alert": alert,
        "model_used": "linear",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def build_price_bundle(df: pd.DataFrame) -> dict:
    cutoff_days = TRAINING_WINDOW_DAYS
    forecasts: dict[str, dict] = {}
    all_data_as_of = df["date"].max()

    for (commodity, market), group in df.groupby(["commodity", "market"]):
        group = group.sort_values("date")
        cutoff = group["date"].max() - timedelta(days=cutoff_days)
        train = group[group["date"] >= cutoff]
        if len(train) < MIN_OBSERVATIONS:
            continue

        prices = train["price"].values
        dates = train["date"]
        currency = str(train["currency"].iloc[-1]) if "currency" in train else "UGX"
        unit = str(train["unit"].iloc[-1]) if "unit" in train else "KG"

        horizons = {str(h): forecast_one(prices, dates, h) for h in HORIZONS}

        history = [
            {"date": d.strftime("%Y-%m-%d"), "price": round(float(p), 2)}
            for d, p in zip(dates.tail(HISTORY_POINTS), prices[-HISTORY_POINTS:])
        ]

        key = f"{commodity}|{market}"
        forecasts[key] = {
            "commodity": commodity,
            "market": market,
            "currency": currency,
            "unit": unit,
            "current_price": round(float(prices[-1]), 2),
            "horizons": horizons,
            "history": history,
        }

    commodities = sorted(df["commodity"].unique().tolist())
    markets = sorted(df["market"].unique().tolist())

    # --- market summaries, top movers, arbitrage (unchanged shape from
    # the previous generator, now built only from food-scoped `forecasts`) ---
    market_summaries: dict[str, dict] = {}
    top_movers_rows = []
    arbitrage: dict[str, list] = {}

    for commodity in commodities:
        entries = {k: v for k, v in forecasts.items() if v["commodity"] == commodity}
        if not entries:
            continue
        by_price = sorted(entries.values(), key=lambda e: e["current_price"])
        cheapest, priciest = by_price[0], by_price[-1]
        market_summaries[commodity] = {
            "commodity": commodity,
            "markets_covered": len(entries),
            "avg_price": round(sum(e["current_price"] for e in entries.values()) / len(entries), 2),
            "cheapest_market": cheapest["market"],
            "cheapest_price": cheapest["current_price"],
            "priciest_market": priciest["market"],
            "priciest_price": priciest["current_price"],
            "currency": cheapest["currency"],
            "unit": cheapest["unit"],
        }

        for e in entries.values():
            top_movers_rows.append({
                "commodity": commodity,
                "market": e["market"],
                "pct_change": e["horizons"]["28"]["pct_change"],
                "current_price": e["current_price"],
                "currency": e["currency"],
                "unit": e["unit"],
            })

        if len(entries) >= 2 and cheapest["market"] != priciest["market"] and cheapest["current_price"] > 0:
            margin_pct = round((priciest["current_price"] - cheapest["current_price"]) / cheapest["current_price"] * 100, 1)
            if margin_pct >= 10:
                arbitrage[commodity] = [{
                    "commodity": commodity,
                    "buy_market": cheapest["market"],
                    "buy_price": cheapest["current_price"],
                    "sell_market": priciest["market"],
                    "sell_price": priciest["current_price"],
                    "gross_margin_pct": margin_pct,
                    "currency": cheapest["currency"],
                    "unit": cheapest["unit"],
                }]

    top_movers_rows.sort(key=lambda r: r["pct_change"], reverse=True)
    gainers = [r for r in top_movers_rows if r["pct_change"] > 0][:10]
    losers = sorted([r for r in top_movers_rows if r["pct_change"] < 0], key=lambda r: r["pct_change"])[:10]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data_as_of": all_data_as_of.strftime("%Y-%m-%d"),
        "sync_status": {
            "wfp_last_synced_at": wfp_sync.last_sync_info().get("synced_at"),
            "fews_net_last_synced_at": fews_net_sync.last_sync_info().get("synced_at"),
        },
        "commodities": commodities,
        "markets": markets,
        "forecasts": forecasts,
        "market_summaries": market_summaries,
        "top_movers": {
            "gainers": gainers,
            "losers": losers,
            "period_days": 28,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        "arbitrage": arbitrage,
    }


# =============================================================================
# Weather bundle — current conditions, a rolling forecast, and plain-language
# farmer advisory per market. Bundled for the same reason prices are: the
# mobile app can't assume a live connection to the Open-Meteo-backed backend.
# =============================================================================

def _advisory(rainfall_7d: float, water_balance: float, temp_max: float) -> tuple[str, str]:
    """Returns (risk_level, advice) — simple, transparent thresholds rather
    than a model, so the reasoning is obvious and easy for Keith to tune."""
    if water_balance <= -15 or rainfall_7d < 5:
        return (
            "Dry spell",
            "Soil moisture is low. Prioritise water for young or flowering crops, "
            "mulch to slow evaporation, and hold off on planting thirsty crops until "
            "rain returns.",
        )
    if rainfall_7d >= 60:
        return (
            "Heavy rain",
            "Recent rainfall has been heavy. Check drainage in low-lying plots, "
            "delay harvesting grain until it can dry properly, and watch stored "
            "produce for damp and mould.",
        )
    if temp_max >= 34:
        return (
            "Hot conditions",
            "High daytime temperatures can stress crops and speed up water loss. "
            "Water early morning or evening, and provide shade for seedlings if you can.",
        )
    return (
        "Normal",
        "Conditions are within a normal range. Good time for routine weeding, "
        "planting, and light fertiliser application.",
    )


def build_weather_bundle() -> dict:
    """
    Reads weather straight from the `weather_readings` table
    backend/app/services/weather_sync.py keeps live — not the two
    hardcoded, date-stamped CSV filenames this used to point at
    (uganda_weather_{historical,forecast}_2026-06-14.csv), which stop
    existing the moment fetch_weather.py's next manual run writes a new
    date into the filename. The DB is the actual source of truth for
    weather now that it auto-syncs (see module docstring, fix #4).
    """
    from backend.app import models as _models  # noqa: F401 — registers WeatherReading before any query
    from backend.app.database import SessionLocal, create_tables
    from backend.app.models.price import Market
    from backend.app.models.weather import WeatherReading

    # Defensive, not redundant: main.py's startup calls this too, but this
    # script is meant to be runnable standalone (a fresh CI runner, or
    # Keith's machine before ./run.sh has ever started the backend once) —
    # without this, a genuinely fresh SQLite file has no `markets` table at
    # all yet, and the query below raised a raw
    # "sqlalchemy.exc.OperationalError: no such table: markets" instead of
    # the friendly "run a weather sync first" message a few lines down.
    # create_tables() is CREATE TABLE IF NOT EXISTS — safe to call here even
    # when the backend already created everything.
    create_tables()

    db = SessionLocal()
    try:
        markets_db = db.query(Market).filter(Market.is_active.is_(True)).all()
        if not markets_db:
            print(
                "No markets in the database yet — run a weather sync first "
                "(POST /weather/sync, or start the backend once so its startup "
                "scheduler runs it). Skipping weather bundle."
            )
            return {}

        bundle_markets: dict[str, dict] = {}
        today = pd.Timestamp.now().normalize()

        for m in markets_db:
            hist_rows = (
                db.query(WeatherReading)
                .filter(WeatherReading.market_id == m.id, WeatherReading.is_forecast.is_(False))
                .order_by(WeatherReading.reading_date)
                .all()
            )
            if not hist_rows:
                continue  # no historical weather synced for this market yet

            latest = hist_rows[-1]
            last7 = hist_rows[-7:]
            rainfall_7d = sum(r.rainfall_mm or 0.0 for r in last7)
            balances = [r.water_balance_mm for r in last7 if r.water_balance_mm is not None]
            water_balance = sum(balances) / len(balances) if balances else 0.0
            risk_level, advice = _advisory(rainfall_7d, water_balance, latest.temp_max_c or 0.0)

            fcst_rows = (
                db.query(WeatherReading)
                .filter(WeatherReading.market_id == m.id, WeatherReading.is_forecast.is_(True))
                .order_by(WeatherReading.reading_date)
                .all()
            )
            forecast_days = [
                {
                    "date": (today + timedelta(days=i + 1)).strftime("%Y-%m-%d"),
                    "temp_max_c": round(r.temp_max_c, 1) if r.temp_max_c is not None else None,
                    "temp_min_c": round(r.temp_min_c, 1) if r.temp_min_c is not None else None,
                    "rainfall_mm": round(r.rainfall_mm, 1) if r.rainfall_mm is not None else None,
                    "humidity_max_pct": round(r.humidity_max_pct, 0) if r.humidity_max_pct is not None else None,
                }
                for i, r in enumerate(fcst_rows)
            ]

            bundle_markets[m.name] = {
                "market": m.name,
                "region": m.region.value if hasattr(m.region, "value") else str(m.region),
                "current": {
                    "as_of": latest.reading_date.strftime("%Y-%m-%d"),
                    "temp_max_c": round(latest.temp_max_c, 1) if latest.temp_max_c is not None else None,
                    "temp_min_c": round(latest.temp_min_c, 1) if latest.temp_min_c is not None else None,
                    "rainfall_mm": round(latest.rainfall_mm, 1) if latest.rainfall_mm is not None else None,
                    "humidity_max_pct": round(latest.humidity_max_pct, 0) if latest.humidity_max_pct is not None else None,
                    "wind_speed_max_kmh": round(latest.wind_speed_max_kmh, 1) if latest.wind_speed_max_kmh is not None else None,
                },
                "forecast": forecast_days,
                "risk_level": risk_level,
                "advice": advice,
                "rainfall_last_7d_mm": round(rainfall_7d, 1),
            }
    finally:
        db.close()

    if not bundle_markets:
        print(
            "Markets exist but none have weather readings yet — run a weather "
            "sync first (POST /weather/sync). Skipping weather bundle."
        )
        return {}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "Open-Meteo (bundled snapshot; live data used automatically when a backend is reachable)",
        "last_synced_at": weather_sync.last_sync_info().get("synced_at"),
        "markets": bundle_markets,
    }


def main() -> None:
    df = load_food_prices()
    price_bundle = build_price_bundle(df)
    OUT_PRICES.parent.mkdir(parents=True, exist_ok=True)
    OUT_PRICES.write_text(json.dumps(price_bundle, indent=None, separators=(",", ":")))
    print(f"Wrote {OUT_PRICES} ({OUT_PRICES.stat().st_size / 1024:.0f} KB), "
          f"{len(price_bundle['forecasts'])} commodity/market pairs, "
          f"{len(price_bundle['commodities'])} food commodities.")

    weather_bundle = build_weather_bundle()
    if weather_bundle:
        OUT_WEATHER.write_text(json.dumps(weather_bundle, indent=None, separators=(",", ":")))
        print(f"Wrote {OUT_WEATHER} ({OUT_WEATHER.stat().st_size / 1024:.0f} KB), "
              f"{len(weather_bundle['markets'])} markets.")


if __name__ == "__main__":
    main()
