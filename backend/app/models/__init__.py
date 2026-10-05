"""
backend/app/models/__init__.py
===============================
Imports every ORM model so:

  1. `Base.metadata.create_all()` (backend/app/database.py::create_tables)
     actually sees every table — a model that's never imported never
     registers itself on `Base.metadata`, so `create_tables()` would
     silently skip it.
  2. String-based `relationship("WeatherReading")` / `relationship("Market")`
     references (used on both sides of the Market <-> WeatherReading
     relationship) resolve correctly. SQLAlchemy resolves these lazily at
     first use, but only if the class has been imported into the same
     `Base` registry by then — importing both modules here guarantees that
     regardless of which one a caller imports first.

Was empty before; this was a real gap, not a style choice — without it,
`WeatherReading` could be imported and used directly
(`from backend.app.models.weather import WeatherReading`) but
`create_tables()` run from a fresh script that only touches
`backend.app.models.price` would never create the `weather_readings` table.
"""

from backend.app.models.price import (
    Crop,
    CropPrice,
    DataQuality,
    Market,
    PriceUnit,
    UgandaRegion,
)
from backend.app.models.weather import WeatherReading

__all__ = [
    "Crop",
    "Market",
    "CropPrice",
    "DataQuality",
    "PriceUnit",
    "UgandaRegion",
    "WeatherReading",
]