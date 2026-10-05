"""
quant/model_selection.py — Per-series model selection for AgriGuard forecasts
================================================================================
Production implementation of the approach validated in
`notebooks/05_model_export.ipynb`, section 1 — with the Prophet-vs-XGBoost
comparison actually run per group, rather than left as the notebook's
explicit placeholder ("in a full run, backtest_prophet would be applied per
group/tier here").

Compares backtested XGBoost MAPE (quant/backtesting.py) against a Prophet
backtest run here, and picks whichever generalizes better **per crop x
market x tier**, rather than assuming one model family everywhere. Falls
back to Prophet automatically wherever XGBoost's backtest MAPE is missing
or clearly worse, matching the fallback-chain behavior described in the
README's fail-safe section.

Prophet is optional at import time (lazy-imported inside `backtest_prophet`)
so this module can be imported, and the XGBoost-only parts used, without
Prophet installed.
"""

from __future__ import annotations

import warnings
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_percentage_error

warnings.filterwarnings("ignore", category=FutureWarning)

XGBOOST = "xgboost"
PROPHET = "prophet"


# =============================================================================
# 1. Prophet backtest (single crop x market series, one tier)
# =============================================================================


def backtest_prophet(group: pd.DataFrame, horizon_steps: int, min_train: int = 12) -> Optional[float]:
    """
    Backtests Prophet on a single crop x market series for one tier's
    horizon, using a simple train/test tail split (Prophet's own
    cross-validation utility is overkill for series this short — most
    AgriGuard crop x market pairs have well under 100 observations).

    Returns the test-window MAPE, or None if the series is too short or
    Prophet fails to fit (thin/degenerate series are common enough across
    crop x market pairs that this must not raise).
    """
    from prophet import Prophet

    group = group.sort_values("date").rename(columns={"date": "ds", "price": "y"})
    if len(group) < min_train + horizon_steps:
        return None

    train = group.iloc[:-horizon_steps]
    test = group.iloc[-horizon_steps:]

    try:
        model = Prophet(yearly_seasonality=True, weekly_seasonality=False, daily_seasonality=False)
        model.fit(train[["ds", "y"]])
        future = model.make_future_dataframe(periods=horizon_steps, freq="W")
        forecast = model.predict(future).tail(horizon_steps)
        return float(mean_absolute_percentage_error(test["y"].to_numpy(), forecast["yhat"].to_numpy()))
    except Exception:
        return None


# =============================================================================
# 2. Per-group model selection
# =============================================================================


def select_model_per_group(
    backtest_results: pd.DataFrame,
    feature_frames_by_tier: dict[str, pd.DataFrame],
    min_train: int = 12,
    tiers: Optional[dict] = None,
) -> pd.DataFrame:
    """
    For every (tier, crop_enc, market_enc) group, compares the already-
    backtested XGBoost MAPE (from `backtest_results`, i.e.
    `quant.backtesting.run_backtest`) against a freshly-run Prophet
    backtest on the same tier's raw price series, and records whichever
    model generalized better.

    `feature_frames_by_tier` maps tier name -> the tier's feature DataFrame
    (as loaded from `features_{tier}.parquet`), which must retain `date`
    and `price` columns alongside `crop_enc`/`market_enc` — the format
    `quant.backtesting.run_backtest` already expects.

    Returns a DataFrame with columns: tier, crop_enc, market_enc, xgb_mape,
    prophet_mape, chosen_model ("xgboost" or "prophet").

    Selection rule: choose XGBoost unless XGBoost's backtest MAPE is
    missing/NaN, or Prophet's MAPE for the same group is strictly lower —
    an explicit fallback chain, not a silent default, per the README's
    fail-safe / backtested-selection design goal.
    """
    from quant.backtesting import TIER_HORIZONS

    tiers = tiers or TIER_HORIZONS

    xgb_choice = (
        backtest_results.groupby(["crop_enc", "market_enc", "tier"])["mape_mean"]
        .mean()
        .reset_index()
        .rename(columns={"mape_mean": "xgb_mape"})
    )

    prophet_rows = []
    for tier, cfg in tiers.items():
        feat = feature_frames_by_tier.get(tier)
        if feat is None:
            continue
        for (crop_enc, market_enc), group in feat.groupby(["crop_enc", "market_enc"]):
            prophet_mape = backtest_prophet(group, cfg["horizon_steps"], min_train=min_train)
            prophet_rows.append(
                {"tier": tier, "crop_enc": crop_enc, "market_enc": market_enc, "prophet_mape": prophet_mape}
            )
    prophet_df = pd.DataFrame(prophet_rows, columns=["tier", "crop_enc", "market_enc", "prophet_mape"])

    model_choice = xgb_choice.merge(prophet_df, on=["tier", "crop_enc", "market_enc"], how="outer")

    def _choose(row) -> str:
        xgb_mape = row.get("xgb_mape")
        prophet_mape = row.get("prophet_mape")
        if pd.isna(xgb_mape):
            return PROPHET if not pd.isna(prophet_mape) else XGBOOST
        if not pd.isna(prophet_mape) and prophet_mape < xgb_mape:
            return PROPHET
        return XGBOOST

    model_choice["chosen_model"] = model_choice.apply(_choose, axis=1)
    return model_choice


# =============================================================================
# 3. Retrain the selected model on full history
# =============================================================================


def train_selected_model(model_type: str, group: pd.DataFrame, feature_cols: Optional[list[str]] = None):
    """
    Fits the chosen model type on the **full** available history for one
    crop x market series (backtest folds intentionally held data back;
    production shouldn't). `feature_cols` is required for `"xgboost"` and
    ignored for `"prophet"` (which trains directly on `date`/`price`).
    """
    if model_type == XGBOOST:
        if not feature_cols:
            raise ValueError("feature_cols is required to train an xgboost model")

        from xgboost import XGBRegressor

        model = XGBRegressor(
            n_estimators=400, learning_rate=0.05, max_depth=6,
            subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=0,
        )
        model.fit(group[feature_cols], group["price"])
        return model

    if model_type == PROPHET:
        from prophet import Prophet

        train = group.sort_values("date").rename(columns={"date": "ds", "price": "y"})
        model = Prophet(yearly_seasonality=True, weekly_seasonality=False, daily_seasonality=False)
        model.fit(train[["ds", "y"]])
        return model

    raise ValueError(f"Unknown model_type: {model_type!r} (expected {XGBOOST!r} or {PROPHET!r})")