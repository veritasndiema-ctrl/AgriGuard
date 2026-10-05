"""
backend/app/models/price.py — AgriGuard Database Models
=========================================================
SQLAlchemy ORM models for the crop price intelligence system.

Tables:
  crops       — canonical crop reference (maize, beans, etc.)
  markets     — physical market locations with GPS + region
  crop_prices — one price observation per crop × market × date × unit

Enums:
  UgandaRegion  — the 4 administrative regions
  PriceUnit     — kg, 90kg_bag, bunch, litre, tonne
  DataQuality   — VERIFIED | ESTIMATED | FLAGGED

Design:
  - Soft-delete via DataQuality.FLAGGED (never hard-delete price history)
  - trader_margin_pct stored alongside prices (MAAIF policy requirement)
  - GPS on markets enables future satellite / weather joins
  - All monetary columns use Numeric(12,2) — never Float for money
"""

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from backend.app.database import Base


# =============================================================================
# ENUMS
# =============================================================================

class UgandaRegion(str, enum.Enum):
    Central  = "Central"
    Eastern  = "Eastern"
    Northern = "Northern"
    Western  = "Western"


class PriceUnit(str, enum.Enum):
    kg         = "kg"
    bag_90kg   = "90kg_bag"
    bunch      = "bunch"
    litre      = "litre"
    tonne      = "tonne"


class DataQuality(str, enum.Enum):
    VERIFIED  = "VERIFIED"
    ESTIMATED = "ESTIMATED"
    FLAGGED   = "FLAGGED"


# =============================================================================
# CROPS
# =============================================================================

class Crop(Base):
    """
    Canonical crop reference table.
    Keeps crop metadata separate from price data so we can add
    new crops without migrating the price table.
    """
    __tablename__ = "crops"

    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String(100), unique=True, nullable=False, index=True)
    local_name    = Column(String(100), nullable=True)   # e.g. "Emponko" for sorghum
    default_unit  = Column(Enum(PriceUnit), default=PriceUnit.kg, nullable=False)
    category      = Column(String(50), nullable=True)    # cereal | legume | root | beverage
    is_active     = Column(Boolean, default=True, nullable=False)
    notes         = Column(Text, nullable=True)

    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    prices        = relationship("CropPrice", back_populates="crop", lazy="dynamic")

    def __repr__(self):
        return f"<Crop id={self.id} name={self.name}>"


# =============================================================================
# MARKETS
# =============================================================================

class Market(Base):
    """
    Physical market locations in Uganda.
    GPS coordinates allow future joins with weather and satellite data.
    """
    __tablename__ = "markets"

    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String(150), unique=True, nullable=False, index=True)
    district      = Column(String(100), nullable=False)
    region        = Column(Enum(UgandaRegion), nullable=False, index=True)
    latitude      = Column(Float, nullable=True)
    longitude     = Column(Float, nullable=True)
    is_active     = Column(Boolean, default=True, nullable=False)
    notes         = Column(Text, nullable=True)

    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    prices        = relationship("CropPrice", back_populates="market", lazy="dynamic")
    # Weather readings for this market (backend/app/models/weather.py).
    # Import backend.app.models.weather somewhere before create_tables() /
    # any query touches this — see backend/app/models/__init__.py — or
    # SQLAlchemy won't have resolved the "WeatherReading" string yet.
    weather_readings = relationship("WeatherReading", back_populates="market", lazy="dynamic")

    def __repr__(self):
        return f"<Market id={self.id} name={self.name} region={self.region}>"


# =============================================================================
# CROP PRICES
# =============================================================================

class CropPrice(Base):
    """
    Core price observation table.
    One row = one crop × market × date × unit observation.

    Uniqueness constraint prevents duplicate submissions for the same
    crop/market/date/unit combination (field officers sometimes re-submit).
    Use PUT /prices/{id} to correct an existing record instead.

    Soft-delete: set quality=FLAGGED with a reason rather than DELETE.
    MAAIF audit requirements mean we never lose price history.
    """
    __tablename__ = "crop_prices"

    __table_args__ = (
        UniqueConstraint(
            "crop_id", "market_id", "price_date", "unit",
            name="uq_crop_market_date_unit"
        ),
    )

    id                  = Column(Integer, primary_key=True, index=True)

    # Foreign keys
    crop_id             = Column(Integer, ForeignKey("crops.id"),   nullable=False, index=True)
    market_id           = Column(Integer, ForeignKey("markets.id"), nullable=False, index=True)

    # Price data
    price_date          = Column(DateTime(timezone=False), nullable=False, index=True)
    wholesale_price_ugx = Column(Numeric(12, 2), nullable=True)
    retail_price_ugx    = Column(Numeric(12, 2), nullable=True)
    unit                = Column(Enum(PriceUnit),  nullable=False, default=PriceUnit.kg)
    trader_margin_pct   = Column(Float, nullable=True)   # % markup from wholesale → retail

    # Data provenance
    quality             = Column(Enum(DataQuality), default=DataQuality.ESTIMATED, nullable=False)
    source              = Column(String(100), nullable=True)   # "MAAIF field survey" | "WFP" | "USSD"
    collector_id        = Column(String(50),  nullable=True)   # field officer ID
    flag_reason         = Column(Text, nullable=True)          # populated when quality=FLAGGED
    notes               = Column(Text, nullable=True)

    # Audit
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    crop                = relationship("Crop",   back_populates="prices")
    market              = relationship("Market", back_populates="prices")

    def __repr__(self):
        return (
            f"<CropPrice id={self.id} crop_id={self.crop_id} "
            f"market_id={self.market_id} date={self.price_date} "
            f"retail={self.retail_price_ugx} UGX>"
        )