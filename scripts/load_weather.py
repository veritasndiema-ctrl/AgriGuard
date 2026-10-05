#!/usr/bin/env python3
"""
scripts/load_weather.py — Load processed weather CSVs into the database
==========================================================================
Bridges the gap `data/README.md` flags directly: "nothing in `backend/` or
`ml/` currently reads from `data/raw/weather/` or `data/processed/weather/`".

`scripts/fetch_weather.py` already writes combined CSVs to
`data/processed/weather/uganda_weather_{historical|forecast}_{date}.csv`.
This script reads those CSVs, looks up each row's market by name (creating
the market lookup once, not per-row), and upserts into `weather_readings`
via `WeatherService.bulk_upsert()` — the same idempotent
insert-or-update-in-place logic the API's `POST /weather` endpoint uses.

Run from repo root, same convention as the rest of scripts/:
    python scripts/load_weather.py
    python scripts/load_weather.py --file data/processed/weather/uganda_weather_historical_2026-06-14.csv
    python scripts/load_weather.py --historical-only
    python scripts/load_weather.py --forecast-only

Known gap this script does NOT fix (see data/README.md §2 "Known gap" and
the market-coverage note): `fetch_weather.py` only covers 8 of the 10 WFP
markets — Fort Portal and Soroti have no weather rows. Rows for markets not
found in the `markets` table are skipped and counted, not silently dropped —
the summary at the end reports how many.

Author: AgriGuard Team
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # allow `python scripts/load_weather.py` from repo root

from backend.app.database import SessionLocal, create_tables  # noqa: E402
from backend.app.core.config import settings  # noqa: E402
from backend.app.models.price import Market  # noqa: E402
from backend.app.schemas.weather import WeatherReadingCreate  # noqa: E402
from backend.app.services.weather_service import WeatherService  # noqa: E402

PROCESSED_DIR = ROOT / "data" / "processed" / "weather"

# CSV columns that map 1:1 onto WeatherReadingCreate fields (see
# backend/app/models/weather.py's docstring — the whole pipeline keeps
# these names identical on purpose).
DIRECT_COLUMNS = [
    "elevation_m", "temp_max_c", "temp_min_c", "rainfall_mm", "rain_mm",
    "precip_hours", "humidity_max_pct", "humidity_min_pct",
    "wind_speed_max_kmh", "sunshine_seconds", "et0_evapotranspiration_mm",
    "water_balance_mm", "data_source", "fetched_at",
]


def find_csv_files(historical_only: bool, forecast_only: bool) -> list[Path]:
    """Discovers processed weather CSVs, newest fetch-date first per type."""
    if not PROCESSED_DIR.exists():
        return []

    pattern = "uganda_weather_*.csv"
    files = sorted(PROCESSED_DIR.glob(pattern), reverse=True)

    if historical_only:
        files = [f for f in files if "historical" in f.name]
    elif forecast_only:
        files = [f for f in files if "forecast" in f.name]

    return files


def load_csv(path: Path) -> pd.DataFrame:
    """Reads one processed weather CSV and tags each row historical/forecast."""
    df = pd.read_csv(path)
    df["is_forecast"] = "forecast" in path.name
    return df


def build_market_lookup(db) -> dict[str, int]:
    """
    Name -> id lookup, built once per run rather than querying per-row.
    Case-insensitive since `fetch_weather.py`'s MARKETS dict and the WFP
    market list may not always agree on capitalization.
    """
    markets = db.query(Market).all()
    return {m.name.strip().lower(): m.id for m in markets}


def rows_to_creates(
    df: pd.DataFrame, market_lookup: dict[str, int]
) -> tuple[list[WeatherReadingCreate], int]:
    """
    Converts CSV rows into WeatherReadingCreate objects, skipping rows
    whose market isn't in the `markets` table yet (see module docstring —
    this is expected for Fort Portal and Soroti until fetch_weather.py's
    MARKETS dict is extended to cover them).
    """
    creates: list[WeatherReadingCreate] = []
    skipped = 0

    for _, row in df.iterrows():
        market_id = market_lookup.get(str(row.get("market", "")).strip().lower())
        if market_id is None:
            skipped += 1
            continue

        field_values = {}
        for col in DIRECT_COLUMNS:
            if col in row and pd.notna(row[col]):
                field_values[col] = row[col]

        try:
            creates.append(
                WeatherReadingCreate(
                    market_id=market_id,
                    reading_date=row["date"],
                    is_forecast=bool(row["is_forecast"]),
                    **field_values,
                )
            )
        except Exception as e:  # noqa: BLE001 — a malformed row shouldn't kill the whole load
            print(f"  ⚠ Skipping malformed row (market={row.get('market')}, "
                  f"date={row.get('date')}): {e}")
            skipped += 1

    return creates, skipped


def main(
    target_file: str = None,
    historical_only: bool = False,
    forecast_only: bool = False,
) -> int:
    print("\n🌦  AgriGuard Weather Loader")
    print(f"   Database : {settings.database_url}")

    # Ensure weather_readings (and markets, crops, crop_prices) exist —
    # safe/idempotent on a dev SQLite file. In production this is a no-op
    # against an already-migrated DB.
    create_tables()

    if target_file:
        files = [Path(target_file)]
    else:
        files = find_csv_files(historical_only, forecast_only)

    if not files:
        print("❌ No processed weather CSVs found.")
        print(f"   Expected under: {PROCESSED_DIR}")
        print("   Run: python scripts/fetch_weather.py first.")
        return 1

    db = SessionLocal()
    total_created = total_updated = total_skipped = 0

    try:
        market_lookup = build_market_lookup(db)
        if not market_lookup:
            print("❌ No markets found in the database.")
            print("   Markets must be seeded (e.g. via price data loading) "
                  "before weather can be joined to them.")
            return 1

        service = WeatherService(db=db, settings=settings)

        for path in files:
            if not path.exists():
                print(f"  ⚠ File not found, skipping: {path}")
                continue

            print(f"\n  → Loading {path.name}...")
            df = load_csv(path)
            creates, skipped_no_market = rows_to_creates(df, market_lookup)

            result = service.bulk_upsert(creates)
            total_created += result["created"]
            total_updated += result["updated"]
            total_skipped += result["skipped"] + skipped_no_market

            print(
                f"    ✓ {result['created']} created, {result['updated']} updated, "
                f"{result['skipped'] + skipped_no_market} skipped "
                f"({len(df)} rows in file)"
            )

    finally:
        db.close()

    print("\n" + "=" * 60)
    print("LOAD SUMMARY")
    print("=" * 60)
    print(f"Created : {total_created}")
    print(f"Updated : {total_updated}")
    print(f"Skipped : {total_skipped}  (unmatched market names / malformed rows)")
    print("=" * 60)
    print("\n✅ Done! Weather is now joinable to prices on market_id.")
    print("   Next: python ml/training/train_forecast.py  (uses weather features)\n")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AgriGuard: Load processed weather CSVs into the database"
    )
    parser.add_argument(
        "--file", type=str, default=None,
        help="Load a single specific CSV instead of auto-discovering all of them",
    )
    parser.add_argument(
        "--historical-only", action="store_true",
        help="Only load historical CSVs (skip forecast files)",
    )
    parser.add_argument(
        "--forecast-only", action="store_true",
        help="Only load forecast CSVs (skip historical files)",
    )

    args = parser.parse_args()
    sys.exit(main(
        target_file=args.file,
        historical_only=args.historical_only,
        forecast_only=args.forecast_only,
    ))
