"""
quant/features.py — Tiered feature engineering for the quant/ layer
================================================================================
Canonical implementation of the feature set explored in
`notebooks/02_feature_engineering.ipynb`. This module exists so that logic
has exactly one home: the notebook previously duplicated it inline, which is
how it drifted (its own markdown claimed it "mirrors `scripts/train_models.py
::build_features`" — it never did; the two pipelines share no code and build
different feature sets for different purposes). `scripts/build_quant_features.py`
imports this module directly, and the notebook should too if re-run.

This is deliberately independent of `scripts/train_models.py::build_features`.
That function builds a single flat feature set for one point-prediction model
(`backend/app/model.py`). This module builds three tier-specific feature sets
(7-14 / 30 / 60-90 day horizons) purely for `quant/`'s offline backtesting,
interval, and model-selection layer — a different consumer with different
needs. Keeping them separate is intentional, not an oversight to "fix" by
merging.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder

DATE_COL = "date"
PRICE_COL = "price"
GROUP_COLS = ("commodity", "market")

# Tier-specific lag sets: near-term tier uses short lags (in *observations*,
# not days — observation frequency varies by crop x market), directional
# tier uses longer lags. Must match quant/backtesting.py's TIER_HORIZONS keys.
TIER_LAG_CONFIG = {
    "tier_7_14": {"lag_steps": (1, 2, 3), "roll_window": 2},
    "tier_30": {"lag_steps": (1, 3, 6), "roll_window": 3},
    "tier_60_90": {"lag_steps": (2, 6, 12), "roll_window": 6},
}

FEATURE_COLS_BASE = [
    "crop_enc", "market_enc",
    "year", "month", "quarter", "week", "day_of_year",
    "month_sin", "month_cos",
]


def add_temporal_features(frame: pd.DataFrame, date_col: str = DATE_COL) -> pd.DataFrame:
    frame = frame.copy()
    frame["year"] = frame[date_col].dt.year
    frame["month"] = frame[date_col].dt.month
    frame["quarter"] = frame[date_col].dt.quarter
    frame["week"] = frame[date_col].dt.isocalendar().week.astype(int)
    frame["day_of_year"] = frame[date_col].dt.dayofyear
    frame["month_sin"] = np.sin(2 * np.pi * frame["month"] / 12)
    frame["month_cos"] = np.cos(2 * np.pi * frame["month"] / 12)
    return frame


def add_lag_features(
    frame: pd.DataFrame,
    lag_steps: tuple = (1, 3, 6),
    roll_window: int = 3,
    group_cols: tuple = GROUP_COLS,
    price_col: str = PRICE_COL,
) -> pd.DataFrame:
    frame = frame.copy()
    group_cols = list(group_cols)
    for step in lag_steps:
        frame[f"price_lag_{step}"] = frame.groupby(group_cols)[price_col].shift(step)
    # Shift before rolling: the current target must never be part of a
    # training feature. Including it made offline metrics optimistic and
    # produced a train/serve mismatch in every live forecast.
    frame[f"price_roll_{roll_window}_avg"] = (
        frame.groupby(group_cols)[price_col].transform(
            lambda s: s.shift(1).rolling(roll_window, min_periods=1).mean()
        )
    )
    frame[f"price_roll_{roll_window}_std"] = (
        frame.groupby(group_cols)[price_col].transform(
            lambda s: s.shift(1).rolling(roll_window, min_periods=2).std()
        )
    )
    return frame


def build_tier_features(
    prices_clean: pd.DataFrame,
    tier_config: dict = TIER_LAG_CONFIG,
    le_crop: Optional[LabelEncoder] = None,
    le_market: Optional[LabelEncoder] = None,
) -> tuple[dict[str, pd.DataFrame], LabelEncoder, LabelEncoder]:
    """
    Builds one feature DataFrame per tier from a cleaned price DataFrame
    (same shape as `scripts/train_models.py::load_and_clean`'s output —
    columns `commodity`, `market`, `date`, `price`).

    Returns (tier_frames, le_crop, le_market) where each `tier_frames[tier]`
    has columns `FEATURE_COLS_BASE + <tier's lag/roll columns> + [price, date]`
    and NaN rows (insufficient history for that tier's lags) already dropped.
    Pass existing encoders to keep train/serve encoding consistent; omit to
    fit fresh ones (fit on the full frame passed in).
    """
    df = prices_clean.sort_values(list(GROUP_COLS) + [DATE_COL]).reset_index(drop=True)
    df = add_temporal_features(df)

    le_crop = le_crop or LabelEncoder().fit(df["commodity"].astype(str))
    le_market = le_market or LabelEncoder().fit(df["market"].astype(str))

    tier_frames = {}
    for tier, cfg in tier_config.items():
        tf = add_lag_features(df, **cfg)
        tf["crop_enc"] = le_crop.transform(tf["commodity"].astype(str))
        tf["market_enc"] = le_market.transform(tf["market"].astype(str))

        lag_cols = [f"price_lag_{s}" for s in cfg["lag_steps"]] + [
            f"price_roll_{cfg['roll_window']}_avg", f"price_roll_{cfg['roll_window']}_std",
        ]
        feature_cols = FEATURE_COLS_BASE + lag_cols
        tf = tf.dropna(subset=feature_cols + [PRICE_COL])
        tier_frames[tier] = tf[feature_cols + [PRICE_COL, DATE_COL]].reset_index(drop=True)

    return tier_frames, le_crop, le_market


def save_tier_features(
    tier_frames: dict[str, pd.DataFrame],
    le_crop: LabelEncoder,
    le_market: LabelEncoder,
    processed_dir: Path,
    tier_config: dict = TIER_LAG_CONFIG,
) -> None:
    """Writes `features_{tier}.parquet` per tier + `feature_encoders.pkl`,
    in the exact layout `quant/backtesting.py::run_backtest` reads."""
    import joblib

    processed_dir = Path(processed_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)

    for tier, frame in tier_frames.items():
        frame.to_parquet(processed_dir / f"features_{tier}.parquet", index=False)

    joblib.dump(
        {"le_crop": le_crop, "le_market": le_market, "tier_lag_config": tier_config},
        processed_dir / "feature_encoders.pkl",
    )
