"""
Unit tests for quant/model_selection.py

Prophet-dependent paths (backtest_prophet, train_selected_model("prophet", ...))
are skipped if prophet isn't installed in the current environment, since it's
a heavy optional dependency (see requirements.txt) — the selection logic
itself (`select_model_per_group`) is tested against precomputed MAPE tables
so it doesn't require Prophet to actually run.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.model_selection import PROPHET, XGBOOST, select_model_per_group, train_selected_model

def _has_prophet() -> bool:
    try:
        import prophet  # noqa: F401

        return True
    except ImportError:
        return False


def test_select_model_per_group_chooses_lower_mape():
    backtest_results = pd.DataFrame(
        {
            "crop_enc": [0, 1],
            "market_enc": [0, 0],
            "tier": ["tier_7_14", "tier_7_14"],
            "mape_mean": [0.10, 0.40],  # crop 0: xgboost good; crop 1: xgboost bad
        }
    )
    # Monkeypatch feature_frames_by_tier lookup path by calling with an empty
    # dict so no Prophet backtest actually runs, then verify the fallback
    # behavior driven purely by xgb_mape (no prophet_mape available -> xgboost wins).
    result = select_model_per_group(backtest_results, feature_frames_by_tier={}, tiers={"tier_7_14": {"horizon_steps": 2, "label": "7-14 days"}})
    assert set(result["chosen_model"]) == {XGBOOST}


def test_select_model_per_group_prefers_prophet_when_better(monkeypatch):
    backtest_results = pd.DataFrame(
        {"crop_enc": [0], "market_enc": [0], "tier": ["tier_7_14"], "mape_mean": [0.30]}
    )
    feat = pd.DataFrame(
        {
            "crop_enc": [0] * 5,
            "market_enc": [0] * 5,
            "date": pd.date_range("2024-01-01", periods=5, freq="W"),
            "price": [100, 110, 90, 105, 95],
        }
    )

    def fake_backtest_prophet(group, horizon_steps, min_train=12):
        return 0.05  # deliberately better than xgboost's 0.30

    monkeypatch.setattr("quant.model_selection.backtest_prophet", fake_backtest_prophet)

    result = select_model_per_group(
        backtest_results,
        feature_frames_by_tier={"tier_7_14": feat},
        tiers={"tier_7_14": {"horizon_steps": 2, "label": "7-14 days"}},
        min_train=1,
    )
    assert result.loc[0, "chosen_model"] == PROPHET


def test_select_model_per_group_falls_back_to_prophet_when_xgb_missing(monkeypatch):
    # No xgboost row for this group at all
    backtest_results = pd.DataFrame({"crop_enc": [], "market_enc": [], "tier": [], "mape_mean": []})
    feat = pd.DataFrame(
        {
            "crop_enc": [0] * 5,
            "market_enc": [0] * 5,
            "date": pd.date_range("2024-01-01", periods=5, freq="W"),
            "price": [100, 110, 90, 105, 95],
        }
    )

    monkeypatch.setattr("quant.model_selection.backtest_prophet", lambda group, horizon_steps, min_train=12: 0.20)

    result = select_model_per_group(
        backtest_results,
        feature_frames_by_tier={"tier_7_14": feat},
        tiers={"tier_7_14": {"horizon_steps": 2, "label": "7-14 days"}},
        min_train=1,
    )
    assert result.loc[0, "chosen_model"] == PROPHET


def test_train_selected_model_unknown_type_raises():
    with pytest.raises(ValueError):
        train_selected_model("not-a-real-model", pd.DataFrame({"price": [1, 2, 3]}))


def test_train_selected_model_xgboost_requires_feature_cols():
    group = pd.DataFrame({"price": [100, 110, 120], "month": [1, 2, 3]})
    with pytest.raises(ValueError):
        train_selected_model(XGBOOST, group, feature_cols=None)


def test_train_selected_model_xgboost_fits():
    pytest.importorskip("xgboost")
    group = pd.DataFrame({"price": [100, 110, 120, 130], "month": [1, 2, 3, 4]})
    model = train_selected_model(XGBOOST, group, feature_cols=["month"])
    preds = model.predict(group[["month"]])
    assert len(preds) == 4


@pytest.mark.skipif(not _has_prophet(), reason="prophet not installed")
def test_backtest_prophet_too_short_returns_none():
    from quant.model_selection import backtest_prophet

    group = pd.DataFrame(
        {"date": pd.date_range("2024-01-01", periods=5, freq="W"), "price": [100, 110, 90, 105, 95]}
    )
    assert backtest_prophet(group, horizon_steps=4, min_train=12) is None
