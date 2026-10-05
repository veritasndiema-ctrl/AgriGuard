"""
Unit tests for quant/intervals.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.intervals import (
    add_interval_columns,
    build_prediction_interval,
    conformal_interval_halfwidth,
    empirical_interval_halfwidth,
)


def test_empirical_interval_halfwidth_matches_notebook_formula():
    from scipy.stats import norm

    z = norm.ppf(0.5 + 0.80 / 2)
    result = empirical_interval_halfwidth(mae_mean=100.0, mae_std=20.0, confidence=0.80)
    assert result == pytest.approx(100.0 + z * 20.0)


def test_empirical_interval_halfwidth_nan_std_treated_as_zero():
    result = empirical_interval_halfwidth(mae_mean=50.0, mae_std=float("nan"), confidence=0.80)
    assert result == pytest.approx(50.0)  # z * 0 == 0, so the half-width collapses to mae_mean


def test_empirical_interval_halfwidth_nan_mean_returns_nan():
    assert np.isnan(empirical_interval_halfwidth(mae_mean=float("nan"), mae_std=10.0))


def test_conformal_interval_halfwidth_basic():
    residuals = [-5, -3, -1, 0, 1, 3, 5, 10, -10, 2]
    hw = conformal_interval_halfwidth(residuals, confidence=0.80)
    expected = float(np.quantile(np.abs(residuals), 0.80))
    assert hw == pytest.approx(expected)


def test_conformal_interval_halfwidth_empty_returns_nan():
    assert np.isnan(conformal_interval_halfwidth([]))


def test_conformal_interval_halfwidth_ignores_nans():
    hw_with_nan = conformal_interval_halfwidth([1, 2, 3, float("nan")], confidence=0.5)
    hw_without_nan = conformal_interval_halfwidth([1, 2, 3], confidence=0.5)
    assert hw_with_nan == pytest.approx(hw_without_nan)


def test_build_prediction_interval_floors_lower_at_zero():
    lower, upper = build_prediction_interval(point_forecast=50.0, halfwidth=200.0)
    assert lower == 0.0
    assert upper == 250.0


def test_build_prediction_interval_normal_case():
    lower, upper = build_prediction_interval(point_forecast=1000.0, halfwidth=150.0)
    assert lower == 850.0
    assert upper == 1150.0


def test_build_prediction_interval_nan_inputs():
    lower, upper = build_prediction_interval(float("nan"), 100.0)
    assert np.isnan(lower) and np.isnan(upper)


def test_add_interval_columns_prefers_conformal_over_approximation():
    backtest_results = pd.DataFrame(
        {
            "tier": ["tier_7_14"],
            "crop_enc": [0],
            "market_enc": [0],
            "mae_mean": [999.0],  # deliberately different from the residual-based answer
            "mae_std": [1.0],
        }
    )
    residuals_long = pd.DataFrame(
        {
            "tier": ["tier_7_14"] * 5,
            "crop_enc": [0] * 5,
            "market_enc": [0] * 5,
            "residual": [1, -2, 3, -4, 5],
        }
    )
    out = add_interval_columns(backtest_results, residuals_long, confidence=0.80)
    expected = conformal_interval_halfwidth([1, -2, 3, -4, 5], confidence=0.80)
    assert out.loc[0, "interval_halfwidth_ugx"] == pytest.approx(expected)
    assert out.loc[0, "interval_halfwidth_ugx"] != pytest.approx(999.0)


def test_add_interval_columns_falls_back_when_group_missing_from_residuals():
    backtest_results = pd.DataFrame(
        {
            "tier": ["tier_7_14"],
            "crop_enc": [0],
            "market_enc": [0],
            "mae_mean": [100.0],
            "mae_std": [10.0],
        }
    )
    # residuals_long covers a *different* group entirely
    residuals_long = pd.DataFrame(
        {"tier": ["tier_30"], "crop_enc": [9], "market_enc": [9], "residual": [1.0]}
    )
    out = add_interval_columns(backtest_results, residuals_long, confidence=0.80)
    expected = empirical_interval_halfwidth(100.0, 10.0, confidence=0.80)
    assert out.loc[0, "interval_halfwidth_ugx"] == pytest.approx(expected)


def test_add_interval_columns_no_residuals_uses_approximation_for_all():
    backtest_results = pd.DataFrame(
        {
            "tier": ["tier_7_14", "tier_30"],
            "crop_enc": [0, 1],
            "market_enc": [0, 1],
            "mae_mean": [100.0, 200.0],
            "mae_std": [10.0, 20.0],
        }
    )
    out = add_interval_columns(backtest_results, residuals_long=None, confidence=0.80)
    assert out["interval_halfwidth_ugx"].notna().all()
