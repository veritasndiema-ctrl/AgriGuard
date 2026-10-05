"""
quant/intervals.py — Prediction intervals for AgriGuard price forecasts
================================================================================
Production implementation of the approach validated in
`notebooks/04_prediction_intervals_risk.ipynb`, section 1.

Two ways to size a prediction interval's half-width around a point forecast:

  1. **Empirical (mean/std) approximation** — `empirical_interval_halfwidth`.
     Assumes fold-level absolute errors are roughly normal and sizes the
     half-width from their mean and standard deviation
     (`quant.backtesting.run_backtest`'s `mae_mean` / `mae_std`). This is
     what notebook 04 uses, and it's the only option available for a
     (tier, crop_enc, market_enc) group with no stored residuals.
  2. **Conformal (empirical-quantile) interval** — `conformal_interval_halfwidth`.
     Takes the actual fold-level residuals for a group
     (`quant.backtesting.run_backtest`'s `residuals_long`) and reads off the
     half-width directly as the target-confidence quantile of their
     absolute values. No normality assumption, and it's what notebook 04
     flags as the improvement to make "once [residuals] are retained" —
     they now are, so this module prefers it automatically.

`add_interval_columns` is the entry point used by the rest of the pipeline
(see `quant/README.md`): it walks `backtest_results` row by row and uses the
conformal method wherever residuals exist for that group, falling back to
the empirical approximation otherwise — so a partial residual table still
degrades gracefully rather than producing NaNs.

Shared discipline with the Vestora quant module (see requirements.txt).
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

DEFAULT_CONFIDENCE = 0.80
DEFAULT_GROUP_COLS = ("tier", "crop_enc", "market_enc")


# =============================================================================
# 1. Empirical (mean/std) half-width — notebook 04's original approximation
# =============================================================================


def empirical_interval_halfwidth(
    mae_mean: float, mae_std: float, confidence: float = DEFAULT_CONFIDENCE
) -> float:
    """
    Half-width from a normal approximation over fold-level absolute errors:
    ``mae_mean + z * mae_std``, where ``z`` is the two-sided z-score for
    `confidence` (e.g. confidence=0.80 -> the 90th percentile of the
    standard normal, matching notebook 04's formula exactly).

    A NaN `mae_std` (a group with only one backtest fold, so std is
    undefined) is treated as 0 — the half-width collapses to `mae_mean`
    rather than propagating NaN. A NaN `mae_mean` (no backtest coverage at
    all for this group) returns NaN; there's nothing to approximate from.
    """
    from scipy.stats import norm

    if pd.isna(mae_mean):
        return float("nan")

    std = 0.0 if pd.isna(mae_std) else float(mae_std)
    z = norm.ppf(0.5 + confidence / 2)
    return float(mae_mean) + z * std


# =============================================================================
# 2. Conformal (empirical-quantile) half-width — the production upgrade
# =============================================================================


def conformal_interval_halfwidth(
    residuals: Sequence[float], confidence: float = DEFAULT_CONFIDENCE
) -> float:
    """
    Half-width as the `confidence` quantile of the *absolute* fold-level
    residuals (actual - predicted), e.g. confidence=0.80 -> the 80th
    percentile of |residual| — a distribution-free ("conformal-style")
    interval that doesn't assume errors are normally distributed.

    NaNs in `residuals` are dropped before the quantile is taken (a
    residual can be NaN if a fold's target was missing). An empty or
    all-NaN input returns NaN rather than raising, matching
    `empirical_interval_halfwidth`'s no-coverage behavior.
    """
    arr = np.asarray(list(residuals), dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.quantile(np.abs(arr), confidence))


# =============================================================================
# 3. Point forecast -> (lower, upper) bounds
# =============================================================================


def build_prediction_interval(point_forecast: float, halfwidth: float) -> tuple[float, float]:
    """
    Turns a point forecast + half-width into a ``(lower, upper)`` bound pair.

    `lower` is floored at 0.0 — a UGX price forecast can never legitimately
    go negative, and a wide half-width on a small point forecast would
    otherwise produce a nonsensical negative lower bound. `upper` is never
    floored (there's no equivalent ceiling). NaN in either input returns
    ``(nan, nan)`` rather than a half-valid pair.
    """
    if pd.isna(point_forecast) or pd.isna(halfwidth):
        return float("nan"), float("nan")

    lower = max(0.0, float(point_forecast) - float(halfwidth))
    upper = float(point_forecast) + float(halfwidth)
    return lower, upper


# =============================================================================
# 4. Attach interval half-widths to a backtest_results table
# =============================================================================


def add_interval_columns(
    backtest_results: pd.DataFrame,
    residuals_long: Optional[pd.DataFrame] = None,
    confidence: float = DEFAULT_CONFIDENCE,
    group_cols: Sequence[str] = DEFAULT_GROUP_COLS,
) -> pd.DataFrame:
    """
    Adds an `interval_halfwidth_ugx` column to `backtest_results` (one row
    per `group_cols`, as produced by `quant.backtesting.run_backtest`).

    Per row, prefers the conformal half-width (`conformal_interval_halfwidth`)
    computed from that row's residuals in `residuals_long` (as produced by
    `quant.backtesting.run_backtest`'s second return value). Falls back to
    the empirical approximation (`empirical_interval_halfwidth`, from that
    row's `mae_mean` / `mae_std`) when:
      - `residuals_long` is None or empty (no residual table available at
        all, e.g. an older backtest run), or
      - the row's group simply has no matching rows in `residuals_long`
        (a coverage gap — every group in the residual table is used, but
        not every group is guaranteed to be in it).

    Returns a copy; `backtest_results` itself is not mutated.
    """
    out = backtest_results.copy()
    group_cols = list(group_cols)

    grouped_residuals = None
    if residuals_long is not None and not residuals_long.empty:
        grouped_residuals = residuals_long.groupby(group_cols)["residual"].apply(list)

    halfwidths = []
    for _, row in out.iterrows():
        key = tuple(row[col] for col in group_cols)

        residuals_for_group = None
        if grouped_residuals is not None and key in grouped_residuals.index:
            residuals_for_group = grouped_residuals.loc[key]

        if residuals_for_group:
            halfwidth = conformal_interval_halfwidth(residuals_for_group, confidence=confidence)
        else:
            halfwidth = empirical_interval_halfwidth(
                row.get("mae_mean"), row.get("mae_std"), confidence=confidence
            )
        halfwidths.append(halfwidth)

    out["interval_halfwidth_ugx"] = halfwidths
    return out