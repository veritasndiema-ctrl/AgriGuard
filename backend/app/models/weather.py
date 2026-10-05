"""
backend/app/models/weather.py — AgriGuard Weather Database Model
==================================================================
SQLAlchemy ORM model for daily weather readings per market.

Why this file exists:
  `scripts/fetch_weather.py` has been pulling daily weather (temperature,
  rainfall, humidity, wind, evapotranspiration, water balance) from
  Open-Meteo for months, writing it to `data/raw/weather/*.json` and
  `data/processed/weather/*.csv` — but nothing ever loaded it into the
  database. Two files already assumed a `WeatherReading` model existed
  and imported it (`backend/app/services/price_service.py`,
  `ml/training/train_forecast.py`) — this is that model, built to match
  exactly what they expect.

Table:
  weather_readings — one row per market × date × historical-or-forecast

Design:
  - `market_id` FKs to the existing `markets` table (backend/app/models/price.py)
    rather than storing a bare market name string, so weather joins cleanly
    with `crop_prices` on `market_id` — no string-matching needed.
  - `is_forecast` distinguishes a 16-day-ahead Open-Meteo forecast row from
    a historical (already-happened) reading. `ml/training/train_forecast.py`
    filters `is_forecast == False` when building training features — never
    train on forecast data, only serve it for live "what's coming" context.
  - Column names mirror `data/processed/weather/uganda_weather_*.csv`
    exactly (see `data/README.md` §2) so `scripts/load_weather.py` can
    bulk-insert straight from the processed CSVs without a rename step.
  - `water_balance_mm` (rainfall − evapotranspiration) is Open-Meteo's raw
    signal for crop stress: sustained negative values across a market are
    the leading drought indicator that (per `fetch_weather.py`'s docstring)
    is meant to predict price spikes 2-3 weeks out. Read by
    `WeatherService.get_drought_risk()`.
  - Unique constraint on (market_id, reading_date, is_forecast) makes
    re-running `fetch_weather.py` + `load_weather.py` idempotent — a
    re-fetched forecast for a date already on file updates in place rather
    than duplicating rows.

Author: AgriGuard Team
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from backend.app.database import Base


# =============================================================================
# WEATHER READINGS
# =============================================================================

class WeatherReading(Base):
    """
    One daily weather observation (or forecast) for one market.

    Sourced from Open-Meteo via `scripts/fetch_weather.py` and loaded via
    `scripts/load_weather.py`. See `data/README.md` §2 for the upstream
    schema and market-coverage caveats (Fort Portal and Soroti aren't
    covered by the weather fetcher yet, so `market_id` may have no
    weather rows for those two even though they exist in `markets`).
    """
    __tablename__ = "weather_readings"

    __table_args__ = (
        UniqueConstraint(
            "market_id", "reading_date", "is_forecast",
            name="uq_market_date_forecast_flag",
        ),
    )

    id                        = Column(Integer, primary_key=True, index=True)

    # Foreign key — joins weather to the same markets crop_prices uses
    market_id                 = Column(Integer, ForeignKey("markets.id"), nullable=False, index=True)

    # What day this reading is FOR (not when it was fetched — see fetched_at)
    reading_date               = Column(Date, nullable=False, index=True)

    # True = a still-in-the-future Open-Meteo forecast row (stale within
    # weeks, never used for training). False = an already-happened
    # historical reading (safe to train on).
    is_forecast                = Column(Boolean, default=False, nullable=False, index=True)

    # Location snapshot — elevation isn't stored on Market, useful for
    # microclimate sanity-checking without a join.
    elevation_m                = Column(Float, nullable=True)

    # Temperature
    temp_max_c                 = Column(Float, nullable=True)
    temp_min_c                 = Column(Float, nullable=True)

    # Rainfall
    rainfall_mm                = Column(Float, nullable=True)   # precipitation_sum (rain + other precip)
    rain_mm                    = Column(Float, nullable=True)   # rain_sum (rain component only)
    precip_hours                = Column(Float, nullable=True)

    # Humidity
    humidity_max_pct           = Column(Float, nullable=True)
    humidity_min_pct           = Column(Float, nullable=True)

    # Wind & sun
    wind_speed_max_kmh          = Column(Float, nullable=True)
    sunshine_seconds            = Column(Float, nullable=True)

    # Crop-stress indicators — the features `train_forecast.py` actually
    # trains on (lagged 7/14/21 days, plus a 30-day drought-day count)
    et0_evapotranspiration_mm   = Column(Float, nullable=True)
    water_balance_mm            = Column(Float, nullable=True)   # rainfall_mm - et0_evapotranspiration_mm

    # Provenance
    data_source                 = Column(String(50), nullable=True, default="open-meteo.com")
    fetched_at                  = Column(DateTime(timezone=True), nullable=True)  # when Open-Meteo returned this row

    # Audit
    created_at                  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at                  = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationship — requires Market.weather_readings on the other side
    # (see backend/app/models/price.py)
    market                       = relationship("Market", back_populates="weather_readings")

    def __repr__(self):
        kind = "forecast" if self.is_forecast else "historical"
        return (
            f"<WeatherReading id={self.id} market_id={self.market_id} "
            f"date={self.reading_date} ({kind}) rain={self.rainfall_mm}mm>"
        )

    # =========================================================================
    # CONVENIENCE
    # =========================================================================

    @property
    def is_drought_stress(self) -> bool:
        """
        True when water balance is meaningfully negative (deficit > 3mm) —
        the threshold `WeatherService.get_drought_risk()` uses by default.
        Exposed here too so ad-hoc queries/notebooks don't have to
        re-hardcode the -3.0 constant.
        """
        return self.water_balance_mm is not None and self.water_balance_mm < -3.0

    @property
    def is_heavy_rain(self) -> bool:
        """True when rainfall exceeds 50mm/day — flooding-risk threshold used
        in `fetch_weather.py`'s `generate_summary_report()`."""
        return self.rainfall_mm is not None and self.rainfall_mm > 50.0
