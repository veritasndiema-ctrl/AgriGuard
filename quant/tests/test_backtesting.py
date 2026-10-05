"""
Unit tests for quant/backtesting.py
Uses small synthetic series — no parquet fixtures or trained models required.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.backtesting import (
    TIER_HORIZONS,
    backtest_group,
    run_backtest,
    summarize_by_tier,
    walk_forward_folds,
)


class _ConstantModel:
    """Deterministic stand-in for XGBRegressor — avoids a hard xgboost
    dependency in unit tests that only exercise the walk-forward machinery."""

    def fit(self, X, y):
        self._mean = float(y.mean())
        return self

    def predict(self, X):
        return np.full(len(X), self._mean)


def _constant_model_factory():
    return _ConstantModel()


def _make_series(n=30, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="W")
    price = 1000 + np.cumsum(rng.normal(0, 20, size=n))
    return pd.DataFrame(
        {
            "date": dates,
            "price": price,
            "crop_enc": 0,
            "market_enc": 0,
            "year": dates.year,
            "month": dates.month,
        }
    )


def test_walk_forward_folds_basic():
    folds = walk_forward_folds(40, horizon_steps=4, min_train=12)
    assert folds[0] == (12, 16)
    assert folds[-1][1] <= 40
    assert all(b - a == 4 for a, b in folds)


def test_walk_forward_folds_matches_notebook_example():
    # notebooks/03_walkforward_backtesting.ipynb sanity check: n_rows=40, horizon=4
    folds = walk_forward_folds(40, 4)
    assert len(folds) == 25
    assert folds[:5] == [(12, 16), (13, 17), (14, 18), (15, 19), (16, 20)]


def test_walk_forward_folds_too_short_returns_empty():
    assert walk_forward_folds(5, horizon_steps=4, min_train=12) == []


def test_walk_forward_folds_zero_or_negative_inputs():
    assert walk_forward_folds(0, 4) == []
    assert walk_forward_folds(40, 0) == []
    assert walk_forward_folds(40, 4, min_train=0) == []


def test_backtest_group_too_short_returns_none():
    group = _make_series(n=10)
    result = backtest_group(
        group, feature_cols=["month"], horizon_steps=4, min_train=12, model_factory=_constant_model_factory
    )
    assert result is None


def test_backtest_group_returns_expected_keys():
    group = _make_series(n=30)
    result = backtest_group(
        group, feature_cols=["month"], horizon_steps=2, min_train=12, model_factory=_constant_model_factory
    )
    assert result is not None
    for key in ("n_folds", "mae_mean", "mae_std", "mape_mean", "mape_std", "r2_mean", "residuals"):
        assert key in result
    assert result["n_folds"] > 0
    assert len(result["residuals"]) == result["n_folds"] * 2  # horizon_steps=2 per fold


def test_backtest_group_zero_variance_train_is_skipped():
    group = _make_series(n=20)
    group["price"] = 1000.0  # constant -> std == 0 for every fold's training slice
    result = backtest_group(
        group, feature_cols=["month"], horizon_steps=2, min_train=12, model_factory=_constant_model_factory
    )
    assert result is None


def test_run_backtest_skips_missing_tier_files(tmp_path):
    # No features_*.parquet written -> every tier should be skipped, not raise.
    with pytest.warns(UserWarning):
        backtest_results, residuals_long = run_backtest(tmp_path, model_factory=_constant_model_factory, save=False)
    assert backtest_results.empty
    assert residuals_long.empty


def test_run_backtest_end_to_end(tmp_path):
    rng = np.random.default_rng(1)
    frames = []
    for crop_enc in (0, 1):
        for market_enc in (0, 1):
            s = _make_series(n=25, seed=crop_enc * 10 + market_enc)
            s["crop_enc"] = crop_enc
            s["market_enc"] = market_enc
            frames.append(s)
    feat = pd.concat(frames, ignore_index=True)

    tiers = {"tier_7_14": TIER_HORIZONS["tier_7_14"]}
    feat.to_parquet(tmp_path / "features_tier_7_14.parquet", index=False)

    backtest_results, residuals_long = run_backtest(
        tmp_path, tiers=tiers, model_factory=_constant_model_factory, save=True
    )

    assert not backtest_results.empty
    assert set(backtest_results["tier"]) == {"tier_7_14"}
    assert (tmp_path / "backtest_results.parquet").exists()
    assert (tmp_path / "backtest_residuals.parquet").exists()
    assert not residuals_long.empty


def test_summarize_by_tier_orders_by_horizon():
    backtest_results = pd.DataFrame(
        {
            "tier_label": ["30 days", "7-14 days", "60-90 days"],
            "mae_mean": [200.0, 100.0, 300.0],
            "mape_mean": [0.10, 0.05, 0.20],
            "r2_mean": [0.8, 0.9, 0.6],
        }
    )
    summary = summarize_by_tier(backtest_results)
    assert list(summary.index) == ["7-14 days", "30 days", "60-90 days"]
    assert summary.loc["7-14 days", "mae_mean_ugx"] == 100.0


def test_summarize_by_tier_empty_input_returns_empty():
    empty = pd.DataFrame()
    assert summarize_by_tier(empty).empty
