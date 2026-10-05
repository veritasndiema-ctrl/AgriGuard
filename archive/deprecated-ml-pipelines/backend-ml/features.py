"""
Feature engineering for AgriGuard's price forecasting pipeline.

Why these features (matches the ML Methodology section of the README):
- Crop prices are seasonal (harvest cycles), so cyclic month encoding
  lets the model learn "hunger season" price spikes without treating
  December and January as numerically far apart.
- Lag prices (1/3/6 months) give the model recent momentum -- prices
  drift from where they were, they don't jump randomly.
- A 3-month rolling average smooths out single-week noise or
  reporting errors in the raw WFP data.
- Crop and market are label-encoded because XGBoost needs numeric
  inputs, but their *identity* (which crop, which market) matters
  more than any ordering, so no ordinal meaning is implied.
"""
import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder


def add_temporal_features(df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df["year"] = df[date_col].dt.year
    df["month"] = df[date_col].dt.month
    df["week"] = df[date_col].dt.isocalendar().week.astype(int)
    df["day_of_year"] = df[date_col].dt.dayofyear
    # Cyclic encoding: sin/cos pair means month 12 and month 1 are
    # numerically close, matching how seasons actually wrap around.
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)
    return df


def add_lag_features(
    df: pd.DataFrame,
    price_col: str = "price",
    group_cols: tuple = ("crop", "market"),
    lags: tuple = (1, 3, 6),
) -> pd.DataFrame:
    df = df.sort_values(list(group_cols) + ["date"]).copy()
    for lag in lags:
        df[f"price_lag_{lag}m"] = df.groupby(list(group_cols))[price_col].shift(lag)
    return df


def add_rolling_features(
    df: pd.DataFrame,
    price_col: str = "price",
    group_cols: tuple = ("crop", "market"),
    window: int = 3,
) -> pd.DataFrame:
    df = df.copy()
    # shift(1) first so the rolling average never includes the row's
    # own (future-relative-to-training) price -- avoids leakage.
    df[f"price_roll_{window}m_avg"] = df.groupby(list(group_cols))[price_col].transform(
        lambda s: s.shift(1).rolling(window, min_periods=1).mean()
    )
    return df


def encode_categoricals(df: pd.DataFrame, cols: tuple = ("crop", "market")):
    df = df.copy()
    encoders = {}
    for col in cols:
        le = LabelEncoder()
        df[f"{col}_encoded"] = le.fit_transform(df[col].astype(str))
        encoders[col] = le
    return df, encoders


def build_feature_matrix(raw_df: pd.DataFrame):
    """
    Full pipeline: raw WFP price rows -> model-ready feature matrix.

    Rows with NaN lag/rolling values (the first 1-6 months of each
    crop/market series, before enough history exists) are dropped --
    XGBoost can't learn momentum from a feature that doesn't exist yet.
    """
    df = add_temporal_features(raw_df)
    df = add_lag_features(df)
    df = add_rolling_features(df)
    df, encoders = encode_categoricals(df)
    lag_roll_cols = [c for c in df.columns if "lag" in c or "roll" in c]
    df = df.dropna(subset=lag_roll_cols)
    return df, encoders
