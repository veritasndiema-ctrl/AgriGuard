"""
quant/risk_metrics.py — Commodity volatility & per-series risk scoring
================================================================================
Production implementation of the approach validated in
`notebooks/04_prediction_intervals_risk.ipynb`, sections 2-3.

A bare forecast can't tell a farmer how much to trust it. This module scores
each crop x market pair on two independent dimensions and combines them:

  1. **Backtest error** (`mape_mean`, from `quant.backtesting.run_backtest`)
     — how wrong the model has been historically for this series.
  2. **Raw commodity price volatility** (coefficient of variation on the
     cleaned price series) — how noisy the underlying market is,
     independent of the model.

A series can be low-risk on one dimension and high on the other, so both are
scored and averaged rather than collapsed into a single backtest metric.
The resulting `risk_score` (0-1, higher = riskier) is mapped to a 3-band
`confidence_label` for display — farmers need an at-a-glance signal, not a
raw number to interpret.

Shared discipline with the Vestora quant module (see requirements.txt).
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


# =============================================================================
# 1. Commodity price volatility
# =============================================================================


def compute_commodity_volatility(
    prices_clean: pd.DataFrame, commodity_col: str = "commodity", price_col: str = "price"
) -> pd.DataFrame:
    """
    Per-commodity coefficient of variation (std / mean) on the cleaned price
    series, matching notebooks/01_data_ingestion_eda.ipynb's volatility
    report. A constant price series yields cv == 0.0 rather than NaN.

    Returns a DataFrame indexed by `commodity_col` with a single `cv`
    column, sorted descending (most volatile commodity first).
    """
    grouped = prices_clean.groupby(commodity_col)[price_col].agg(["mean", "std"])
    grouped["std"] = grouped["std"].fillna(0.0)
    grouped["cv"] = np.where(grouped["mean"] != 0, grouped["std"] / grouped["mean"], 0.0)

    result = grouped[["cv"]].sort_values("cv", ascending=False)
    result.index.name = commodity_col
    return result


# =============================================================================
# 2. Confidence label bands
# =============================================================================


def confidence_label(score: float) -> str:
    """
    Maps a 0-1 risk score to a 3-band label for display, matching
    notebooks/04_prediction_intervals_risk.ipynb exactly:
      score < 0.33            -> "High confidence"
      0.33 <= score < 0.66    -> "Moderate confidence"
      score >= 0.66           -> "Low confidence — treat as directional only"
    A NaN score (e.g. no backtest coverage for this series) maps to
    "Unknown confidence" rather than silently falling into the lowest band.
    """
    if pd.isna(score):
        return "Unknown confidence"
    if score < 0.33:
        return "High confidence"
    if score < 0.66:
        return "Moderate confidence"
    return "Low confidence — treat as directional only"


# =============================================================================
# 3. Risk score per crop x market
# =============================================================================


def compute_risk_scores(
    backtest_results: pd.DataFrame,
    volatility: pd.DataFrame,
    le_crop,
) -> pd.DataFrame:
    """
    Builds the per crop x market risk table: backtest MAPE (averaged across
    tiers) joined with commodity volatility (`compute_commodity_volatility`),
    each min-max normalized to 0-1 and averaged into `risk_score`.

    `le_crop` is the fitted crop LabelEncoder saved by notebook 02
    (`feature_encoders.pkl`), used to map `crop_enc` back to the commodity
    name volatility is keyed on via `inverse_transform`.

    A commodity missing from `volatility` (no coverage) gets `cv` = NaN
    rather than raising; the corresponding `cv_norm` term is dropped to 0 in
    the final `risk_score` via `fillna`, so a single missing series doesn't
    crash the whole report. Returned sorted descending by `risk_score`
    (riskiest first).
    """
    risk = (
        backtest_results.groupby(["crop_enc", "market_enc"])
        .agg(mape_mean=("mape_mean", "mean"), n_folds=("n_folds", "sum"))
        .reset_index()
    )
    risk["commodity"] = le_crop.inverse_transform(risk["crop_enc"])

    risk = risk.merge(volatility[["cv"]].reset_index(), on="commodity", how="left")

    mape_min, mape_max = risk["mape_mean"].min(), risk["mape_mean"].max()
    risk["mape_norm"] = (risk["mape_mean"] - mape_min) / (mape_max - mape_min)

    cv_min, cv_max = risk["cv"].min(), risk["cv"].max()
    risk["cv_norm"] = (risk["cv"] - cv_min) / (cv_max - cv_min)

    risk["risk_score"] = (risk["mape_norm"].fillna(0) + risk["cv_norm"].fillna(0)) / 2

    return risk.sort_values("risk_score", ascending=False).reset_index(drop=True)


# =============================================================================
# 4. Apply confidence labels to a risk table
# =============================================================================


def add_confidence_labels(risk_report: pd.DataFrame) -> pd.DataFrame:
    """Adds a `confidence_label` column derived from `risk_score` via `confidence_label`."""
    out = risk_report.copy()
    out["confidence_label"] = out["risk_score"].apply(confidence_label)
    return out


# =============================================================================
# 5. End-to-end report builder
# =============================================================================


def build_risk_report(
    prices_clean: pd.DataFrame,
    backtest_results: pd.DataFrame,
    le_crop,
    commodity_col: str = "commodity",
    price_col: str = "price",
    save_path: Optional[str] = None,
) -> pd.DataFrame:
    """
    Full pipeline: cleaned prices -> commodity volatility -> risk scores ->
    confidence labels. Matches notebooks/04_prediction_intervals_risk.ipynb
    end to end. Writes to `save_path` (parquet) when provided — the
    production default is `data/processed/risk_scores.parquet`, per
    `quant/README.md`'s pipeline diagram.
    """
    volatility = compute_commodity_volatility(prices_clean, commodity_col=commodity_col, price_col=price_col)
    risk = compute_risk_scores(backtest_results, volatility, le_crop)
    risk_report = add_confidence_labels(risk)

    if save_path is not None:
        risk_report.to_parquet(save_path, index=False)

    return risk_report