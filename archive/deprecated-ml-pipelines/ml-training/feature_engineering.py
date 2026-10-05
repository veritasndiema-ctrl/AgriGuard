"""
Shared feature engineering for AgriGuard price models.

STATUS: not actually imported by scripts/train_models.py -- that script
has its own inline feature engineering instead (a historical drift from
whenever this was split out; the two haven't been reconciled). Currently
unused by any committed script; kept around for notebook use and as a
starting point if scripts/train_models.py is ever refactored to use it.
See ml/README.md for how this fits into the wider pipeline picture.
"""

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {"date", "market", "commodity", "price"}


def load_and_clean(path) -> pd.DataFrame:
    """Load WFP price CSV, normalise column names, drop invalid rows."""
    df = pd.read_csv(path, parse_dates=["date"], dayfirst=False)
    df.columns = [c.lower().strip() for c in df.columns]

    # support WFP schema column aliases
    rename = {
        "adm1name": "region",
        "mktname":  "market",
        "cmname":   "commodity",
        "cur_name": "currency",
        "pt_name":  "pricetype",
        "mp_price": "price",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")

    df = df.dropna(subset=["price", "market", "commodity"])
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df[df["price"] > 0]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    return df.sort_values(["market", "commodity", "date"]).reset_index(drop=True)


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add year, month, quarter and cyclical month encoding."""
    df = df.copy()
    df["year"]      = df["date"].dt.year
    df["month"]     = df["date"].dt.month
    df["quarter"]   = df["date"].dt.quarter
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)
    return df


def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add lag, rolling mean, and pct-change features per market/commodity group."""
    df = df.copy()
    grp = df.groupby(["market", "commodity"])["price"]

    for lag in [1, 3, 6, 12]:
        df[f"price_lag{lag}"] = grp.shift(lag)

    for window in [3, 6, 12]:
        df[f"price_roll{window}"] = grp.transform(
            lambda x: x.shift(1).rolling(window).mean()
        )

    df["price_pct1"]  = grp.pct_change(1)
    df["price_pct12"] = grp.pct_change(12)

    return df.dropna(subset=["price_lag12"])


def engineer_all(df: pd.DataFrame) -> pd.DataFrame:
    """Full pipeline: time features → lag features."""
    df = add_time_features(df)
    df = add_lag_features(df)
    return df


FEATURE_COLUMNS = [
    "market_enc", "commodity_enc",
    "year", "month", "quarter", "month_sin", "month_cos",
    "price_lag1", "price_lag3", "price_lag6", "price_lag12",
    "price_roll3", "price_roll6", "price_roll12",
    "price_pct1", "price_pct12",
]
