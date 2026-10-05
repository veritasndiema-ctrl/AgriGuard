"""
backend/app/schemas/price.py — Pydantic Schemas for Crop Price API
===================================================================
Request and response models used by routers/prices.py.

Separation from the flat schemas.py allows:
  - prices.py router to import what it needs without circular deps
  - Cleaner swagger docs (schemas grouped by domain)
  - Easy extension without breaking existing endpoints

Naming:
  *Create  — incoming POST body (no id, no audit fields)
  *Update  — incoming PUT body (all fields optional)
  *Response — outgoing full record (includes nested relations)
  *Summary  — outgoing light record (for list endpoints / tables)
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator

from backend.app.models.price import DataQuality, PriceUnit, UgandaRegion


# =============================================================================
# NESTED SUMMARIES (used inside responses)
# =============================================================================

class CropSummary(BaseModel):
    id:           int
    name:         str
    local_name:   Optional[str] = None
    default_unit: PriceUnit

    model_config = {"from_attributes": True}


class MarketSummary(BaseModel):
    id:        int
    name:      str
    district:  str
    region:    UgandaRegion
    latitude:  Optional[float] = None
    longitude: Optional[float] = None

    model_config = {"from_attributes": True}


# =============================================================================
# CREATE SCHEMA
# =============================================================================

class CropPriceCreate(BaseModel):
    """
    Body for POST /prices.
    At least one of wholesale_price_ugx or retail_price_ugx is required.
    """
    crop_id:             int          = Field(..., gt=0)
    market_id:           int          = Field(..., gt=0)
    price_date:          date         = Field(..., description="Observation date (YYYY-MM-DD)")
    unit:                PriceUnit    = PriceUnit.kg
    wholesale_price_ugx: Optional[Decimal] = Field(None, ge=0)
    retail_price_ugx:    Optional[Decimal] = Field(None, ge=0)
    trader_margin_pct:   Optional[float]   = Field(None, ge=0, le=100)
    quality:             DataQuality  = DataQuality.ESTIMATED
    source:              Optional[str] = Field(None, max_length=100)
    collector_id:        Optional[str] = Field(None, max_length=50)
    notes:               Optional[str] = None

    @model_validator(mode="after")
    def at_least_one_price(self):
        if self.wholesale_price_ugx is None and self.retail_price_ugx is None:
            raise ValueError(
                "At least one of wholesale_price_ugx or retail_price_ugx is required."
            )
        return self


# =============================================================================
# UPDATE SCHEMA
# =============================================================================

class CropPriceUpdate(BaseModel):
    """
    Body for PUT /prices/{id}.
    All fields optional — send only what you want to change.
    Core dimensions (crop, market, date, unit) are intentionally excluded;
    those are immutable — delete and re-create if they're wrong.
    """
    wholesale_price_ugx: Optional[Decimal] = Field(None, ge=0)
    retail_price_ugx:    Optional[Decimal] = Field(None, ge=0)
    trader_margin_pct:   Optional[float]   = Field(None, ge=0, le=100)
    quality:             Optional[DataQuality] = None
    source:              Optional[str]     = Field(None, max_length=100)
    notes:               Optional[str]     = None
    flag_reason:         Optional[str]     = None


# =============================================================================
# RESPONSE SCHEMAS
# =============================================================================

class CropPriceSummary(BaseModel):
    """Light summary for list endpoints — avoids N+1 joins on tables."""
    id:                  int
    crop_id:             int
    market_id:           int
    price_date:          datetime
    unit:                PriceUnit
    wholesale_price_ugx: Optional[Decimal] = None
    retail_price_ugx:    Optional[Decimal] = None
    quality:             DataQuality
    source:              Optional[str] = None

    model_config = {"from_attributes": True}


class CropPriceResponse(BaseModel):
    """Full response for single-record endpoints — includes nested crop/market."""
    id:                  int
    price_date:          datetime
    unit:                PriceUnit
    wholesale_price_ugx: Optional[Decimal] = None
    retail_price_ugx:    Optional[Decimal] = None
    trader_margin_pct:   Optional[float]   = None
    quality:             DataQuality
    source:              Optional[str]     = None
    collector_id:        Optional[str]     = None
    flag_reason:         Optional[str]     = None
    notes:               Optional[str]     = None
    created_at:          Optional[datetime] = None
    updated_at:          Optional[datetime] = None

    crop:   CropSummary
    market: MarketSummary

    model_config = {"from_attributes": True}


# =============================================================================
# PAGINATION
# =============================================================================

class PaginatedResponse(BaseModel):
    total:  int
    limit:  int
    offset: int
    data:   List[Any]


# =============================================================================
# FILTER PARAMS (passed between router and service)
# =============================================================================

class PriceFilterParams(BaseModel):
    crop_id:    Optional[int]         = None
    market_id:  Optional[int]         = None
    region:     Optional[UgandaRegion] = None
    start_date: Optional[date]        = None
    end_date:   Optional[date]        = None
    quality:    Optional[DataQuality] = None
    limit:      int = 100
    offset:     int = 0


# =============================================================================
# TREND / COMPARISON RESPONSES
# =============================================================================

class TrendPoint(BaseModel):
    period:    str           # "2024-W03" | "2024-01" | "2024-01-15"
    avg_price: Decimal
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None
    n_obs:     int


class PriceTrendResponse(BaseModel):
    crop_id:    int
    crop_name:  str
    market_id:  Optional[int]  = None
    market_name: Optional[str] = None
    interval:   str
    unit:       PriceUnit
    points:     List[TrendPoint]


class MarketPricePoint(BaseModel):
    market_id:   int
    market_name: str
    region:      UgandaRegion
    price:       Optional[Decimal]
    unit:        PriceUnit
    pct_vs_avg:  Optional[float] = None   # deviation from national average


class MarketComparisonResponse(BaseModel):
    crop_id:      int
    crop_name:    str
    price_date:   Optional[date]
    national_avg: Optional[Decimal]
    markets:      List[MarketPricePoint]