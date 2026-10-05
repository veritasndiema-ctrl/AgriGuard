"""
fetch_weather.py — AgriGuard Weather Data Pipeline
====================================================
Pulls historical and forecast weather data from the Open-Meteo API
(https://open-meteo.com) for key Ugandan agricultural markets.

Open-Meteo is:
  - Completely FREE — no API key required
  - High resolution (1km grid)
  - Covers Uganda with good accuracy
  - Provides both historical data and 16-day forecasts

What this script collects:
  - Daily temperature (max/min)
  - Precipitation (rainfall in mm)
  - Relative humidity
  - Wind speed
  - Evapotranspiration (useful for crop stress modeling)

These variables are the most important for correlating with
crop price movements — e.g. drought → supply drop → price spike.

Usage:
    python scripts/fetch_weather.py                  # fetch all markets
    python scripts/fetch_weather.py --market Kampala # single market
    python scripts/fetch_weather.py --days 90        # last 90 days

Author: AgriGuard Team
"""

import requests
import json
import csv
import os
import argparse
from datetime import datetime, timedelta
from pathlib import Path


# =============================================================================
# CONFIGURATION — Uganda's key agricultural markets / districts
# Coordinates sourced from MAAIF market monitoring points
# =============================================================================

MARKETS = {
    "Kampala": {
        "lat": 0.3476,
        "lon": 32.5825,
        "region": "Central",
        "primary_crops": ["maize", "beans", "tomatoes"],
    },
    "Gulu": {
        "lat": 2.7747,
        "lon": 32.2990,
        "region": "Northern",
        "primary_crops": ["maize", "sorghum", "groundnuts"],
    },
    "Mbarara": {
        "lat": -0.6072,
        "lon": 30.6545,
        "region": "Western",
        "primary_crops": ["maize", "beans", "irish_potatoes"],
    },
    "Mbale": {
        "lat": 1.0796,
        "lon": 34.1750,
        "region": "Eastern",
        "primary_crops": ["maize", "coffee", "bananas"],
    },
    "Kasese": {
        "lat": 0.1833,
        "lon": 30.0833,
        "region": "Western",
        "primary_crops": ["maize", "beans", "cassava"],
    },
    "Lira": {
        "lat": 2.2499,
        "lon": 32.8998,
        "region": "Northern",
        "primary_crops": ["maize", "sorghum", "simsim"],
    },
    "Jinja": {
        "lat": 0.4244,
        "lon": 33.2041,
        "region": "Eastern",
        "primary_crops": ["maize", "sugarcane", "beans"],
    },
    "Arua": {
        "lat": 3.0200,
        "lon": 30.9100,
        "region": "West Nile",
        "primary_crops": ["maize", "cassava", "groundnuts"],
    },
}

# =============================================================================
# WEATHER VARIABLES TO FETCH
# These are Open-Meteo variable names — see docs.open-meteo.com for full list
# =============================================================================

DAILY_VARIABLES = [
    "temperature_2m_max",        # Max daily temp in °C
    "temperature_2m_min",        # Min daily temp in °C
    "precipitation_sum",         # Total rainfall mm/day
    "rain_sum",                  # Rain component only (excl. snow)
    "relative_humidity_2m_max",  # Max humidity %
    "relative_humidity_2m_min",  # Min humidity %
    "wind_speed_10m_max",        # Max wind speed km/h
    "et0_fao_evapotranspiration",# Reference evapotranspiration (crop stress)
    "precipitation_hours",       # Hours of precipitation (intensity proxy)
    "sunshine_duration",         # Seconds of sunshine per day
]

# Base URL for Open-Meteo historical weather API
HISTORICAL_API_URL = "https://archive-api.open-meteo.com/v1/archive"

# Base URL for Open-Meteo 16-day forecast API
FORECAST_API_URL = "https://api.open-meteo.com/v1/forecast"

# Where to save the output data
# Follows the AgriGuard project structure from the skeleton
OUTPUT_DIR = Path("data/raw/weather")
PROCESSED_DIR = Path("data/processed/weather")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_date_range(days_back: int = 365) -> tuple[str, str]:
    """
    Returns a (start_date, end_date) tuple formatted as 'YYYY-MM-DD'.

    Args:
        days_back: How many days of historical data to fetch.
                   Default is 365 (one full year).

    Returns:
        Tuple of (start_date_str, end_date_str)

    Example:
        >>> get_date_range(90)
        ('2024-02-10', '2024-05-10')
    """
    end_date = datetime.today()
    start_date = end_date - timedelta(days=days_back)
    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


def build_historical_params(lat: float, lon: float, start: str, end: str) -> dict:
    """
    Builds the query parameter dict for the Open-Meteo historical archive API.

    Args:
        lat: Latitude of the market location
        lon: Longitude of the market location
        start: Start date string 'YYYY-MM-DD'
        end: End date string 'YYYY-MM-DD'

    Returns:
        Dictionary of query parameters for requests.get()
    """
    return {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "daily": ",".join(DAILY_VARIABLES),
        "timezone": "Africa/Kampala",  # Uganda timezone (UTC+3)
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    }


def build_forecast_params(lat: float, lon: float) -> dict:
    """
    Builds query parameters for the 16-day weather forecast API.

    Args:
        lat: Latitude
        lon: Longitude

    Returns:
        Dictionary of query parameters
    """
    return {
        "latitude": lat,
        "longitude": lon,
        "daily": ",".join(DAILY_VARIABLES),
        "timezone": "Africa/Kampala",
        "forecast_days": 16,          # Maximum available from Open-Meteo free tier
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    }


def fetch_weather_data(url: str, params: dict, market_name: str) -> dict | None:
    """
    Makes the HTTP GET request to Open-Meteo and handles errors gracefully.

    Args:
        url: The API endpoint URL
        params: Query parameters dict
        market_name: Used only for logging/error messages

    Returns:
        Parsed JSON response as a dict, or None if the request fails.
    """
    try:
        print(f"  → Fetching weather for {market_name}...")
        response = requests.get(url, params=params, timeout=30)

        # Raise an exception for HTTP errors (4xx, 5xx)
        response.raise_for_status()

        data = response.json()

        # Open-Meteo returns an 'error' key in JSON on some failures
        if "error" in data:
            print(f"  ✗ API error for {market_name}: {data.get('reason', 'Unknown')}")
            return None

        return data

    except requests.exceptions.Timeout:
        print(f"  ✗ Timeout fetching data for {market_name}. Will retry next run.")
        return None

    except requests.exceptions.ConnectionError:
        print(f"  ✗ Connection failed for {market_name}. Check your internet.")
        return None

    except requests.exceptions.HTTPError as e:
        print(f"  ✗ HTTP {e.response.status_code} for {market_name}: {e}")
        return None

    except json.JSONDecodeError:
        print(f"  ✗ Invalid JSON response for {market_name}")
        return None


def parse_weather_response(api_response: dict, market_name: str, region: str) -> list[dict]:
    """
    Flattens the Open-Meteo JSON response into a list of daily records.

    The API returns data in columnar format:
        {"daily": {"time": [...], "temperature_2m_max": [...], ...}}

    We convert this to row format (one dict per day) which is easier
    to insert into the database and write to CSV.

    Args:
        api_response: Raw JSON from Open-Meteo
        market_name: Name of the market (e.g. "Kampala")
        region: Uganda region name

    Returns:
        List of dicts, one per day, ready for DB insertion or CSV export.
        Example row:
        {
            "date": "2024-01-15",
            "market": "Kampala",
            "region": "Central",
            "temp_max": 28.5,
            "temp_min": 18.2,
            "rainfall_mm": 12.4,
            ...
        }
    """
    daily = api_response.get("daily", {})
    dates = daily.get("time", [])

    if not dates:
        print(f"  ⚠ No daily data returned for {market_name}")
        return []

    records = []

    for i, date in enumerate(dates):
        record = {
            "date": date,
            "market": market_name,
            "region": region,
            "latitude": api_response.get("latitude"),
            "longitude": api_response.get("longitude"),
            "elevation_m": api_response.get("elevation"),

            # Temperature
            "temp_max_c": daily.get("temperature_2m_max", [None] * len(dates))[i],
            "temp_min_c": daily.get("temperature_2m_min", [None] * len(dates))[i],

            # Rainfall
            "rainfall_mm": daily.get("precipitation_sum", [None] * len(dates))[i],
            "rain_mm": daily.get("rain_sum", [None] * len(dates))[i],
            "precip_hours": daily.get("precipitation_hours", [None] * len(dates))[i],

            # Humidity
            "humidity_max_pct": daily.get("relative_humidity_2m_max", [None] * len(dates))[i],
            "humidity_min_pct": daily.get("relative_humidity_2m_min", [None] * len(dates))[i],

            # Wind & Sun
            "wind_speed_max_kmh": daily.get("wind_speed_10m_max", [None] * len(dates))[i],
            "sunshine_seconds": daily.get("sunshine_duration", [None] * len(dates))[i],

            # Evapotranspiration — key indicator of crop water stress
            # High ET0 + low rainfall = drought stress = likely price increase
            "et0_evapotranspiration_mm": daily.get("et0_fao_evapotranspiration", [None] * len(dates))[i],

            # Derived: simple water deficit proxy for crop stress modeling
            # Positive = surplus water, Negative = deficit (stress)
            "water_balance_mm": (
                (daily.get("precipitation_sum", [None] * len(dates))[i] or 0)
                - (daily.get("et0_fao_evapotranspiration", [None] * len(dates))[i] or 0)
            ),

            # Metadata
            "fetched_at": datetime.utcnow().isoformat(),
            "data_source": "open-meteo.com",
        }
        records.append(record)

    return records


def save_to_json(records: list[dict], market_name: str, data_type: str = "historical") -> Path:
    """
    Saves weather records to a JSON file in data/raw/weather/.

    File naming convention: {market}_{data_type}_{YYYY-MM-DD}.json
    This makes it easy to identify what's already been downloaded.

    Args:
        records: List of daily weather record dicts
        market_name: Market name for filename
        data_type: "historical" or "forecast"

    Returns:
        Path to the saved file
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.today().strftime("%Y-%m-%d")
    filename = OUTPUT_DIR / f"{market_name.lower()}_{data_type}_{today}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"  ✓ Saved {len(records)} records → {filename}")
    return filename


def save_to_csv(all_records: list[dict], data_type: str = "historical") -> Path:
    """
    Saves ALL markets' weather records into a single CSV file.
    This goes to data/processed/weather/ — ready for DB loading.

    A single CSV across all markets is easier to bulk-load into PostgreSQL.

    Args:
        all_records: Combined list of records from all markets
        data_type: "historical" or "forecast"

    Returns:
        Path to the saved CSV
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.today().strftime("%Y-%m-%d")
    filename = PROCESSED_DIR / f"uganda_weather_{data_type}_{today}.csv"

    if not all_records:
        print("  ⚠ No records to save to CSV.")
        return filename

    # Use the keys from the first record as column headers
    fieldnames = list(all_records[0].keys())

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_records)

    print(f"\n✅ Combined CSV saved: {filename} ({len(all_records)} total rows)")
    return filename


def generate_summary_report(all_records: list[dict]) -> None:
    """
    Prints a simple console summary of the fetched weather data.
    Useful for quick sanity-checking before loading into the DB.

    Highlights:
    - Markets fetched
    - Date range covered
    - Any missing data (None values in rainfall)
    - Extreme weather flags (heavy rain, drought conditions)
    """
    if not all_records:
        print("\n⚠ No data to summarize.")
        return

    print("\n" + "=" * 60)
    print("AGRIGUARD WEATHER FETCH SUMMARY")
    print("=" * 60)

    markets_fetched = list(set(r["market"] for r in all_records))
    dates = sorted(set(r["date"] for r in all_records))

    print(f"Markets fetched : {len(markets_fetched)}")
    print(f"  → {', '.join(markets_fetched)}")
    print(f"Date range      : {dates[0]} → {dates[-1]}")
    print(f"Total records   : {len(all_records)}")

    # Count missing rainfall values
    missing_rain = sum(1 for r in all_records if r.get("rainfall_mm") is None)
    if missing_rain > 0:
        print(f"\n⚠ Missing rainfall data: {missing_rain} records")

    # Flag extreme rainfall days (>50mm = heavy rain, likely flooding risk)
    heavy_rain_days = [
        r for r in all_records
        if r.get("rainfall_mm") is not None and r["rainfall_mm"] > 50
    ]
    if heavy_rain_days:
        print(f"\n🌧 Heavy rain days (>50mm): {len(heavy_rain_days)}")
        for r in sorted(heavy_rain_days, key=lambda x: x["rainfall_mm"], reverse=True)[:5]:
            print(f"   {r['date']} | {r['market']:12} | {r['rainfall_mm']:.1f} mm")

    # Flag drought-stress days (negative water balance for consecutive days)
    drought_days = [
        r for r in all_records
        if r.get("water_balance_mm") is not None and r["water_balance_mm"] < -5
    ]
    if drought_days:
        print(f"\n☀️  Drought-stress days (water deficit > 5mm): {len(drought_days)}")

    print("=" * 60)


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main(target_market: str = None, days_back: int = 365, forecast: bool = True):
    """
    Main entry point. Orchestrates the full weather data fetch pipeline:
      1. Determine which markets to fetch
      2. Fetch historical weather data
      3. Optionally fetch 16-day forecast
      4. Parse, save JSON (raw), save CSV (processed)
      5. Print summary

    Args:
        target_market: If given, only fetch this one market. Otherwise fetch all.
        days_back: Days of historical data to retrieve (default: 365)
        forecast: Whether to also fetch 16-day forecast (default: True)
    """

    # -------------------------------------------------------------------------
    # 1. Determine which markets to process
    # -------------------------------------------------------------------------
    if target_market:
        if target_market not in MARKETS:
            print(f"❌ Market '{target_market}' not found.")
            print(f"   Available: {', '.join(MARKETS.keys())}")
            return
        markets_to_fetch = {target_market: MARKETS[target_market]}
    else:
        markets_to_fetch = MARKETS

    print(f"\n🌿 AgriGuard Weather Fetcher")
    print(f"   Markets  : {len(markets_to_fetch)}")
    print(f"   History  : last {days_back} days")
    print(f"   Forecast : {'Yes (16 days)' if forecast else 'No'}")
    print(f"   Source   : Open-Meteo (free, no API key)\n")

    start_date, end_date = get_date_range(days_back)
    print(f"   Date range: {start_date} → {end_date}\n")

    all_historical = []
    all_forecast = []

    # -------------------------------------------------------------------------
    # 2. Loop through each market and fetch data
    # -------------------------------------------------------------------------
    for market_name, info in markets_to_fetch.items():

        lat = info["lat"]
        lon = info["lon"]
        region = info["region"]

        # --- Historical data ---
        hist_params = build_historical_params(lat, lon, start_date, end_date)
        hist_response = fetch_weather_data(HISTORICAL_API_URL, hist_params, market_name)

        if hist_response:
            records = parse_weather_response(hist_response, market_name, region)
            all_historical.extend(records)
            save_to_json(records, market_name, data_type="historical")

        # --- 16-day forecast ---
        if forecast:
            fcast_params = build_forecast_params(lat, lon)
            fcast_response = fetch_weather_data(FORECAST_API_URL, fcast_params, market_name)

            if fcast_response:
                f_records = parse_weather_response(fcast_response, market_name, region)
                all_forecast.extend(f_records)
                save_to_json(f_records, market_name, data_type="forecast")

    # -------------------------------------------------------------------------
    # 3. Save combined CSVs (processed — ready for DB loading)
    # -------------------------------------------------------------------------
    print("\n📦 Saving combined CSVs...")
    if all_historical:
        save_to_csv(all_historical, data_type="historical")
    if all_forecast:
        save_to_csv(all_forecast, data_type="forecast")

    # -------------------------------------------------------------------------
    # 4. Print summary report
    # -------------------------------------------------------------------------
    generate_summary_report(all_historical)

    print("\n✅ Done! Next step: run scripts/load_data.py to insert into PostgreSQL.\n")


# =============================================================================
# CLI ARGUMENT PARSING
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AgriGuard: Fetch weather data for Ugandan agricultural markets"
    )
    parser.add_argument(
        "--market",
        type=str,
        default=None,
        help="Fetch a single market only (e.g. --market Kampala)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Days of historical data to fetch (default: 365)",
    )
    parser.add_argument(
        "--no-forecast",
        action="store_true",
        help="Skip fetching 16-day forecast data",
    )

    args = parser.parse_args()

    main(
        target_market=args.market,
        days_back=args.days,
        forecast=not args.no_forecast,
    )