"""
AgriGuard — backend API tests.
Run from repo root:  pytest tests/ -v

This file previously tested a different, older API shape (commodity/market/
year/month payloads, /api/v1/forecasts/* routes, a "models" key on /health)
that main.py and the routers no longer implement — every test in the old
version was failing against the current, working API. Rewritten against
what backend/app/main.py, routers/forecasts.py, and routers/markets.py
actually expose today.

/api/v1/predict tests mock backend.app.model.predict_price so they don't
depend on a trained ml/models/*.pkl existing. The /forecasts and /markets
tests hit the real endpoints against the committed
data/raw/wfp_food_prices_uga.csv — that file is small and always present,
so there's nothing worth mocking there.
"""

import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

# ensure repo root is on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.main import app

client = TestClient(app)


# ── fixtures ───────────────────────────────────────────────────────────────

# Shape returned by backend.app.model.predict_price() — see model.py.
MOCK_PREDICT_RESULT = {
    "commodity": "Maize",
    "market": "Kampala",
    "year": 2026,
    "month": 8,
    "predicted_price_ugx": 1350.0,
    "lower_bound_ugx": 1215.0,
    "upper_bound_ugx": 1485.0,
    "currency": "UGX",
}


# ── health & root ──────────────────────────────────────────────────────────

def test_root():
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert "message" in body
    assert "docs" in body


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "ml_ready" in body
    assert "validator_ready" in body


# ── /api/v1/predict ────────────────────────────────────────────────────────
# Schema is PricePredictionRequest{crop, region, date} — see backend/app/schemas/__init__.py.

def test_predict_success():
    # See test_predict_model_not_ready for why this patches
    # backend.app.main.predict_price rather than backend.app.model.predict_price.
    with patch("backend.app.main.predict_price", return_value=MOCK_PREDICT_RESULT):
        r = client.post("/api/v1/predict", json={
            "crop": "Maize",
            # Mbarara, not Kampala: validate_input() checks region against
            # model.list_markets(), which reads the real committed CSV —
            # WFP's current export has no market literally named "Kampala"
            # (its Kampala coverage is under "Owino"). Mbarara is present in
            # every WFP Uganda snapshot this repo has shipped with.
            "region": "Mbarara",
            "date": "2026-08-01",
        })
    assert r.status_code == 200
    body = r.json()
    assert body["crop"] == "Maize"
    assert body["region"] == "Mbarara"
    assert body["currency"] == "UGX"
    assert "predicted_price" in body
    assert body["recommendation"] in {"SELL", "HOLD", "STORE"}


def test_predict_missing_field_is_422():
    # crop/region/date are all required — Pydantic itself rejects a
    # missing field before validate_input() ever runs.
    r = client.post("/api/v1/predict", json={"region": "Kampala", "date": "2026-08-01"})
    assert r.status_code == 422


def test_predict_bad_date_format_is_400():
    # A malformed (but present) date is a validate_input() failure, not a
    # Pydantic one — crop/region/date are plain strings in the schema.
    r = client.post("/api/v1/predict", json={
        "crop": "Maize",
        "region": "Kampala",
        "date": "01-08-2026",  # wrong order, not YYYY-MM-DD
    })
    assert r.status_code == 400
    assert "details" in r.json()


def test_predict_model_not_ready():
    from backend.app.model import ModelNotReadyError
    # main.py does `from backend.app.model import predict_price` — that
    # binds the name into backend.app.main's own namespace, so patching
    # backend.app.model.predict_price (the origin) doesn't touch what
    # main.py actually calls. Patch it where it's used instead.
    with patch("backend.app.main.predict_price",
               side_effect=ModelNotReadyError("Model not loaded")):
        r = client.post("/api/v1/predict", json={
            "crop": "Maize",
            "region": "Mbarara",  # see test_predict_success for why not "Kampala"
            "date": "2026-08-01",
        })
    assert r.status_code == 503


# ── /forecasts/* (no /api/v1 prefix — see routers/forecasts.py) ────────────

def test_forecast_commodities():
    r = client.get("/forecasts/commodities")
    assert r.status_code == 200
    body = r.json()
    assert "commodities" in body
    assert "markets" in body
    assert "Maize" in body["commodities"]


def test_forecast_unknown_commodity_is_404():
    r = client.get("/forecasts/NotACrop")
    assert r.status_code == 404


def test_forecast_history():
    r = client.get("/forecasts/history/Maize", params={"market": "Kampala"})
    assert r.status_code == 200
    assert "history" in r.json()


# ── /markets/* (no /api/v1 prefix — see routers/markets.py) ────────────────

def test_market_summary():
    r = client.get("/markets/summary/Maize")
    assert r.status_code == 200
    body = r.json()
    assert body["commodity"].startswith("Maize")  # resolves to the current "Maize (White)" series
    assert "best_market_to_sell" in body
    assert "national_avg_price" in body


def test_national_summary():
    r = client.get("/markets/national-summary")
    assert r.status_code == 200
    assert "commodities" in r.json()


def test_top_movers():
    r = client.get("/markets/movers")
    assert r.status_code == 200
    body = r.json()
    assert "gainers" in body
    assert "losers" in body
