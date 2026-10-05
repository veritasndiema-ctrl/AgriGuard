"""
backend/app/services/quant_bridge.py — Wires quant/ into the live forecast path
================================================================================
Until now, quant/ (backtesting, intervals, model_selection, risk_metrics) only
ever ran offline — against notebooks and `data/processed/features_*.parquet`
tables produced by `scripts/build_quant_features.py`. `backend/app/routers/
forecasts.py`, the endpoint AgriGuard actually serves forecasts from, had no
connection to it: prediction intervals there came from Prophet's own output
(or a flat 10%-of-std band for the linear fallback) and "confidence" was a
hand-rolled heuristic (`_clamp_confidence`) derived from interval width alone
— not from how right or wrong the model had actually been on data like this.

This module is the bridge: given the same (date, price) training window
`forecasts.py` already builds for a request, it runs a real walk-forward
backtest (`quant.backtesting.backtest_group`) on that single series using the
same feature set (`quant.features`) and the same model family (XGBoost) the
offline quant/ pipeline uses, then sizes the interval with
`quant.intervals`' conformal (empirical-quantile) method over the resulting
fold residuals — the "production upgrade" quant/intervals.py's own docstring
describes, now actually reachable from a live request instead of only from
a notebook.

Design constraint: this must never be the reason a live forecast fails.
Every failure mode (xgboost missing, too little history for a single
walk-forward fold, an unexpected error anywhere in the pipeline) returns
None rather than raising, and `forecasts.py` treats None as "fall back to
the existing heuristic" — quant_bridge is an enhancement layered on top of
the pre-existing pipeline, not a replacement dependency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from quant import backtesting, features, intervals

logger = logging.getLogger(__name__)

# Map a requested forecast horizon (days) onto quant/backtesting.py's
# existing TIER_HORIZONS rather than inventing a second horizon scheme.
# Boundaries sit at the midpoints between the tiers' own day-ish labels
# ("7-14", "30", "60-90").
_TIER_DAY_BOUNDARIES: tuple[tuple[float, str], ...] = (
    (21, "tier_7_14"),
    (45, "tier_30"),
    (float("inf"), "tier_60_90"),
)


def _nearest_tier(horizon_days: int) -> str:
    for upper, tier in _TIER_DAY_BOUNDARIES:
        if horizon_days <= upper:
            return tier
    return "tier_60_90"  # unreachable (last boundary is inf) — defensive only


@dataclass
class QuantConfidence:
    """Result of a successful quant-backed backtest for one series."""
    halfwidth: float   # UGX half-width — feed to apply_halfwidth() per forecast point
    confidence: float  # [0, 1], derived from backtested MAPE on this series
    n_folds: int        # How many walk-forward folds this rests on
    tier: str           # Which quant/backtesting.py tier was used


def apply_halfwidth(point_forecast: float, halfwidth: float) -> tuple[float, float]:
    """Re-exports quant.intervals.build_prediction_interval so forecasts.py
    doesn't need to import quant/ directly for this one call."""
    return intervals.build_prediction_interval(point_forecast, halfwidth)


def quant_confidence(train: pd.DataFrame, horizon_days: int) -> Optional[QuantConfidence]:
    """
    Backtests `train` (columns: date, price — a single commodity x market
    series, already the request's training window) and returns a
    QuantConfidence sized from the real backtested error, or None if quant/
    can't do better here than the router's existing heuristic.

    None is returned (never raised) when:
      - xgboost isn't installed (quant/backtesting.py's model factory is a
        lazy import, same optional-dependency pattern as the rest of the app)
      - the series is too short for even one walk-forward fold at this tier
        (quant.backtesting.backtest_group's own coverage-gap check)
      - anything else in the feature/backtest pipeline raises unexpectedly
    """
    try:
        tier = _nearest_tier(horizon_days)
        tier_cfg = backtesting.TIER_HORIZONS[tier]
        lag_cfg = features.TIER_LAG_CONFIG[tier]

        # features.build_tier_features() is written for a multi-series frame
        # (it label-encodes commodity/market to build crop_enc/market_enc).
        # A single series still passes through cleanly — both encoders just
        # collapse to one class each, which is fine: backtest_group() only
        # uses crop_enc/market_enc as feature columns here, not to
        # discriminate between groups (there's only one).
        prices_clean = train[["date", "price"]].copy()
        prices_clean["commodity"] = "series"
        prices_clean["market"] = "series"

        tier_frames, _, _ = features.build_tier_features(
            prices_clean, tier_config={tier: lag_cfg}
        )
        feat = tier_frames[tier]
        if feat.empty:
            return None

        lag_cols = [f"price_lag_{s}" for s in lag_cfg["lag_steps"]] + [
            f"price_roll_{lag_cfg['roll_window']}_avg",
            f"price_roll_{lag_cfg['roll_window']}_std",
        ]
        feature_cols = features.FEATURE_COLS_BASE + lag_cols

        result = backtesting.backtest_group(
            feat,
            feature_cols,
            tier_cfg["horizon_steps"],
            min_train=backtesting.DEFAULT_MIN_TRAIN,
        )
        if result is None:
            return None

        residuals = result.get("residuals") or []
        halfwidth = (
            intervals.conformal_interval_halfwidth(residuals)
            if residuals
            else intervals.empirical_interval_halfwidth(result["mae_mean"], result["mae_std"])
        )
        if pd.isna(halfwidth):
            return None

        # Simple, bounded confidence from backtested MAPE: 0% MAPE -> 1.0,
        # 100%+ MAPE -> 0.0. Deliberately not a calibrated probability —
        # it's meant as an honest "how wrong was this model on data just
        # like this", which is more than the width-only heuristic it
        # replaces ever reflected.
        mape = result.get("mape_mean")
        confidence = 0.5 if pd.isna(mape) else max(0.0, min(1.0, 1.0 - float(mape)))

        return QuantConfidence(
            halfwidth=float(halfwidth),
            confidence=round(confidence, 3),
            n_folds=int(result["n_folds"]),
            tier=tier,
        )

    except ImportError as exc:
        logger.info(
            "quant_bridge: optional dependency unavailable — using heuristic confidence instead (%s).",
            exc,
        )
        return None
    except Exception as exc:
        logger.warning("quant_bridge: falling back to heuristic confidence — %s", exc)
        return None
