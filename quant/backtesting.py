"""
quant/backtesting.py — Walk-forward backtesting for AgriGuard price forecasts
================================================================================
Production implementation of the approach validated in
`notebooks/03_walkforward_backtesting.ipynb`. Replaces a single 80/20 split
with rolling-window, walk-forward validation, evaluated separately per tier
(7-14, 30, 60-90 day horizons) and per crop x market series.

Why walk-forward, not a single split: a single 80/20 split tells you how a
model does on one fixed future window. Walk-forward re-trains on an expanding
window and tests on each subsequent slice — much closer to how the model is
actually used in production (retrained periodically, always predicting
forward), and it surfaces performance that degrades in specific seasons/years
rather than averaging that away.

Shared discipline with the Vestora quant module (see requirements.txt).

Typical usage:

    from quant.backtesting import run_backtest, summarize_by_tier

    backtest_results, residuals = run_backtest(Path("data/processed"))
    tier_summary = summarize_by_tier(backtest_results)

`backtest_results` is what notebook 04 (quant/intervals.py, quant/risk_metrics.py)
and notebook 05 (quant/model_selection.py) build on. `residuals` is the
long-format fold-level residual table — kept explicitly (the notebook's own
prediction-interval step notes it "isn't retained" and falls back to a
mean/std approximation; here it is, so quant/intervals.py can use the true
empirical residual quantile instead).
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score

warnings.filterwarnings("ignore", category=UserWarning)

# =============================================================================
# Tier configuration — must match notebooks/02_feature_engineering.ipynb and
# notebooks/03_walkforward_backtesting.ipynb. horizon_steps is expressed in
# *observations ahead*, not days, since observation frequency varies by
# crop x market (see notebooks/01_data_ingestion_eda.ipynb's coverage report).
# =============================================================================

TIER_HORIZONS = {
    "tier_7_14": {"horizon_steps": 2, "label": "7-14 days"},
    "tier_30": {"horizon_steps": 4, "label": "30 days"},
    "tier_60_90": {"horizon_steps": 8, "label": "60-90 days"},
}

DEFAULT_MIN_TRAIN = 12
TARGET_COL = "price"
DATE_COL = "date"
GROUP_COLS = ("crop_enc", "market_enc")


# =============================================================================
# 1. Walk-forward split generator
# =============================================================================


def walk_forward_folds(
    n_rows: int, horizon_steps: int, min_train: int = DEFAULT_MIN_TRAIN, step: int = 1
) -> list[tuple[int, int]]:
    """
    Yields (train_end_idx, test_end_idx) index pairs for expanding-window
    walk-forward CV: each fold trains on everything up to a cut point and
    tests on the next `horizon_steps` observations.

    Run per crop x market group, not globally — series lengths and gap
    structure differ across pairs.
    """
    if n_rows <= 0 or horizon_steps <= 0 or min_train <= 0:
        return []

    folds = []
    train_end = min_train
    while train_end + horizon_steps <= n_rows:
        folds.append((train_end, train_end + horizon_steps))
        train_end += step
    return folds


# =============================================================================
# 2. Backtest runner
# =============================================================================


def _default_model_factory():
    """Same model family as production (XGBRegressor) — lazy import so this
    module can be imported without xgboost installed unless actually used."""
    try:
        from xgboost import XGBRegressor

        return XGBRegressor(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbosity=0,
        )
    except ImportError:
        # Keep live uncertainty calibration available in minimal API
        # deployments. This deterministic tree model has the same sklearn
        # estimator interface and is only a dependency fallback.
        from sklearn.ensemble import HistGradientBoostingRegressor

        return HistGradientBoostingRegressor(
            max_iter=200, learning_rate=0.05, max_leaf_nodes=15, random_state=42
        )


def backtest_group(
    group: pd.DataFrame,
    feature_cols: list[str],
    horizon_steps: int,
    min_train: int = DEFAULT_MIN_TRAIN,
    target_col: str = TARGET_COL,
    date_col: str = DATE_COL,
    model_factory: Optional[Callable] = None,
) -> Optional[dict]:
    """
    Runs walk-forward backtesting for a single crop x market series at one
    tier's horizon. Returns fold-aggregated metrics plus the raw fold-level
    residuals (signed, actual - predicted), or None if the series is too
    short for this tier's `min_train` requirement — that's a coverage gap,
    not an error, so callers should skip rather than crash on None.
    """
    model_factory = model_factory or _default_model_factory
    group = group.sort_values(date_col).reset_index(drop=True)
    folds = walk_forward_folds(len(group), horizon_steps, min_train=min_train)
    if not folds:
        return None

    fold_metrics = []
    residuals: list = []

    for train_end, test_end in folds:
        train = group.iloc[:train_end]
        test = group.iloc[train_end:test_end]
        if len(test) == 0 or train[target_col].std() == 0:
            continue

        try:
            model = model_factory()
            model.fit(train[feature_cols], train[target_col])
            preds = model.predict(test[feature_cols])
        except Exception:
            # A single bad fold (e.g. a degenerate feature slice) shouldn't
            # sink the whole group's backtest — record it as a skip.
            continue

        actual = test[target_col].to_numpy()
        residuals.extend((actual - preds).tolist())

        fold_metrics.append(
            {
                "mae": mean_absolute_error(actual, preds),
                "mape": mean_absolute_percentage_error(actual, preds),
                "r2": r2_score(actual, preds) if len(test) > 1 else np.nan,
                "n_test": len(test),
            }
        )

    if not fold_metrics:
        return None

    m = pd.DataFrame(fold_metrics)
    return {
        "n_folds": len(m),
        "mae_mean": m["mae"].mean(),
        "mae_std": m["mae"].std(),
        "mape_mean": m["mape"].mean(),
        "mape_std": m["mape"].std(),
        "r2_mean": m["r2"].mean(),
        "residuals": residuals,
    }


# =============================================================================
# 3. Run backtest across all tiers and crop x market pairs
# =============================================================================


def run_backtest(
    processed_dir: Path,
    tiers: dict = TIER_HORIZONS,
    min_train: int = DEFAULT_MIN_TRAIN,
    model_factory: Optional[Callable] = None,
    save: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Runs the full backtest across every tier and every crop x market pair,
    reading `features_{tier}.parquet` produced by
    `scripts/build_quant_features.py` (via `quant.features`) from
    `processed_dir`.

    Returns (backtest_results, residuals_long):
      - backtest_results: one row per (tier, crop_enc, market_enc) with
        fold-aggregated metrics. Written to
        `processed_dir/backtest_results.parquet` when save=True.
      - residuals_long: one row per (tier, crop_enc, market_enc, residual),
        the raw fold-level errors. Written to
        `processed_dir/backtest_residuals.parquet` when save=True. This is
        what quant/intervals.py should use for true empirical-quantile
        conformal intervals rather than the mean/std approximation.
    """
    processed_dir = Path(processed_dir)
    results = []
    residual_rows = []

    for tier, cfg in tiers.items():
        feat_path = processed_dir / f"features_{tier}.parquet"
        if not feat_path.exists():
            warnings.warn(
                f"Skipping {tier}: {feat_path} not found "
                "(run `python scripts/build_quant_features.py` first)"
            )
            continue

        feat = pd.read_parquet(feat_path)
        feature_cols = [c for c in feat.columns if c not in (TARGET_COL, DATE_COL)]

        for (crop_enc, market_enc), group in feat.groupby(list(GROUP_COLS)):
            metrics = backtest_group(
                group, feature_cols, cfg["horizon_steps"], min_train=min_train, model_factory=model_factory
            )
            if metrics is None:
                continue

            residuals = metrics.pop("residuals")
            metrics.update(
                {"tier": tier, "tier_label": cfg["label"], "crop_enc": crop_enc, "market_enc": market_enc}
            )
            results.append(metrics)

            for r in residuals:
                residual_rows.append(
                    {"tier": tier, "tier_label": cfg["label"], "crop_enc": crop_enc, "market_enc": market_enc, "residual": r}
                )

    backtest_results = pd.DataFrame(results)
    residuals_long = pd.DataFrame(residual_rows)

    if save and not backtest_results.empty:
        backtest_results.to_parquet(processed_dir / "backtest_results.parquet", index=False)
        residuals_long.to_parquet(processed_dir / "backtest_residuals.parquet", index=False)

    return backtest_results, residuals_long


# =============================================================================
# 4. Aggregate results per tier
# =============================================================================


def summarize_by_tier(backtest_results: pd.DataFrame, tiers: dict = TIER_HORIZONS) -> pd.DataFrame:
    """
    Per-tier summary table — this is what should replace the README's single
    blended MAE/MAPE/R2 figures, reported per tier as promised.

    Expected pattern: MAE/MAPE increase and R2 decreases moving from the
    7-14 day tier to the 60-90 day tier. If it doesn't, that's a signal to
    re-check the lag/horizon configuration in notebook 02 before trusting
    the numbers.
    """
    if backtest_results.empty:
        return backtest_results

    order = [cfg["label"] for cfg in tiers.values()]
    return (
        backtest_results.groupby("tier_label")
        .agg(
            n_crop_market_pairs=("mae_mean", "count"),
            mae_mean_ugx=("mae_mean", "mean"),
            mape_mean_pct=("mape_mean", lambda s: s.mean() * 100),
            r2_mean=("r2_mean", "mean"),
        )
        .reindex(order)
    )