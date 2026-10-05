"""
routers/weather.py — AgriGuard Weather API Endpoints
=======================================================
FastAPI router handling all weather HTTP endpoints.

Endpoints defined here:
  GET  /weather                    → paginated list with filters
  GET  /weather/{id}               → single reading
  POST /weather                    → ingest one reading (upsert)
  GET  /weather/latest             → most recent historical reading per market
  GET  /weather/trend              → aggregated trend (temp/rainfall/water balance)
  GET  /weather/{market_id}/forecast → 16-day-ahead forecast for one market
  GET  /weather/drought-risk       → drought-stress scoring per market
  GET  /weather/alerts/heavy-rain  → heavy-rain / flood-risk alerts
  GET  /weather/sync/status        → when Open-Meteo was last auto-synced
  POST /weather/sync               → trigger an on-demand sync now

Design principles (matching routers/prices.py):
  - All DB access goes through the service layer (weather_service.py)
  - Dependency injection for DB session and settings
  - Every endpoint has response_model so FastAPI auto-generates docs
  - Pagination on the list endpoint

Unlike routers/prices.py used to be, this router only ever imported via
the working `backend.app.*` root — see backend/app/main.py for the fuller
history (prices.py's own import root has since been fixed too, and both
are wired in now).

Author: AgriGuard Team
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, settings as _settings
from backend.app.database import get_db
from backend.app.models.price import UgandaRegion
from backend.app.schemas.weather import (
    DroughtRiskResponse,
    HeavyRainAlertResponse,
    WeatherFilterParams,
    WeatherReadingCreate,
    WeatherReadingResponse,
    WeatherReadingSummary,
    WeatherTrendResponse,
)
from backend.app.services import weather_sync
from backend.app.services.weather_service import WeatherService

# =============================================================================
# ROUTER SETUP
# =============================================================================

router = APIRouter(
    prefix="/weather",
    tags=["Weather"],
    responses={
        404: {"description": "Record not found"},
        422: {"description": "Validation error — check request body"},
    },
)


# =============================================================================
# DEPENDENCIES
# =============================================================================

def get_settings() -> Settings:
    """
    core/config.py exposes a module-level `settings` singleton rather than
    a factory function — this wraps it so the endpoint signatures below can
    still use FastAPI's `Depends(get_settings)` pattern for testability
    (a test can override this dependency to inject different settings).
    """
    return _settings


def get_weather_service(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WeatherService:
    return WeatherService(db=db, settings=settings)


# =============================================================================
# LIST / DETAIL / CREATE
# =============================================================================

@router.get("", response_model=dict)
def list_weather_readings(
    market_id: Optional[int] = None,
    region: Optional[UgandaRegion] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    is_forecast: Optional[bool] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    service: WeatherService = Depends(get_weather_service),
):
    """Paginated, filterable list of weather readings."""
    filters = WeatherFilterParams(
        market_id=market_id,
        region=region,
        start_date=start_date,
        end_date=end_date,
        is_forecast=is_forecast,
        limit=limit,
        offset=offset,
    )
    readings, total = service.get_readings(filters)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "data": [WeatherReadingSummary.model_validate(r) for r in readings],
    }


@router.get("/latest", response_model=list[WeatherReadingResponse])
def get_latest_weather(
    region: Optional[UgandaRegion] = None,
    service: WeatherService = Depends(get_weather_service),
):
    """Most recent historical reading per market — the dashboard's default view."""
    return service.get_latest_readings(region=region, is_forecast=False)


@router.get("/trend", response_model=WeatherTrendResponse)
def get_weather_trend(
    start_date: date,
    end_date: date,
    market_id: Optional[int] = None,
    interval: str = Query("week", pattern="^(day|week|month)$"),
    service: WeatherService = Depends(get_weather_service),
):
    """
    Aggregated weather trend (avg temp, total rainfall, avg water balance)
    over time. Omit `market_id` for a national-level trend.
    """
    return service.get_weather_trend(
        market_id=market_id,
        start_date=start_date,
        end_date=end_date,
        interval=interval,
    )


@router.get("/{market_id}/forecast", response_model=list[WeatherReadingResponse])
def get_market_forecast(
    market_id: int,
    days: int = Query(16, ge=1, le=16),
    service: WeatherService = Depends(get_weather_service),
):
    """16-day-ahead Open-Meteo forecast for one market, soonest first."""
    return service.get_forecast(market_id=market_id, days=days)


@router.get("/{reading_id}", response_model=WeatherReadingResponse)
def get_weather_reading(
    reading_id: int,
    service: WeatherService = Depends(get_weather_service),
):
    """Single weather reading by id."""
    reading = service.get_reading_by_id(reading_id)
    if not reading:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Weather reading with id={reading_id} not found.",
        )
    return reading


@router.post("", response_model=WeatherReadingResponse, status_code=201)
def ingest_weather_reading(
    payload: WeatherReadingCreate,
    service: WeatherService = Depends(get_weather_service),
):
    """
    Ingest (insert or update) a single weather reading. Idempotent on
    (market_id, reading_date, is_forecast) — re-posting the same day just
    updates it. `scripts/load_weather.py` uses the service's `bulk_upsert()`
    directly rather than calling this endpoint row-by-row for bulk loads.
    """
    return service.upsert_reading(payload)


# =============================================================================
# ANALYTICS
# =============================================================================

@router.get("/analytics/drought-risk", response_model=DroughtRiskResponse)
def get_drought_risk(
    region: Optional[UgandaRegion] = None,
    lookback_days: int = Query(30, ge=1, le=365),
    deficit_threshold_mm: float = Query(-3.0, le=0),
    service: WeatherService = Depends(get_weather_service),
):
    """
    Drought-stress score per market over the lookback window — the leading
    indicator `fetch_weather.py`'s docstring describes ("drought → supply
    drop → price spike"), finally surfaced as a queryable signal. Sorted
    worst-first (SEVERE > HIGH > MODERATE > LOW).
    """
    return service.get_drought_risk(
        region=region,
        lookback_days=lookback_days,
        deficit_threshold_mm=deficit_threshold_mm,
    )


@router.get("/alerts/heavy-rain", response_model=HeavyRainAlertResponse)
def get_heavy_rain_alerts(
    threshold_mm: float = Query(50.0, gt=0),
    lookback_days: int = Query(7, ge=1, le=90),
    include_forecast: bool = True,
    service: WeatherService = Depends(get_weather_service),
):
    """
    Recent or upcoming (if `include_forecast=true`) heavy-rain days above
    `threshold_mm` — flooding-risk early warning, sorted heaviest-first.
    """
    return service.get_heavy_rain_alerts(
        threshold_mm=threshold_mm,
        lookback_days=lookback_days,
        include_forecast=include_forecast,
    )


# =============================================================================
# LIVE SYNC — mirrors routers/forecasts.py's /sync/status + /sync for WFP
# =============================================================================

class WeatherSyncStatusResponse(BaseModel):
    """Status of the background Open-Meteo weather sync (see services/weather_sync.py)."""
    synced_at: Optional[str] = None       # When we last pulled fresh data (UTC ISO-8601)
    markets_synced: Optional[int] = None  # How many markets succeeded on that run
    markets_total: Optional[int] = None
    lookback_days: Optional[int] = None
    per_market: Optional[dict] = None     # Per-market {synced, created, updated, dropped_invalid, error}


@router.get("/sync/status", response_model=WeatherSyncStatusResponse)
def get_weather_sync_status():
    """
    When was weather last synced from Open-Meteo, and what did that run
    cover. No network call — reads the local sync-state file.
    """
    return WeatherSyncStatusResponse(**weather_sync.last_sync_info())


@router.post("/sync", response_model=WeatherSyncStatusResponse)
def trigger_weather_sync():
    """
    On-demand version of the background weather sync job (see
    services/weather_sync.py and its scheduled run in main.py's startup).
    Seeds `markets` if needed, then re-pulls trailing history + 16-day
    forecast for every covered market.
    """
    weather_sync.sync_if_updated()
    return WeatherSyncStatusResponse(**weather_sync.last_sync_info())
