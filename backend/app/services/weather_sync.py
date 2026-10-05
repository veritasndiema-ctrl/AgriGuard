"""
backend/app/services/weather_sync.py — Live sync for Open-Meteo weather data
================================================================================
Problem this replaces: `scripts/fetch_weather.py` + `scripts/load_weather.py`
were two disconnected MANUAL steps — fetch to CSV, then separately run a
second script to load that CSV into the DB — and nobody was running either
on a schedule. Worse: `load_weather.py` requires the `markets` table to
already be seeded ("Markets must be seeded ... before weather can be joined
to them"), but nothing in this repo ever created a single row in `markets` —
`scripts/load_data.py` never touches it. So today, `weather_readings` is
empty and `GET /weather/analytics/drought-risk` has zero rows to score,
regardless of `fetch_weather.py`/`load_weather.py` being fully implemented.

This module closes that whole loop and matches wfp_sync.py's /
fews_net_sync.py's shape — `sync_if_updated(force) -> bool` and
`last_sync_info() -> dict` — so `backend/app/services/data_sources.py`
schedules it exactly like those two auto-syncing price sources (see that
module's registry; main.py's startup scheduler is generic over it, so
registering weather there is the only wiring this needed).

Each cycle:
  1. Seeds/refreshes the `markets` table from MARKET_COORDS (idempotent
     upsert by name) — the step nothing in the repo did before.
  2. Fetches Open-Meteo's trailing `settings.weather_sync_lookback_days` of
     history plus a 16-day forecast, per market.
  3. Cleans every row through WeatherReadingCreate's own validation
     (0-100% humidity bounds, non-negative rainfall, min-humidity-not-above-
     max) — a row that fails validation is dropped and logged, never
     silently coerced or inserted anyway.
  4. Upserts via WeatherService.bulk_upsert(), keyed on
     (market_id, reading_date, is_forecast) — safe to re-run indefinitely,
     same idempotency load_weather.py relied on.
  5. Records per-market sync state (counts, failures, timestamp) to a local
     JSON file so the API/dashboard can show "last synced" without another
     Open-Meteo call.

Open-Meteo has no changed-since metadata endpoint the way HDX/FDW do (a
16-day forecast reissues daily regardless of whether anything material
changed) — so this always re-fetches on every scheduled run instead of
wfp_sync.py's check-metadata-then-maybe-download two-step. The trailing
window (not a full historical re-backfill) keeps each cycle's request count
small and fixed (2 requests x len(MARKET_COORDS)), cheap enough to run on
the same interval as the price syncs.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.app.core.config import settings
from backend.app.database import SessionLocal
from backend.app.models.price import Market, UgandaRegion
from backend.app.schemas.weather import WeatherReadingCreate
from backend.app.services.weather_service import WeatherService

logger = logging.getLogger(__name__)

HISTORICAL_API_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_API_URL = "https://api.open-meteo.com/v1/forecast"

DAILY_VARIABLES = [
    "temperature_2m_max", "temperature_2m_min", "precipitation_sum", "rain_sum",
    "relative_humidity_2m_max", "relative_humidity_2m_min", "wind_speed_10m_max",
    "et0_fao_evapotranspiration", "precipitation_hours", "sunshine_duration",
]

# The same 8 markets scripts/fetch_weather.py has always covered (coordinates
# sourced from MAAIF market monitoring points). Seeded here into `markets`
# since nothing else in the repo ever did (see module docstring) — WFP's
# price CSV covers more markets (e.g. Fort Portal, Soroti, the refugee-
# settlement markets) that simply have no weather coverage yet; extending
# this list is how you'd add them.
# UgandaRegion only models Uganda's 4 standard regions; Arua's "West Nile"
# sub-region is administratively grouped under Northern Region in that schema.
MARKET_COORDS: list[dict] = [
    {"name": "Kampala", "lat": 0.3476,  "lon": 32.5825, "district": "Kampala", "region": UgandaRegion.Central},
    {"name": "Gulu",    "lat": 2.7747,  "lon": 32.2990, "district": "Gulu",    "region": UgandaRegion.Northern},
    {"name": "Mbarara", "lat": -0.6072, "lon": 30.6545, "district": "Mbarara", "region": UgandaRegion.Western},
    {"name": "Mbale",   "lat": 1.0796,  "lon": 34.1750, "district": "Mbale",   "region": UgandaRegion.Eastern},
    {"name": "Kasese",  "lat": 0.1833,  "lon": 30.0833, "district": "Kasese",  "region": UgandaRegion.Western},
    {"name": "Lira",    "lat": 2.2499,  "lon": 32.8998, "district": "Lira",    "region": UgandaRegion.Northern},
    {"name": "Jinja",   "lat": 0.4244,  "lon": 33.2041, "district": "Jinja",   "region": UgandaRegion.Eastern},
    {"name": "Arua",    "lat": 3.0200,  "lon": 30.9100, "district": "Arua",    "region": UgandaRegion.Northern},
]

STATE_PATH = Path(settings.price_data_path).parent / ".weather_sync_state.json"

_RETRYABLE = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
)


# =============================================================================
# Local sync-state bookkeeping (same pattern as wfp_sync.py)
# =============================================================================

def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("weather sync state file unreadable — treating as first run.")
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def last_sync_info() -> dict:
    """What the API/UI can show for 'last updated' — no network call."""
    return _load_state()


# =============================================================================
# Market seeding
# =============================================================================

def ensure_markets_seeded(db) -> dict[str, int]:
    """
    Upsert-by-name each of MARKET_COORDS into `markets` — creating missing
    rows, refreshing lat/lon/district/region on ones that already exist.
    Returns a name(lowercased) -> id lookup, the same shape
    scripts/load_weather.py builds, for the fetch loop below to reuse.

    Before this existed, `markets` had zero rows in a fresh environment —
    scripts/load_weather.py's own README-equivalent note calls this out
    ("Markets must be seeded ... before weather can be joined to them") but
    nothing ever did the seeding.
    """
    lookup: dict[str, int] = {}
    for spec in MARKET_COORDS:
        market = db.query(Market).filter(Market.name == spec["name"]).first()
        if market is None:
            market = Market(
                name=spec["name"],
                district=spec["district"],
                region=spec["region"],
                latitude=spec["lat"],
                longitude=spec["lon"],
            )
            db.add(market)
            db.flush()  # assigns market.id without committing yet
        else:
            market.latitude = spec["lat"]
            market.longitude = spec["lon"]
            market.district = spec["district"]
            market.region = spec["region"]
        lookup[spec["name"].strip().lower()] = market.id

    db.commit()
    return lookup


# =============================================================================
# Open-Meteo fetch + clean
# =============================================================================

@_RETRYABLE
def _fetch(url: str, params: dict) -> dict:
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(data.get("reason", "Open-Meteo returned an error"))
    return data


def _daily_params(lat: float, lon: float, **extra) -> dict:
    return {
        "latitude": lat,
        "longitude": lon,
        "daily": ",".join(DAILY_VARIABLES),
        "timezone": "Africa/Kampala",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        **extra,
    }


def _rows_from_response(payload: dict, market_id: int, is_forecast: bool) -> tuple[list[WeatherReadingCreate], int]:
    """
    Flattens Open-Meteo's columnar `daily` block into validated
    WeatherReadingCreate rows. This IS the cleaning step: a row that fails
    WeatherReadingCreate's own bounds (humidity outside 0-100%, negative
    rainfall, min-humidity-above-max, etc. — see schemas/weather.py) is
    dropped and counted, never inserted with bad values.

    Returns (valid_rows, dropped_count).
    """
    daily = payload.get("daily", {})
    dates = daily.get("time", [])
    elevation = payload.get("elevation")
    fetched_at = datetime.now(timezone.utc)

    def col(key: str, i: int):
        values = daily.get(key)
        return values[i] if values and i < len(values) else None

    rows: list[WeatherReadingCreate] = []
    dropped = 0
    for i, day in enumerate(dates):
        rainfall = col("precipitation_sum", i)
        et0 = col("et0_fao_evapotranspiration", i)
        water_balance = (rainfall - et0) if rainfall is not None and et0 is not None else None

        try:
            rows.append(
                WeatherReadingCreate(
                    market_id=market_id,
                    reading_date=day,
                    is_forecast=is_forecast,
                    elevation_m=elevation,
                    temp_max_c=col("temperature_2m_max", i),
                    temp_min_c=col("temperature_2m_min", i),
                    rainfall_mm=rainfall,
                    rain_mm=col("rain_sum", i),
                    precip_hours=col("precipitation_hours", i),
                    humidity_max_pct=col("relative_humidity_2m_max", i),
                    humidity_min_pct=col("relative_humidity_2m_min", i),
                    wind_speed_max_kmh=col("wind_speed_10m_max", i),
                    sunshine_seconds=col("sunshine_duration", i),
                    et0_evapotranspiration_mm=et0,
                    water_balance_mm=water_balance,
                    data_source="open-meteo.com",
                    fetched_at=fetched_at,
                )
            )
        except ValidationError as exc:
            dropped += 1
            logger.warning(
                "Dropping invalid weather row (market_id=%s date=%s): %s", market_id, day, exc
            )

    return rows, dropped


# =============================================================================
# Full sync pipeline
# =============================================================================

def sync_if_updated(force: bool = False) -> bool:
    """
    Full pipeline: seed markets -> fetch trailing history + 16-day forecast
    per market -> validate/clean -> upsert -> record sync state.

    `force` has no special meaning here (unlike wfp_sync.py, there's no
    "check metadata first" step to skip — every call already re-fetches)
    but is accepted so this matches the other sync modules' signature for
    main.py's generic scheduler and the dashboard's "Check for updates now"
    button.

    Returns True if at least one market synced successfully; False only if
    every market's fetch failed this cycle (existing data is left alone).
    """
    db = SessionLocal()
    per_market: dict[str, dict] = {}
    any_success = False

    try:
        lookup = ensure_markets_seeded(db)
        service = WeatherService(db=db, settings=settings)

        end_date = date.today()
        start_date = end_date - timedelta(days=settings.weather_sync_lookback_days)

        for spec in MARKET_COORDS:
            market_id = lookup[spec["name"].strip().lower()]
            try:
                hist = _fetch(
                    HISTORICAL_API_URL,
                    _daily_params(
                        spec["lat"], spec["lon"],
                        start_date=start_date.isoformat(),
                        end_date=end_date.isoformat(),
                    ),
                )
                fcast = _fetch(
                    FORECAST_API_URL,
                    _daily_params(spec["lat"], spec["lon"], forecast_days=16),
                )
            except Exception as exc:  # noqa: BLE001 — one market's outage shouldn't sink the cycle
                logger.warning("Weather fetch failed for %s (will retry next cycle): %s", spec["name"], exc)
                per_market[spec["name"]] = {"synced": False, "error": str(exc)}
                continue

            hist_rows, hist_dropped = _rows_from_response(hist, market_id, is_forecast=False)
            fcast_rows, fcast_dropped = _rows_from_response(fcast, market_id, is_forecast=True)

            result = service.bulk_upsert(hist_rows + fcast_rows)
            per_market[spec["name"]] = {
                "synced": True,
                "dropped_invalid": hist_dropped + fcast_dropped,
                **result,
            }
            any_success = True

    finally:
        db.close()

    prior = _load_state()
    state = {
        "synced_at": datetime.now(timezone.utc).isoformat() if any_success else prior.get("synced_at"),
        "markets_synced": sum(1 for m in per_market.values() if m.get("synced")),
        "markets_total": len(MARKET_COORDS),
        "lookback_days": settings.weather_sync_lookback_days,
        "history_through": date.today().isoformat() if any_success else prior.get("history_through"),
        "forecast_through": (
            (date.today() + timedelta(days=16)).isoformat()
            if any_success else prior.get("forecast_through")
        ),
        "per_market": per_market,
    }
    _save_state(state)

    if any_success:
        logger.info(
            "Weather sync complete — %d/%d markets updated.",
            state["markets_synced"], state["markets_total"],
        )
    else:
        logger.error("Weather sync failed for every market this cycle — keeping existing data.")

    return any_success
