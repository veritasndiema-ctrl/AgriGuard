"""
AgriGuard MVP Schemas
=====================
Defines the request/response structures for the core prediction/health
endpoints in backend/app/main.py.

Purpose:
- Ensure frontend and backend speak the same language
- Prevent invalid data from reaching ML models
- Keep API responses consistent for demo reliability

This is `schemas/__init__.py`, not a flat `schemas.py`, so that
`backend.app.schemas.price` and `backend.app.schemas.weather` (the
price-record and weather-observation schemas used by
backend/app/routers/{prices,weather}.py and
backend/app/services/{price,weather}_service.py) can exist as real
submodules alongside these. A flat schemas.py and a schemas/ package
can't coexist in the same parent package -- Python resolves the name
to whichever one the import system finds first (the flat module, in
practice), which silently made backend.app.schemas.price and
.weather unimportable. Moving this file's content here instead of
leaving it as schemas.py fixes that without changing any of the
`from backend.app.schemas import (...)` call sites (they still work
against this __init__.py) or the `from backend.app.schemas.price
import (...)` / `.weather` call sites (they now resolve for real).
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# =============================================================================
# REQUEST SCHEMAS
# =============================================================================

class PricePredictionRequest(BaseModel):
    """
    Input for crop price prediction.
    """

    crop: str = Field(..., example="maize")
    region: str = Field(..., example="Mbarara")
    date: str = Field(..., example="2026-06-01")


# =============================================================================
# RESPONSE SCHEMAS
# =============================================================================

class PricePredictionResponse(BaseModel):
    """
    Output from price prediction model.
    """

    crop: str
    region: str
    date: str

    predicted_price: float
    currency: str = "UGX"

    trend: str  # "up" | "down" | "stable"
    recommendation: str  # SELL / HOLD / STORE

    confidence: float = Field(..., ge=0, le=1)

    timestamp: datetime


# =============================================================================
# GENERIC SYSTEM RESPONSE (OPTIONAL BUT USEFUL FOR /health etc.)
# =============================================================================

class HealthResponse(BaseModel):
    """
    System health status response.
    """

    status: str
    version: str
    ml_ready: bool
    timestamp: datetime
