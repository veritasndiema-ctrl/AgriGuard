"""
backend/app/models/schemas.py — Price-domain Pydantic schemas
=============================================================
Used by the (not-yet-wired) prices router and price_service.
Kept separate from the top-level backend/app/schemas.py MVP schemas.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class PriceObservationBase(BaseModel):
    commodity: str = Field(..., min_length=1, max_length=120)
    market: str = Field(..., min_length=1, max_length=120)
    price: float = Field(..., gt=0)
    currency: str = Field(default="UGX", max_length=8)
    unit: str = Field(default="KG", max_length=32)
    price_type: str = Field(default="Retail", max_length=32)
    observed_on: date
    region: Optional[str] = Field(default=None, max_length=120)
    source: Optional[str] = Field(default="WFP", max_length=64)


class PriceObservationCreate(PriceObservationBase):
    pass


class PriceObservationUpdate(BaseModel):
    price: Optional[float] = Field(default=None, gt=0)
    currency: Optional[str] = Field(default=None, max_length=8)
    unit: Optional[str] = Field(default=None, max_length=32)
    price_type: Optional[str] = Field(default=None, max_length=32)
    region: Optional[str] = Field(default=None, max_length=120)
    source: Optional[str] = Field(default=None, max_length=64)


class PriceObservationRead(PriceObservationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None


class PriceListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[PriceObservationRead]


class PriceStatsResponse(BaseModel):
    commodity: str
    market: Optional[str] = None
    n_observations: int
    min_price: float
    max_price: float
    mean_price: float
    median_price: float
    currency: str = "UGX"
    first_date: date
    last_date: date
