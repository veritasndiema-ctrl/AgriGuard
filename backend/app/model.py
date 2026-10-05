"""
Model loading and inference helpers.
Models are loaded once at import time; individual functions raise
ModelNotReadyError (HTTP 503) instead of crashing if pkl files are absent.
"""

import os
import json
import logging
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np
import pandas as pd

from backend.app.services.food_scope import filter_food_only

log = logging.getLogger(__name__)

ROOT      = Path(__file__).resolve().parents[2]
MODEL_DIR = Path(os.getenv("MODEL_DIR", str(ROOT / "ml" / "models")))
DATA_FILE = Path(
    os.getenv("AGRIGUARD_PRICE_DATA",
              str(ROOT / "data" / "raw" / "wfp_food_prices_uga.csv"))
)


class ModelNotReadyError(RuntimeError):
    """Raised when a required model or data file is not yet available."""


# ── load models at startup ────────────────────────────────────────────────────

def _load_pkl(name: str) -> Optional[Any]:
    p = MODEL_DIR / name
    if not p.exists():
        log.warning(f"Model file missing: {p}  — run scripts/train_models.py")
        return None
    try:
        # scripts/train_models.py saves artifacts with joblib.dump(...,
        # compress=3) — that's a zlib-compressed pickle stream, which raw
        # pickle.load() can't parse ("invalid load key" errors). joblib.load
        # handles both compressed and uncompressed pickles transparently,
        # so it's the correct counterpart regardless of the compress level
        # a given training run used.
        return joblib.load(p)
    except Exception as e:
        log.error(f"Failed to load {p}: {e}")
        return None


_price_model   = _load_pkl("price_forecast_model.pkl")
_encoders      = _load_pkl("encoders.pkl")
_metrics: dict = {}

_metrics_path = MODEL_DIR / "metrics.json"
if _metrics_path.exists():
    try:
        with open(_metrics_path) as f:
            _metrics = json.load(f)
    except Exception as e:
        log.warning(f"Could not load metrics.json: {e}")

# ── lazy data cache ───────────────────────────────────────────────────────────
_price_df: Optional[pd.DataFrame] = None


def _get_price_df() -> pd.DataFrame:
    global _price_df
    if _price_df is not None:
        return _price_df
    if not DATA_FILE.exists():
        raise ModelNotReadyError(
            f"Price data not found at {DATA_FILE}. "
            "Run scripts/download_wfp_data.py first."
        )
    df = pd.read_csv(DATA_FILE, parse_dates=["date"])
    df.columns = [c.lower().strip() for c in df.columns]
    # Food-only scope — see backend/app/services/food_scope.py. This is the
    # fallback path used by list_commodities()/list_markets() when no
    # trained encoders.pkl exists yet; the real fix for a *trained* model is
    # scripts/train_models.py's own food filter (its encoder never learns
    # non-food classes in the first place), but this fallback would
    # otherwise offer Basin/Batteries/etc. as "known commodities" to
    # validate_input() even before a model has ever been trained.
    df = filter_food_only(df, source_label="model.py fallback")
    _price_df = df
    return _price_df


# ── public helpers ────────────────────────────────────────────────────────────

def status() -> dict:
    return {
        "price_model":   _price_model is not None,
        "encoders":      _encoders    is not None,
        "data_file":     DATA_FILE.exists(),
        "metrics":       _metrics,
    }


def predict_price(commodity: str, market: str, year: int, month: int) -> dict:
    if _price_model is None or _encoders is None:
        raise ModelNotReadyError("Price forecast model not loaded.")

    le_market    = _encoders.get("market")
    le_commodity = _encoders.get("commodity")
    features     = _encoders.get("price_features", [])

    if le_market is None or le_commodity is None:
        raise ModelNotReadyError("Encoders missing from encoders.pkl.")

    # validate known labels
    if market not in le_market.classes_:
        known = list(le_market.classes_)
        raise ValueError(f"Unknown market '{market}'. Known: {known}")
    if commodity not in le_commodity.classes_:
        known = list(le_commodity.classes_)
        raise ValueError(f"Unknown commodity '{commodity}'. Known: {known}")

    # build lag features from historical data
    df = _get_price_df()
    hist = df[
        (df["commodity"].str.lower() == commodity.lower()) &
        (df["market"].str.lower()    == market.lower())
    ].sort_values("date")

    def _lag(n: int) -> float:
        return float(hist["price"].iloc[-n]) if len(hist) >= n else float(hist["price"].mean())

    def _roll_mean(n: int) -> float:
        return float(hist["price"].tail(n).mean()) if len(hist) >= n else float(hist["price"].mean())

    def _pct_change(n: int) -> float:
        if len(hist) < n + 1:
            return 0.0
        old = hist["price"].iloc[-(n + 1)]
        new = hist["price"].iloc[-1]
        return float((new - old) / (old + 1e-9))

    row = {
        "market_enc":     le_market.transform([market])[0],
        "commodity_enc":  le_commodity.transform([commodity])[0],
        "year":           year,
        "month":          month,
        "quarter":        (month - 1) // 3 + 1,
        "month_sin":      np.sin(2 * np.pi * month / 12),
        "month_cos":      np.cos(2 * np.pi * month / 12),
        "price_lag1":     _lag(1),
        "price_lag3":     _lag(3),
        "price_lag6":     _lag(6),
        "price_lag12":    _lag(12),
        "price_roll3":    _roll_mean(3),
        "price_roll6":    _roll_mean(6),
        "price_roll12":   _roll_mean(12),
        "price_pct1":     _pct_change(1),
        "price_pct12":    _pct_change(12),
    }

    X = pd.DataFrame([row])[features]
    pred = float(_price_model.predict(X)[0])

    # Use the model's held-out error when available. The 80% two-sided
    # normal multiplier is a conservative approximation for this point
    # forecast; never allow a zero-width interval.
    metrics = _metrics.get("price_forecast", _metrics)
    mae = float(metrics.get("MAE_UGX", 0.0) or 0.0)
    residual_band = max(mae * 1.28, abs(pred) * 0.03)
    return {
        "commodity":    commodity,
        "market":       market,
        "year":         year,
        "month":        month,
        "predicted_price_ugx": round(pred, 2),
        "lower_bound_ugx":     round(max(0.0, pred - residual_band), 2),
        "upper_bound_ugx":     round(pred + residual_band, 2),
        "latest_price_ugx":    round(float(hist["price"].iloc[-1]), 2) if not hist.empty else round(pred, 2),
        "price_lag1":          round(float(hist["price"].iloc[-1]), 2) if not hist.empty else round(pred, 2),
        "currency":     "UGX",
    }


def get_metrics() -> dict:
    return _metrics


def list_markets() -> list[str]:
    if _encoders and "market" in _encoders:
        return sorted(_encoders["market"].classes_.tolist())
    try:
        df = _get_price_df()
        return sorted(df["market"].dropna().unique().tolist())
    except Exception:
        return []


def list_commodities() -> list[str]:
    if _encoders and "commodity" in _encoders:
        return sorted(_encoders["commodity"].classes_.tolist())
    try:
        df = _get_price_df()
        return sorted(df["commodity"].dropna().unique().tolist())
    except Exception:
        return []
