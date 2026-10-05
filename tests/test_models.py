"""
Unit tests for backend/app/model.py
Tests the logic layer — no trained pkl files required.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_model_not_ready_error_is_runtime():
    from backend.app.model import ModelNotReadyError
    assert issubclass(ModelNotReadyError, RuntimeError)


def test_status_returns_dict():
    from backend.app import model as m
    s = m.status()
    assert isinstance(s, dict)
    assert "price_model" in s
    assert "data_file" in s


def test_list_commodities_returns_list():
    from backend.app import model as m
    result = m.list_commodities()
    assert isinstance(result, list)


def test_list_markets_returns_list():
    from backend.app import model as m
    result = m.list_markets()
    assert isinstance(result, list)


def test_predict_price_raises_when_no_model():
    from backend.app import model as m
    from backend.app.model import ModelNotReadyError
    # Temporarily remove the model
    original = m._price_model
    m._price_model = None
    try:
        with pytest.raises(ModelNotReadyError):
            m.predict_price("Maize", "Kampala", 2025, 6)
    finally:
        m._price_model = original


def test_get_metrics_returns_dict():
    from backend.app import model as m
    assert isinstance(m.get_metrics(), dict)
