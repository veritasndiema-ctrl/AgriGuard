"""
Regression tests for the farmer-facing demo path.

They lock in the bugs that were found when preparing the MUST Interfaculty
Creativity & Innovation Challenge screening: dead default markets, a stale
"Maize" series, USSD and REST disagreeing, and README metrics that were not
reproducible.
"""

import json
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.routers.ussd import DEFAULT_MARKETS, load_data
from backend.app.services.market_resolver import (
    best_market, is_refugee_market, match_commodity, regular_markets,
)

client = TestClient(app)
df = load_data()


def ussd(text: str) -> str:
    r = client.get("/ussd/simulate", params={"text": text})
    assert r.status_code == 200
    return r.text


# ── Resolver ──────────────────────────────────────────────────────────────────

def test_maize_resolves_to_current_series_not_dead_one():
    assert match_commodity(df, "maize") == "Maize (White)"
    assert match_commodity(df, "MAIZ") == "Maize (White)"


def test_unknown_crop_is_none():
    assert match_commodity(df, "notacrop") is None


def test_regular_markets_exclude_refugee_settlements_and_stale_series():
    markets = regular_markets(df, "Maize (White)")
    assert markets, "expected at least one current regular market for maize"
    assert not any(is_refugee_market(m) for m in markets)


def test_every_default_ussd_market_has_current_data():
    """The old default list led with Kampala, which has no WFP data at all."""
    current = {m for c in ("Beans", "Maize (White)")
               for m in regular_markets(df, c)}
    assert "Kampala" not in DEFAULT_MARKETS
    assert any(m in current for m in DEFAULT_MARKETS)


# ── USSD demo path ────────────────────────────────────────────────────────────

def test_ussd_demo_path_returns_prices_not_errors():
    assert ussd("").startswith("CON")
    assert "UGX" in ussd("1*Beans*1")
    assert "Best Markets" in ussd("2*Maize")
    out = ussd("3*Maize")
    assert "Next month" in out and "UGX" in out


def test_ussd_screens_fit_the_character_limit():
    for t in ("", "1*Beans*1", "2*Maize", "3*Maize", "4", "5"):
        assert len(ussd(t)) <= 182


# ── Channels agree ────────────────────────────────────────────────────────────

def test_ussd_forecast_market_matches_rest_default_market():
    out = ussd("3*Maize")
    rest = client.get("/forecasts/Maize").json()
    assert rest["market"] in out
    assert rest["commodity"] == "Maize (White)"


def test_rest_fallback_is_explained_not_silent():
    j = client.get("/forecasts/Maize", params={"market": "Kampala"}).json()
    assert j["market"] != "Kampala"
    assert "Kampala" in (j["data_quality_note"] or "")


def test_arbitrage_and_compare_work_without_arguments():
    assert client.get("/markets/arbitrage/Beans").status_code == 200
    assert client.get("/markets/compare/Maize").status_code == 200
    assert client.get("/forecasts/compare/Maize").status_code == 200


# ── Model honesty ─────────────────────────────────────────────────────────────

def test_metrics_file_is_chronological_and_reports_a_baseline():
    p = Path(__file__).resolve().parents[1] / "ml" / "metrics.json"
    if not p.exists():
        return  # produced by scripts/train_models.py
    m = json.loads(p.read_text())["price_forecast"]
    assert "chronological" in m["split"]
    assert "naive_MAPE_pct" in m and "MAPE_pct" in m
