"""
Unit tests for quant/risk_metrics.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.risk_metrics import (
    add_confidence_labels,
    build_risk_report,
    compute_commodity_volatility,
    compute_risk_scores,
    confidence_label,
)


class _FakeLabelEncoder:
    """Stand-in for sklearn's LabelEncoder — avoids fitting a real one just
    to exercise the crop_enc -> commodity name mapping."""

    def __init__(self, classes):
        self._classes = list(classes)

    def inverse_transform(self, encoded):
        return np.array([self._classes[i] for i in encoded])


def test_compute_commodity_volatility_basic():
    df = pd.DataFrame(
        {
            "commodity": ["Maize"] * 4 + ["Beans"] * 4,
            "price": [100, 110, 90, 100, 500, 500, 500, 500],
        }
    )
    vol = compute_commodity_volatility(df)
    assert "cv" in vol.columns
    assert vol.loc["Beans", "cv"] == 0.0  # constant price -> zero volatility
    assert vol.loc["Maize", "cv"] > 0
    # Sorted descending by cv
    assert list(vol.index)[0] == "Maize"


def test_confidence_label_bands():
    assert confidence_label(0.0) == "High confidence"
    assert confidence_label(0.32) == "High confidence"
    assert confidence_label(0.33) == "Moderate confidence"
    assert confidence_label(0.65) == "Moderate confidence"
    assert confidence_label(0.66) == "Low confidence — treat as directional only"
    assert confidence_label(1.0) == "Low confidence — treat as directional only"


def test_confidence_label_nan():
    assert confidence_label(float("nan")) == "Unknown confidence"


def test_compute_risk_scores_higher_mape_and_cv_gives_higher_score():
    backtest_results = pd.DataFrame(
        {
            "crop_enc": [0, 1],
            "market_enc": [0, 0],
            "mape_mean": [0.05, 0.30],  # crop 1 is much worse
            "n_folds": [10, 10],
        }
    )
    volatility = pd.DataFrame({"cv": [0.1, 0.5]}, index=pd.Index(["Maize", "Beans"], name="commodity"))
    le_crop = _FakeLabelEncoder(["Maize", "Beans"])

    risk = compute_risk_scores(backtest_results, volatility, le_crop)
    beans_score = risk.loc[risk["commodity"] == "Beans", "risk_score"].iloc[0]
    maize_score = risk.loc[risk["commodity"] == "Maize", "risk_score"].iloc[0]
    assert beans_score > maize_score
    assert risk.iloc[0]["commodity"] == "Beans"  # sorted descending by risk_score


def test_compute_risk_scores_missing_volatility_does_not_crash():
    backtest_results = pd.DataFrame(
        {"crop_enc": [0], "market_enc": [0], "mape_mean": [0.1], "n_folds": [5]}
    )
    volatility = pd.DataFrame({"cv": []}, index=pd.Index([], name="commodity"))
    le_crop = _FakeLabelEncoder(["Maize"])

    risk = compute_risk_scores(backtest_results, volatility, le_crop)
    assert len(risk) == 1
    assert pd.isna(risk.iloc[0]["cv"])
    # cv_norm/mape_norm fall back to 0 in risk_score via fillna
    assert not pd.isna(risk.iloc[0]["risk_score"])


def test_add_confidence_labels():
    risk_report = pd.DataFrame({"risk_score": [0.1, 0.5, 0.9]})
    out = add_confidence_labels(risk_report)
    assert list(out["confidence_label"]) == [
        "High confidence",
        "Moderate confidence",
        "Low confidence — treat as directional only",
    ]


def test_build_risk_report_end_to_end(tmp_path):
    prices_clean = pd.DataFrame(
        {
            "commodity": ["Maize"] * 3 + ["Beans"] * 3,
            "price": [100, 120, 90, 500, 480, 520],
        }
    )
    backtest_results = pd.DataFrame(
        {"crop_enc": [0, 1], "market_enc": [0, 0], "mape_mean": [0.05, 0.15], "n_folds": [8, 8]}
    )
    le_crop = _FakeLabelEncoder(["Maize", "Beans"])

    out_path = tmp_path / "risk_scores.parquet"
    risk_report = build_risk_report(prices_clean, backtest_results, le_crop, save_path=str(out_path))

    assert "confidence_label" in risk_report.columns
    assert out_path.exists()
