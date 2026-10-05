"""
backend/app/schemas/weather.py — Pydantic Schemas for Weather API
====================================================================
Request and response models used by routers/weather.py.

Mirrors the layout of schemas/price.py:
  *Create   — incoming POST body (no id, no audit fields)
  *Response — outgoing full record (includes nested market)
  *Summary  — outgoing light record (for list endpoints)

Field names intentionally match `backend/app/models/weather.py` 1:1,
which in turn match `data/processed/weather/uganda_weather_*.csv`
(see data/README.md §2) — no renaming at any layer of this pipeline.
"""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from backend.app.models.price import UgandaRegion
from backend.app.schemas.price import MarketSummary


# =============================================================================
# CREATE SCHEMA
# =============================================================================

class WeatherReadingCreate(BaseModel):
    """
    Body for POST /weather.
    Mirrors what `scripts/fetch_weather.py` -> `scripts/load_weather.py`
    already produce per market-day row.
    """
    market_id:                  int            = Field(..., gt=0)
    reading_date:                date
    is_forecast:                 bool           = False

    elevation_m:                 Optional[float] = None
    temp_max_c:                  Optional[float] = None
    temp_min_c:                  Optional[float] = None
    rainfall_mm:                 Optional[float] = Field(None, ge=0)
    rain_mm:                     Optional[float] = Field(None, ge=0)
    precip_hours:                 Optional[float] = Field(None, ge=0, le=24)
    humidity_max_pct:            Optional[float] = Field(None, ge=0, le=100)
    humidity_min_pct:            Optional[float] = Field(None, ge=0, le=100)
    wind_speed_max_kmh:           Optional[float] = Field(None, ge=0)
    sunshine_seconds:             Optional[float] = Field(None, ge=0)
    et0_evapotranspiration_mm:    Optional[float] = None
    water_balance_mm:             Optional[float] = None
    data_source:                  Optional[str]   = Field("open-meteo.com", max_length=50)
    fetched_at:                   Optional[datetime] = None

    @model_validator(mode="after")
    def humidity_min_not_above_max(self):
        if (
            self.humidity_min_pct is not None
            and self.humidity_max_pct is not None
            and self.humidity_min_pct > self.humidity_max_pct
        ):
            raise ValueError("humidity_min_pct cannot exceed humidity_max_pct")
        return self


# =============================================================================
# RESPONSE SCHEMAS
# =============================================================================

class WeatherReadingSummary(BaseModel):
    """Light summary for list endpoints — avoids the market join."""
    id:            int
    market_id:     int
    reading_date:  date
    is_forecast:   bool
    temp_max_c:    Optional[float] = None
    temp_min_c:    Optional[float] = None
    rainfall_mm:   Optional[float] = None
    water_balance_mm: Optional[float] = None

    model_config = {"from_attributes": True}


class WeatherReadingResponse(BaseModel):
    """Full response for single-record endpoints — includes nested market."""
    id:                          int
    reading_date:                 date
    is_forecast:                  bool
    elevation_m:                  Optional[float] = None
    temp_max_c:                   Optional[float] = None
    temp_min_c:                   Optional[float] = None
    rainfall_mm:                  Optional[float] = None
    rain_mm:                      Optional[float] = None
    precip_hours:                  Optional[float] = None
    humidity_max_pct:             Optional[float] = None
    humidity_min_pct:             Optional[float] = None
    wind_speed_max_kmh:            Optional[float] = None
    sunshine_seconds:              Optional[float] = None
    et0_evapotranspiration_mm:     Optional[float] = None
    water_balance_mm:              Optional[float] = None
    data_source:                   Optional[str]   = None
    fetched_at:                    Optional[datetime] = None
    created_at:                    Optional[datetime] = None
    updated_at:                    Optional[datetime] = None

    market: MarketSummary

    model_config = {"from_attributes": True}


# =============================================================================
# PAGINATION / FILTERING
# =============================================================================

class WeatherFilterParams(BaseModel):
    market_id:    Optional[int]         = None
    region:       Optional[UgandaRegion] = None
    start_date:   Optional[date]        = None
    end_date:     Optional[date]        = None
    is_forecast:  Optional[bool]        = None
    limit:        int = 100
    offset:       int = 0


# =============================================================================
# TREND RESPONSE
# =============================================================================

class WeatherTrendPoint(BaseModel):
    period:              str    # "2026-W03" | "2026-06" | "2026-06-14"
    avg_temp_max_c:      Optional[float] = None
    avg_temp_min_c:      Optional[float] = None
    total_rainfall_mm:   Optional[float] = None
    avg_water_balance_mm: Optional[float] = None
    n_obs:               int


class WeatherTrendResponse(BaseModel):
    market_id:    Optional[int] = None
    market_name:  Optional[str] = None
    interval:     str
    period_start: date
    period_end:   date
    points:       List[WeatherTrendPoint]


# =============================================================================
# DROUGHT RISK — the leading-indicator signal fetch_weather.py's docstring
# describes ("drought → supply drop → price spike") but nothing surfaced
# until now.
# =============================================================================

class DroughtRiskItem(BaseModel):
    market_id:            int
    market_name:          str
    region:               UgandaRegion
    deficit_days:         int      # days with water_balance_mm < threshold, within lookback window
    lookback_days:         int
    avg_water_balance_mm:  Optional[float] = None
    latest_reading_date:   Optional[date]  = None
    risk_level:            str      # "LOW" | "MODERATE" | "HIGH" | "SEVERE"


class DroughtRiskResponse(BaseModel):
    generated_for_date: date
    threshold_mm:       float
    lookback_days:       int
    markets:             List[DroughtRiskItem]


# =============================================================================
# HEAVY RAIN / FLOOD ALERTS
# =============================================================================

class HeavyRainAlertItem(BaseModel):
    market_id:     int
    market_name:   str
    region:        UgandaRegion
    reading_date:  date
    rainfall_mm:   float
    is_forecast:   bool


class HeavyRainAlertResponse(BaseModel):
    threshold_mm:  float
    lookback_days:  int
    alerts:         List[HeavyRainAlertItem]
