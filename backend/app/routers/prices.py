"""
routers/prices.py — AgriGuard Price API Endpoints
==================================================
FastAPI router handling all crop price HTTP endpoints.

Endpoints defined here:
  GET    /prices                → paginated list with filters
  GET    /prices/{id}           → single price record
  POST   /prices                → submit a new price observation
  PUT    /prices/{id}           → update an existing record
  DELETE /prices/{id}           → soft-delete (sets quality=FLAGGED)
  GET    /prices/trend          → time-series trend for a crop/market
  GET    /prices/compare        → same crop across all markets side-by-side
  GET    /prices/latest         → most recent price per crop per market
  GET    /prices/alerts         → markets where prices have spiked >20%

Design principles:
  - All DB access goes through a service layer (price_service.py)
    Routers only handle HTTP — no raw SQL here
  - Dependency injection for DB session and settings
  - Consistent error messages (never expose raw DB errors to client)
  - Every endpoint has response_model so FastAPI auto-generates docs
  - Pagination on all list endpoints (MAAIF has years of price data)

Import root fixed to `backend.app.*` (previously `app.*`, a top-level
package that doesn't exist anywhere in this repo — every import below
would have raised ModuleNotFoundError). This also depended on
`backend/app/schemas/price.py`, which briefly had its own unrelated
issue: it lived alongside a flat `backend/app/schemas.py` that shadowed
it as a Python import target, making `backend.app.schemas.price`
unimportable regardless of which root you used. Both are fixed now
(see backend/app/schemas/__init__.py and backend/app/main.py's router
notes), so this router is wired into main.py below the /prices prefix.

Author: AgriGuard Team
"""

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, settings as _settings
from backend.app.database import get_db
from backend.app.models.price import DataQuality, PriceUnit, UgandaRegion
from backend.app.schemas.price import (
    CropPriceCreate,
    CropPriceResponse,
    CropPriceSummary,
    CropPriceUpdate,
    MarketComparisonResponse,
    PaginatedResponse,
    PriceFilterParams,
    PriceTrendResponse,
)
from backend.app.services.price_service import PriceService

# =============================================================================
# ROUTER SETUP
# prefix and tags are picked up by main.py when including this router
# =============================================================================

router = APIRouter(
    prefix="/prices",
    tags=["Crop Prices"],
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
    Mirrors routers/weather.py's identical wrapper.
    """
    return _settings


def get_price_service(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PriceService:
    """
    Dependency that creates a PriceService instance per request.
    FastAPI calls this automatically when a route has it as a Depends().
    The DB session is automatically closed after the request completes.
    """
    return PriceService(db=db, settings=settings)


# =============================================================================
# ENDPOINT 1: GET /prices
# List prices with flexible filtering and pagination
# =============================================================================

@router.get(
    "/",
    response_model=PaginatedResponse,
    summary="List crop prices",
    description=(
        "Returns paginated crop price observations. "
        "Filter by crop, market, region, date range, or data quality. "
        "Default: last 30 days, all crops, all markets."
    ),
)
def list_prices(
    # --- Filter parameters (all optional) ---
    crop_id: Optional[int] = Query(
        None, gt=0, description="Filter by crop ID"
    ),
    market_id: Optional[int] = Query(
        None, gt=0, description="Filter by market ID"
    ),
    region: Optional[UgandaRegion] = Query(
        None, description="Filter by Uganda region"
    ),
    start_date: Optional[date] = Query(
        None, description="Start date (YYYY-MM-DD)"
    ),
    end_date: Optional[date] = Query(
        None, description="End date (YYYY-MM-DD)"
    ),
    quality: Optional[DataQuality] = Query(
        None, description="Filter by data quality flag"
    ),
    # --- Pagination ---
    limit: int = Query(100, ge=1, le=1000, description="Records per page"),
    offset: int = Query(0, ge=0, description="Records to skip"),
    # --- Dependencies ---
    service: PriceService = Depends(get_price_service),
):
    """
    Main price listing endpoint. Powers the data table in the MAAIF dashboard.

    Default behaviour (no filters): returns the last 30 days of prices
    across all crops and markets, most recent first.

    Common usage patterns:
      - Dashboard load:  GET /prices?limit=50
      - Maize in Gulu:   GET /prices?crop_id=1&market_id=2
      - Northern region: GET /prices?region=Northern&start_date=2024-01-01
      - Export all data: GET /prices?limit=1000&offset=0 (then paginate)
    """
    # Apply default date range if not specified (last 30 days)
    if not start_date and not end_date:
        end_date = date.today()
        start_date = end_date - timedelta(days=30)

    # Validate date range
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"start_date ({start_date}) must be before end_date ({end_date})",
        )

    filters = PriceFilterParams(
        crop_id=crop_id,
        market_id=market_id,
        region=region,
        start_date=start_date,
        end_date=end_date,
        quality=quality,
        limit=limit,
        offset=offset,
    )

    prices, total = service.get_prices(filters)

    return PaginatedResponse(
        total=total,
        limit=limit,
        offset=offset,
        data=[CropPriceSummary.model_validate(p) for p in prices],
    )


# =============================================================================
# ENDPOINT 2: GET /prices/latest
# Most recent price per crop per market — the "live dashboard" view
# Must be defined BEFORE /prices/{id} to avoid FastAPI treating
# "latest" as an integer ID
# =============================================================================

@router.get(
    "/latest",
    response_model=list[CropPriceSummary],
    summary="Latest price per crop per market",
    description=(
        "Returns the single most recent price observation for each "
        "crop/market combination. This is the 'live prices' view. "
        "Optionally filter by region or crop."
    ),
)
def get_latest_prices(
    crop_id: Optional[int] = Query(None, gt=0),
    region: Optional[UgandaRegion] = Query(None),
    service: PriceService = Depends(get_price_service),
):
    """
    Powers the "Current Prices" panel on the MAAIF dashboard.

    Returns one row per (crop, market) pair — the most recent observation.
    If a market hasn't reported in 7+ days, that's flagged in the response.

    Example: "Show me today's maize price across all markets"
        GET /prices/latest?crop_id=1
    """
    prices = service.get_latest_prices(crop_id=crop_id, region=region)
    return [CropPriceSummary.model_validate(p) for p in prices]


# =============================================================================
# ENDPOINT 3: GET /prices/trend
# Time series of average prices — powers the main chart
# =============================================================================

@router.get(
    "/trend",
    response_model=PriceTrendResponse,
    summary="Price trend over time",
    description=(
        "Returns weekly-averaged price trend for a crop. "
        "Optionally scoped to a single market. "
        "If no market specified, returns national average. "
        "This is the main chart data for the MAAIF dashboard."
    ),
)
def get_price_trend(
    crop_id: int = Query(..., gt=0, description="Crop to get trend for"),
    market_id: Optional[int] = Query(
        None, description="Specific market. Omit for national average."
    ),
    start_date: date = Query(
        default_factory=lambda: date.today() - timedelta(days=180),
        description="Start date (default: 6 months ago)",
    ),
    end_date: date = Query(
        default_factory=date.today,
        description="End date (default: today)",
    ),
    interval: str = Query(
        "week",
        pattern="^(day|week|month)$",
        description="Aggregation interval: day, week, or month",
    ),
    service: PriceService = Depends(get_price_service),
):
    """
    The trend endpoint drives the primary time-series chart.

    Returns price points aggregated by day/week/month so the chart
    isn't overwhelmed by daily noise. Weekly is the recommended default
    for MAAIF presentations — smooths out market-day spikes.

    Example: Maize price trend in Gulu, last 6 months, weekly
        GET /prices/trend?crop_id=1&market_id=2&interval=week
    """
    # Validate crop exists
    crop = service.get_crop_or_404(crop_id)

    # Validate market exists if provided
    market = service.get_market_or_404(market_id) if market_id else None

    trend_data = service.get_price_trend(
        crop_id=crop_id,
        market_id=market_id,
        start_date=start_date,
        end_date=end_date,
        interval=interval,
    )

    return trend_data


# =============================================================================
# ENDPOINT 4: GET /prices/compare
# Same crop, all markets — spot regional price disparities
# =============================================================================

@router.get(
    "/compare",
    response_model=MarketComparisonResponse,
    summary="Compare crop price across markets",
    description=(
        "Returns the latest price for a single crop across all active markets. "
        "Shows which markets are cheapest/most expensive and "
        "each market's deviation from the national average. "
        "Useful for identifying arbitrage opportunities and supply gaps."
    ),
)
def compare_markets(
    crop_id: int = Query(..., gt=0, description="Crop to compare"),
    price_date: Optional[date] = Query(
        None,
        description="Date to compare (default: most recent available)",
    ),
    unit: Optional[PriceUnit] = Query(
        None,
        description="Filter to a specific unit (default: crop's default unit)",
    ),
    service: PriceService = Depends(get_price_service),
):
    """
    Cross-market price comparison — a key MAAIF use case.

    MAAIF uses this to:
    - Identify food insecure regions (high prices = low supply)
    - Track whether price shocks in one region spread to others
    - Advise traders on where to move stock

    Example: Compare maize prices across all markets today
        GET /prices/compare?crop_id=1
    """
    service.get_crop_or_404(crop_id)

    comparison = service.compare_markets(
        crop_id=crop_id,
        price_date=price_date,
        unit=unit,
    )
    return comparison


# =============================================================================
# ENDPOINT 5: GET /prices/alerts
# Markets where prices have spiked significantly — food security signal
# =============================================================================

@router.get(
    "/alerts",
    response_model=list[dict],
    summary="Price spike alerts",
    description=(
        "Returns crop/market combinations where the price has increased "
        "by more than the threshold percentage compared to the previous period. "
        "Default threshold: 20% increase. Used for food security early warning."
    ),
)
def get_price_alerts(
    threshold_pct: float = Query(
        20.0,
        ge=5.0,
        le=200.0,
        description="Spike threshold as percentage (default: 20%)",
    ),
    lookback_days: int = Query(
        30,
        ge=7,
        le=365,
        description="Compare current prices to this many days ago",
    ),
    region: Optional[UgandaRegion] = Query(None),
    service: PriceService = Depends(get_price_service),
):
    """
    Early warning endpoint for food security monitoring.

    MAAIF's mandate includes food security surveillance. This endpoint
    flags any crop/market where prices have jumped significantly —
    a signal of supply disruption, hoarding, or climate shock.

    A spike is defined as:
        current_price > previous_period_avg × (1 + threshold_pct/100)

    Example: Find markets where any crop spiked >25% in the last 2 weeks
        GET /prices/alerts?threshold_pct=25&lookback_days=14
    """
    alerts = service.get_price_alerts(
        threshold_pct=threshold_pct,
        lookback_days=lookback_days,
        region=region,
    )
    return alerts


# =============================================================================
# ENDPOINT 6: GET /prices/{id}
# Single price record — for detail view or edit
# =============================================================================

@router.get(
    "/{price_id}",
    response_model=CropPriceResponse,
    summary="Get single price record",
)
def get_price(
    price_id: int,
    service: PriceService = Depends(get_price_service),
):
    """
    Returns full detail for one price record including nested
    crop and market info, trader margin, and audit timestamps.
    """
    price = service.get_price_by_id(price_id)
    if not price:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Price record with id={price_id} not found.",
        )
    return CropPriceResponse.model_validate(price)


# =============================================================================
# ENDPOINT 7: POST /prices
# Submit a new price observation
# =============================================================================

@router.post(
    "/",
    response_model=CropPriceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new price observation",
    description=(
        "Creates a new crop price record. "
        "At least one of wholesale_price_ugx or retail_price_ugx is required. "
        "Returns 409 if a record for the same crop/market/date/unit already exists."
    ),
)
def create_price(
    price_data: CropPriceCreate,
    service: PriceService = Depends(get_price_service),
):
    """
    The data ingestion endpoint. Called by:
    - MAAIF field staff submitting survey data
    - The USSD service when a farmer reports a price
    - Admin bulk import scripts

    Validates crop and market IDs exist before inserting.
    Returns 409 Conflict if duplicate (same crop/market/date/unit).
    """
    # Validate foreign keys point to real records
    service.get_crop_or_404(price_data.crop_id)
    service.get_market_or_404(price_data.market_id)

    # Check for duplicate
    existing = service.find_duplicate(price_data)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A price record already exists for "
                f"crop_id={price_data.crop_id}, "
                f"market_id={price_data.market_id}, "
                f"date={price_data.price_date}, "
                f"unit={price_data.unit}. "
                f"Use PUT /prices/{existing.id} to update it."
            ),
        )

    new_price = service.create_price(price_data)
    return CropPriceResponse.model_validate(new_price)


# =============================================================================
# ENDPOINT 8: PUT /prices/{id}
# Update an existing price record
# =============================================================================

@router.put(
    "/{price_id}",
    response_model=CropPriceResponse,
    summary="Update a price record",
    description="Partial update — only send fields you want to change.",
)
def update_price(
    price_id: int,
    update_data: CropPriceUpdate,
    service: PriceService = Depends(get_price_service),
):
    """
    Used by MAAIF supervisors to correct field data entry errors.

    Only price values, quality flag, and notes can be updated.
    Core dimensions (crop, market, date, unit) are immutable —
    if those are wrong, delete and re-create.
    """
    price = service.get_price_by_id(price_id)
    if not price:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Price record with id={price_id} not found.",
        )

    updated = service.update_price(price, update_data)
    return CropPriceResponse.model_validate(updated)


# =============================================================================
# ENDPOINT 9: DELETE /prices/{id}
# Soft delete — flags as FLAGGED rather than removing from DB
# =============================================================================

@router.delete(
    "/{price_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Flag a price record as invalid",
    description=(
        "Soft-deletes by setting quality=FLAGGED. "
        "Records are never hard-deleted — MAAIF needs the audit trail. "
        "Flagged records are excluded from trend and comparison calculations."
    ),
)
def delete_price(
    price_id: int,
    reason: str = Query(
        ...,
        min_length=10,
        description="Reason for flagging — required for audit trail",
    ),
    service: PriceService = Depends(get_price_service),
):
    """
    Flags a price record as invalid rather than deleting it.

    Hard deletes are dangerous in a food security monitoring system —
    you can never reconstruct what the data looked like at a given time.
    Instead we flag bad records and exclude them from calculations.

    A reason is required so MAAIF supervisors have an audit trail.
    """
    price = service.get_price_by_id(price_id)
    if not price:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Price record with id={price_id} not found.",
        )

    service.flag_price(price, reason=reason)
    # 204 No Content — success, nothing to return