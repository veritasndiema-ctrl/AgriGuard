"""
scripts/validate_data.py — Schema & range validation for AgriGuard datasets
===========================================================================
Validates the WFP price CSV and (optionally) Open-Meteo weather files before
they are used for training or inference.

Usage:
    python scripts/validate_data.py
    python scripts/validate_data.py --prices data/raw/wfp_food_prices_uga.csv
    python scripts/validate_data.py --prices data/raw/wfp_food_prices_uga.csv \\
                                    --weather-dir data/processed/weather
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Expected schema for WFP Uganda prices
# ---------------------------------------------------------------------------
REQUIRED_PRICE_COLS = {"date", "commodity", "market", "price"}
OPTIONAL_PRICE_COLS = {"currency", "unit", "pricetype", "region", "latitude", "longitude"}

# Plausible price bounds (UGX per kg / unit) — soft checks, not hard rejects
PRICE_MIN = 10.0
PRICE_MAX = 50_000.0

# Date window we expect for the Uganda series
DATE_MIN = pd.Timestamp("2015-01-01")
DATE_MAX = pd.Timestamp("2030-12-31")


class ValidationError(Exception):
    """Raised when a hard schema/range violation is found."""


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    rename = {
        "cmname": "commodity",
        "mktname": "market",
        "admname": "region",
        "adm1name": "region",
        "ptname": "pricetype",
        "um": "unit",
        "mp_price": "price",
        "cur": "currency",
    }
    df.rename(columns={k: v for k, v in rename.items() if k in df.columns}, inplace=True)
    return df


def validate_prices(path: Path, strict: bool = True) -> dict:
    """
    Validate a WFP-style price CSV.

    Returns a summary dict. Raises ValidationError on hard failures when
    strict=True.
    """
    if not path.exists():
        raise ValidationError(f"Price file not found: {path}")

    df = pd.read_csv(path, low_memory=False)
    df = _normalise_columns(df)

    summary: dict = {
        "file": str(path),
        "rows_raw": len(df),
        "columns": sorted(df.columns.tolist()),
        "issues": [],
        "warnings": [],
    }

    # Required columns
    missing = REQUIRED_PRICE_COLS - set(df.columns)
    if missing:
        msg = f"Missing required columns: {sorted(missing)}"
        summary["issues"].append(msg)
        if strict:
            raise ValidationError(msg)

    # Type coercion
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        n_bad_dates = df["date"].isna().sum()
        if n_bad_dates:
            summary["warnings"].append(f"{n_bad_dates} unparseable dates")
        if df["date"].notna().any():
            dmin, dmax = df["date"].min(), df["date"].max()
            summary["date_range"] = [str(dmin.date()), str(dmax.date())]
            if dmin < DATE_MIN or dmax > DATE_MAX:
                summary["warnings"].append(
                    f"Date range {dmin.date()}–{dmax.date()} outside expected window"
                )

    if "price" in df.columns:
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        n_nan = df["price"].isna().sum()
        n_neg = (df["price"] <= 0).sum()
        n_high = (df["price"] > PRICE_MAX).sum()
        n_low = ((df["price"] > 0) & (df["price"] < PRICE_MIN)).sum()
        if n_nan:
            summary["warnings"].append(f"{n_nan} non-numeric prices")
        if n_neg:
            summary["issues"].append(f"{n_neg} non-positive prices")
        if n_high:
            summary["warnings"].append(f"{n_high} prices above {PRICE_MAX} UGX")
        if n_low:
            summary["warnings"].append(f"{n_low} prices below {PRICE_MIN} UGX")

        valid = df["price"].dropna()
        valid = valid[valid > 0]
        if len(valid):
            summary["price_stats"] = {
                "min": float(valid.min()),
                "median": float(valid.median()),
                "mean": float(valid.mean()),
                "max": float(valid.max()),
            }

    # Cardinality
    for col in ("commodity", "market"):
        if col in df.columns:
            summary[f"n_{col}"] = int(df[col].nunique())
            summary[f"top_{col}"] = (
                df[col].value_counts().head(5).to_dict()
            )

    summary["rows_clean"] = int(
        df.dropna(subset=list(REQUIRED_PRICE_COLS & set(df.columns))).shape[0]
    )
    summary["ok"] = len(summary["issues"]) == 0
    return summary


def validate_weather_dir(weather_dir: Path) -> dict:
    """Light check on processed weather CSVs."""
    summary: dict = {"dir": str(weather_dir), "files": [], "warnings": []}
    if not weather_dir.exists():
        summary["warnings"].append("Weather directory does not exist")
        return summary

    for f in sorted(weather_dir.glob("*.csv")):
        try:
            df = pd.read_csv(f, nrows=5)
            summary["files"].append(
                {"name": f.name, "cols": list(df.columns), "sample_rows": len(df)}
            )
        except Exception as exc:
            summary["warnings"].append(f"Could not read {f.name}: {exc}")
    return summary


def main(prices: Path, weather_dir: Optional[Path], strict: bool) -> int:
    print("=" * 60)
    print("  AgriGuard — Data Validation")
    print("=" * 60)

    try:
        price_summary = validate_prices(prices, strict=strict)
    except ValidationError as e:
        print(f"\n❌ HARD FAIL: {e}")
        return 1

    print(f"\n📂 Prices: {price_summary['file']}")
    print(f"   Raw rows     : {price_summary['rows_raw']:,}")
    print(f"   Clean rows   : {price_summary.get('rows_clean', '—'):,}")
    print(f"   Columns      : {price_summary['columns']}")
    if "date_range" in price_summary:
        print(f"   Date range   : {price_summary['date_range'][0]} → {price_summary['date_range'][1]}")
    if "n_commodity" in price_summary:
        print(f"   Commodities  : {price_summary['n_commodity']}")
        print(f"   Markets      : {price_summary['n_market']}")
    if "price_stats" in price_summary:
        s = price_summary["price_stats"]
        print(f"   Price (UGX)  : min={s['min']:.0f}  med={s['median']:.0f}  mean={s['mean']:.0f}  max={s['max']:.0f}")

    for w in price_summary.get("warnings", []):
        print(f"   ⚠️  {w}")
    for i in price_summary.get("issues", []):
        print(f"   ❌ {i}")

    if weather_dir:
        wsum = validate_weather_dir(weather_dir)
        print(f"\n🌤  Weather dir: {wsum['dir']}")
        print(f"   Files found : {len(wsum['files'])}")
        for f in wsum["files"][:5]:
            print(f"      • {f['name']}  cols={f['cols']}")
        for w in wsum.get("warnings", []):
            print(f"   ⚠️  {w}")

    if price_summary["ok"]:
        print("\n✅ Price dataset passed validation.")
        return 0
    else:
        print("\n❌ Price dataset has issues (see above).")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate AgriGuard data files")
    parser.add_argument(
        "--prices",
        type=Path,
        default=Path("data/raw/wfp_food_prices_uga.csv"),
    )
    parser.add_argument(
        "--weather-dir",
        type=Path,
        default=None,
        help="Optional path to processed weather CSVs",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=True,
        help="Raise on missing required columns (default: True)",
    )
    parser.add_argument(
        "--no-strict",
        action="store_false",
        dest="strict",
    )
    args = parser.parse_args()
    sys.exit(main(args.prices, args.weather_dir, args.strict))
