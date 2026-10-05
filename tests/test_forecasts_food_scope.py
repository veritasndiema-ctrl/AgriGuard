"""
tests/test_forecasts_food_scope.py — food-only scope, sparse-data fallback,
and quant_bridge wiring.

Split out from tests/test_api.py because these exercise
routers/forecasts.py's internals directly (not just the HTTP surface) and
services/quant_bridge.py, which test_api.py never touches.
"""

import numpy as np
import pandas as pd
import pytest

from backend.app.routers import forecasts as f
from backend.app.services import quant_bridge


# =============================================================================
# Food-only scope
# =============================================================================

def test_is_food_commodity_blocklist():
    assert f._is_food_commodity("Maize") is True
    assert f._is_food_commodity("Beans") is True
    assert f._is_food_commodity("Batteries") is False
    assert f._is_food_commodity("Charcoal") is False
    assert f._is_food_commodity("Exchange Rate (Usd/Lcu)") is False


def test_load_wfp_csv_excludes_non_food_categories():
    df = f._load_wfp_csv()
    assert len(df) > 0
    commodities = set(df["commodity"].unique())
    # Known non-food items confirmed present in the raw WFP CSV's
    # "non-food" category (see category value_counts on the raw file).
    for non_food in ("Batteries", "Charcoal", "Basin", "Hoe", "Firewood"):
        assert non_food not in commodities
    assert "Maize" in commodities


# =============================================================================
# Sparse-data fallback
# =============================================================================

def _series(n, start_price=1000.0):
    dates = pd.date_range("2024-01-01", periods=n, freq="30D")
    prices = start_price + np.cumsum(np.random.default_rng(0).normal(0, 5, n))
    return pd.DataFrame({"date": dates, "price": prices})


def test_naive_forecast_holds_price_flat_and_widens_band():
    train = _series(6)
    horizon = 10
    fc = f.naive_forecast(train, horizon)
    assert len(fc) == horizon
    # Flat point forecast
    assert (fc["yhat"] == fc["yhat"].iloc[0]).all()
    # Interval widens over the horizon
    first_width = fc["yhat_upper"].iloc[0] - fc["yhat_lower"].iloc[0]
    last_width = fc["yhat_upper"].iloc[-1] - fc["yhat_lower"].iloc[-1]
    assert last_width > first_width


def test_build_forecast_response_sparse_uses_naive_and_flags_limited():
    train = _series(6)  # between ABSOLUTE_MIN_OBSERVATIONS(3) and MIN_OBSERVATIONS(10)
    resp = f._build_forecast_response("Maize", "Kampala", train, train, horizon=10)
    assert resp.model_used == "naive"
    assert resp.data_quality == "limited"
    assert resp.data_quality_note is not None


def test_build_forecast_response_sufficient_data_not_flagged_limited():
    train = _series(60)
    resp = f._build_forecast_response("Maize", "Kampala", train, train, horizon=14)
    assert resp.model_used != "naive"
    assert resp.data_quality == "sufficient"
    assert resp.data_quality_note is None


# =============================================================================
# quant_bridge — fails safe, never raises
# =============================================================================

def test_quant_confidence_returns_none_on_too_little_data():
    train = _series(5)
    assert quant_bridge.quant_confidence(train, horizon_days=14) is None


def test_quant_confidence_produces_a_real_result_on_enough_data():
    train = _series(120)
    result = quant_bridge.quant_confidence(train, horizon_days=14)
    assert result is not None
    assert result.n_folds > 0
    assert result.halfwidth >= 0
    assert 0.0 <= result.confidence <= 1.0


def test_apply_halfwidth_never_returns_negative_lower_bound():
    lower, upper = quant_bridge.apply_halfwidth(10.0, 50.0)
    assert lower == 0.0
    assert upper == 60.0
