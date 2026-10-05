"""
ml/training/train_forecast.py — AgriGuard Price Forecasting Model
==================================================================
Trains a machine learning model to predict crop prices 14 days ahead.

Approach: Gradient Boosted Trees (XGBoost)
Why XGBoost over deep learning for this MVP?
  - Works well with tabular data (our price + weather features)
  - Handles missing data natively (weather gaps, market reporting gaps)
  - Trains in seconds on a laptop — no GPU needed
  - Produces feature importance scores MAAIF can understand
  - Robust to outliers (price shocks don't break the model)
  - Proven in agricultural price forecasting literature

Feature engineering:
  - Lag features: prices from 7, 14, 21, 30 days ago
  - Rolling statistics: 7-day and 30-day moving averages and std dev
  - Seasonal features: month, week of year, Uganda season indicator
  - Weather features: rainfall, temperature, water balance (from fetch_weather.py)
  - Market features: region encoding, market size proxy

Target variable:
  - retail_price_ugx 14 days into the future
  - One model per crop (better accuracy than a single multi-crop model)

Output:
  - Trained model saved to ml/saved_models/{crop_name}_forecast_v{N}.pkl
  - Evaluation metrics saved to ml/evaluation/{crop_name}_metrics.json
  - Feature importance chart saved to ml/evaluation/{crop_name}_features.png

Run:
    cd backend
    python ml/training/train_forecast.py                    # all crops
    python ml/training/train_forecast.py --crop Maize       # single crop
    python ml/training/train_forecast.py --horizon 7        # 7-day forecast
    python ml/training/train_forecast.py --evaluate-only    # metrics only

Author: AgriGuard Team
"""

import argparse
import json
import pickle
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error
from sklearn.model_selection import TimeSeriesSplit
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

warnings.filterwarnings("ignore")   # Suppress sklearn/xgboost verbose warnings

# Add backend/ to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Fixed: was `from app.config` / `from app.models.price` — a nonexistent
# top-level `app` package (see README "Known issues"; the real root is
# `backend.app`, matching how this repo actually runs — see
# backend/Dockerfile / docker-compose.yml). WeatherReading now also exists
# (backend/app/models/weather.py) — until both fixes, this script's whole
# weather-feature branch (load_weather_data / build_features) was dead code.
from backend.app.core.config import settings
from backend.app.models.price import Crop, CropPrice, DataQuality, Market
from backend.app.models.weather import WeatherReading

# =============================================================================
# PATHS
# =============================================================================

MODELS_DIR     = Path("ml/saved_models")
EVALUATION_DIR = Path("ml/evaluation")
MODELS_DIR.mkdir(parents=True, exist_ok=True)
EVALUATION_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# MODEL CONFIGURATION
# =============================================================================

# XGBoost hyperparameters — tuned for Uganda agricultural price data
# These are solid defaults; run a GridSearch after the MVP for gains
XGBOOST_PARAMS = {
    "n_estimators":      300,      # Number of trees
    "max_depth":         5,        # Tree depth — prevents overfitting
    "learning_rate":     0.05,     # Shrinkage — lower = more robust
    "subsample":         0.8,      # Row sampling per tree
    "colsample_bytree":  0.8,      # Feature sampling per tree
    "min_child_weight":  5,        # Min samples in a leaf node
    "reg_alpha":         0.1,      # L1 regularisation
    "reg_lambda":        1.0,      # L2 regularisation
    "random_state":      42,
    "n_jobs":            -1,       # Use all CPU cores
    "verbosity":         0,        # Silent
}

# Forecast horizon in days
DEFAULT_HORIZON = 14

# Minimum training samples required (won't train on thin data)
MIN_TRAINING_SAMPLES = 100

# Time series cross-validation splits
CV_SPLITS = 5


# =============================================================================
# DATA LOADING
# =============================================================================

def load_price_data(db, crop_id: int) -> pd.DataFrame:
    """
    Loads all non-flagged price records for a crop from the DB.
    Averages across markets per day to create a national-level series,
    then also keeps per-market series for market-specific forecasting.

    Returns a DataFrame with columns:
        price_date, market_id, market_name, region,
        retail_price_ugx, wholesale_price_ugx
    """
    rows = (
        db.query(
            CropPrice.price_date,
            CropPrice.market_id,
            CropPrice.retail_price_ugx,
            CropPrice.wholesale_price_ugx,
            Market.name.label("market_name"),
            Market.region.label("region"),
        )
        .join(Market, CropPrice.market_id == Market.id)
        .filter(
            CropPrice.crop_id == crop_id,
            CropPrice.retail_price_ugx.isnot(None),
            CropPrice.quality != DataQuality.FLAGGED,
        )
        .order_by(CropPrice.price_date, CropPrice.market_id)
        .all()
    )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "price_date", "market_id", "retail_price_ugx",
        "wholesale_price_ugx", "market_name", "region"
    ])
    df["price_date"] = pd.to_datetime(df["price_date"])
    df["retail_price_ugx"] = df["retail_price_ugx"].astype(float)
    df["wholesale_price_ugx"] = df["wholesale_price_ugx"].astype(float)

    return df


def load_weather_data(db) -> pd.DataFrame:
    """
    Loads historical weather readings from the DB.
    Averages across all markets to create national-level weather features.
    Per-market weather is joined later when building market-specific features.

    Returns DataFrame with daily weather columns.
    """
    rows = (
        db.query(
            WeatherReading.reading_date,
            WeatherReading.market_id,
            WeatherReading.temp_max_c,
            WeatherReading.temp_min_c,
            WeatherReading.rainfall_mm,
            WeatherReading.et0_evapotranspiration_mm,
            WeatherReading.water_balance_mm,
            WeatherReading.humidity_max_pct,
        )
        .filter(WeatherReading.is_forecast == False)
        .order_by(WeatherReading.reading_date)
        .all()
    )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "reading_date", "market_id", "temp_max_c", "temp_min_c",
        "rainfall_mm", "et0_evapotranspiration_mm",
        "water_balance_mm", "humidity_max_pct"
    ])
    df["reading_date"] = pd.to_datetime(df["reading_date"])
    return df


# =============================================================================
# FEATURE ENGINEERING
# The quality of these features determines model accuracy more than
# the choice of algorithm. Spend time here for real improvements.
# =============================================================================

def build_features(
    price_df: pd.DataFrame,
    weather_df: pd.DataFrame,
    horizon: int = DEFAULT_HORIZON,
    market_id: int = None,
) -> pd.DataFrame:
    """
    Transforms raw price and weather data into an ML feature matrix.

    Args:
        price_df:   Raw price data from load_price_data()
        weather_df: Raw weather data from load_weather_data()
        horizon:    Days ahead to forecast (target shift)
        market_id:  If provided, build features for one market only

    Returns:
        DataFrame where each row is a training sample:
        - Features: lag prices, rolling stats, seasonality, weather
        - Target:   retail_price_ugx shifted -horizon days (future price)

    Feature groups:
        1. Lag price features  — what were prices N days ago?
        2. Rolling statistics  — recent trend and volatility
        3. Calendar features   — month, week, season indicator
        4. Weather features    — rainfall, temperature, water stress
        5. Market features     — regional encoding
    """
    # Filter to specific market if requested
    if market_id:
        df = price_df[price_df["market_id"] == market_id].copy()
    else:
        # National average: mean price across all markets per day
        df = (
            price_df.groupby("price_date")
            .agg(
                retail_price_ugx=("retail_price_ugx", "mean"),
                wholesale_price_ugx=("wholesale_price_ugx", "mean"),
            )
            .reset_index()
        )

    # Sort by date and set as index
    df = df.sort_values("price_date").set_index("price_date")

    # Fill missing dates (markets don't report every day)
    # Forward-fill short gaps (<= 3 days); longer gaps stay NaN
    full_range = pd.date_range(df.index.min(), df.index.max(), freq="D")
    df = df.reindex(full_range)
    df["retail_price_ugx"] = df["retail_price_ugx"].fillna(method="ffill", limit=3)

    # -------------------------------------------------------------------------
    # GROUP 1: Lag Price Features
    # "What was the price N days ago?" — the most predictive features
    # -------------------------------------------------------------------------
    for lag in [1, 3, 7, 14, 21, 28, 30]:
        df[f"lag_{lag}d"] = df["retail_price_ugx"].shift(lag)

    # Price momentum: difference between recent and older price
    # Positive = prices rising, Negative = falling
    df["momentum_7d"]  = df["lag_7d"]  - df["lag_14d"]
    df["momentum_14d"] = df["lag_14d"] - df["lag_28d"]

    # Percentage change vs last week and last month
    df["pct_change_7d"]  = (df["lag_1d"] - df["lag_7d"])  / df["lag_7d"]  * 100
    df["pct_change_30d"] = (df["lag_1d"] - df["lag_30d"]) / df["lag_30d"] * 100

    # -------------------------------------------------------------------------
    # GROUP 2: Rolling Statistics
    # Capture the recent trend and volatility
    # -------------------------------------------------------------------------
    df["rolling_mean_7d"]  = df["retail_price_ugx"].shift(1).rolling(7).mean()
    df["rolling_mean_30d"] = df["retail_price_ugx"].shift(1).rolling(30).mean()
    df["rolling_std_7d"]   = df["retail_price_ugx"].shift(1).rolling(7).std()
    df["rolling_std_30d"]  = df["retail_price_ugx"].shift(1).rolling(30).std()

    # Price relative to its own 30-day average (detects deviation from norm)
    df["price_vs_30d_avg"] = df["lag_1d"] / df["rolling_mean_30d"]

    # Coefficient of variation: std/mean — measures recent price stability
    df["cv_30d"] = df["rolling_std_30d"] / df["rolling_mean_30d"]

    # -------------------------------------------------------------------------
    # GROUP 3: Calendar / Seasonal Features
    # Uganda has strong bimodal seasonality — these features are critical
    # -------------------------------------------------------------------------
    df["month"]       = df.index.month
    df["week_of_year"] = df.index.isocalendar().week.astype(int)
    df["day_of_year"] = df.index.dayofyear

    # Sine/cosine encoding of month — captures cyclical nature
    # Better than raw month number (December→January is continuous, not 12→1 jump)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)
    df["week_sin"]  = np.sin(2 * np.pi * df["week_of_year"] / 52)
    df["week_cos"]  = np.cos(2 * np.pi * df["week_of_year"] / 52)

    # Uganda season indicator:
    # 0 = lean (Jan-Feb, Jun-Jul)
    # 1 = Season A harvest (Apr-May)
    # 2 = Season B harvest (Sep-Nov)
    # 3 = planting (Mar, Aug)
    def get_season(month):
        if month in [1, 2]:   return 0   # Lean season
        if month in [6, 7]:   return 0   # Mid-year lean
        if month in [4, 5]:   return 1   # Season A harvest
        if month in [9, 10, 11]: return 2  # Season B harvest
        return 3                          # Planting / transition

    df["uganda_season"] = df["month"].apply(get_season)

    # Days until next harvest (approximate) — creates urgency signal
    # Season A: May 15, Season B: October 15
    def days_to_harvest(dt):
        year = dt.year
        season_a = pd.Timestamp(year, 5, 15)
        season_b = pd.Timestamp(year, 10, 15)
        candidates = [season_a, season_b,
                      season_a.replace(year=year+1),
                      season_b.replace(year=year+1)]
        future = [c for c in candidates if c > dt]
        return (min(future) - dt).days if future else 180

    df["days_to_harvest"] = [
        days_to_harvest(dt) for dt in df.index
    ]

    # -------------------------------------------------------------------------
    # GROUP 4: Weather Features
    # Join national-average weather by date
    # Weather 14-21 days ago predicts current crop stress → future price
    # -------------------------------------------------------------------------
    if not weather_df.empty:
        # National average weather per day
        weather_avg = (
            weather_df.groupby("reading_date")
            .agg(
                temp_max_c=("temp_max_c", "mean"),
                rainfall_mm=("rainfall_mm", "mean"),
                water_balance_mm=("water_balance_mm", "mean"),
                humidity_max_pct=("humidity_max_pct", "mean"),
            )
            .reset_index()
            .rename(columns={"reading_date": "date"})
            .set_index("date")
        )

        # Merge weather into feature matrix
        df = df.join(weather_avg, how="left")

        # Lag weather features — weather 2-3 weeks ago affects this week's prices
        for lag in [7, 14, 21]:
            df[f"rain_lag_{lag}d"]          = df["rainfall_mm"].shift(lag)
            df[f"water_balance_lag_{lag}d"] = df["water_balance_mm"].shift(lag)

        # Rolling weather stats — 30-day drought/flood indicator
        df["rain_30d_total"]   = df["rainfall_mm"].shift(1).rolling(30).sum()
        df["drought_days_30d"] = (
            df["water_balance_mm"].shift(1).rolling(30)
            .apply(lambda x: (x < -3).sum())  # Days with water deficit > 3mm
        )

        # Drop raw weather (we use lagged versions instead)
        df.drop(
            columns=["temp_max_c", "rainfall_mm",
                     "water_balance_mm", "humidity_max_pct"],
            errors="ignore",
            inplace=True,
        )

    # -------------------------------------------------------------------------
    # TARGET VARIABLE: Price 'horizon' days in the future
    # This is what the model is trained to predict
    # -------------------------------------------------------------------------
    df["target"] = df["retail_price_ugx"].shift(-horizon)

    # Drop rows where target is NaN (the last 'horizon' days have no future price)
    # Also drop rows where lag features are NaN (first 30 days)
    df.dropna(subset=["target", "lag_30d"], inplace=True)

    return df


def get_feature_columns(df: pd.DataFrame) -> list[str]:
    """
    Returns the list of feature column names (everything except target
    and raw price columns).
    """
    exclude = {
        "target",
        "retail_price_ugx",
        "wholesale_price_ugx",
        "market_name",
        "region",
        "market_id",
        "month",        # Encoded as sin/cos instead
        "week_of_year", # Encoded as sin/cos instead
    }
    return [c for c in df.columns if c not in exclude]


# =============================================================================
# MODEL TRAINING
# =============================================================================

def train_model(
    features_df: pd.DataFrame,
    horizon: int = DEFAULT_HORIZON,
) -> tuple:
    """
    Trains an XGBoost model using time-series cross-validation.

    Time-series CV is different from random CV:
    - We must always train on PAST data and test on FUTURE data
    - Random splits would let the model "see" future prices during training
      which inflates accuracy scores (data leakage)
    - TimeSeriesSplit ensures the train/test boundary always moves forward

    Args:
        features_df: Feature matrix from build_features()
        horizon:     Forecast horizon (used for labelling only)

    Returns:
        (trained_model, cv_metrics, feature_importances)
    """
    try:
        from xgboost import XGBRegressor
    except ImportError:
        print("  Installing xgboost...")
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "xgboost", "--quiet"]
        )
        from xgboost import XGBRegressor

    feature_cols = get_feature_columns(features_df)
    X = features_df[feature_cols].values
    y = features_df["target"].values

    if len(X) < MIN_TRAINING_SAMPLES:
        raise ValueError(
            f"Not enough training samples: {len(X)} < {MIN_TRAINING_SAMPLES}. "
            "Run seed_prices.py --days 365 first."
        )

    # -------------------------------------------------------------------------
    # Time Series Cross-Validation
    # Evaluates model on multiple train/test splits
    # -------------------------------------------------------------------------
    tscv = TimeSeriesSplit(n_splits=CV_SPLITS)
    cv_maes  = []   # Mean Absolute Error per fold
    cv_mapes = []   # Mean Absolute Percentage Error per fold

    print(f"  Running {CV_SPLITS}-fold time series cross-validation...")

    for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model = XGBRegressor(**XGBOOST_PARAMS)
        model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            verbose=False,
        )

        preds = model.predict(X_test)

        mae  = mean_absolute_error(y_test, preds)
        mape = mean_absolute_percentage_error(y_test, preds) * 100

        cv_maes.append(mae)
        cv_mapes.append(mape)
        print(f"    Fold {fold+1}: MAE = UGX {mae:,.0f} | MAPE = {mape:.1f}%")

    print(f"\n  CV Summary:")
    print(f"    Mean MAE  : UGX {np.mean(cv_maes):,.0f} ± {np.std(cv_maes):,.0f}")
    print(f"    Mean MAPE : {np.mean(cv_mapes):.1f}% ± {np.std(cv_mapes):.1f}%")

    # -------------------------------------------------------------------------
    # Final model: train on ALL data
    # -------------------------------------------------------------------------
    print("\n  Training final model on full dataset...")
    final_model = XGBRegressor(**XGBOOST_PARAMS)
    final_model.fit(X, y, verbose=False)

    # Feature importances — MAAIF loves knowing what drives prices
    importances = dict(zip(feature_cols, final_model.feature_importances_))
    importances = dict(
        sorted(importances.items(), key=lambda x: x[1], reverse=True)
    )

    cv_metrics = {
        "horizon_days":  horizon,
        "n_samples":     len(X),
        "n_features":    len(feature_cols),
        "cv_splits":     CV_SPLITS,
        "mean_mae_ugx":  round(float(np.mean(cv_maes)), 2),
        "std_mae_ugx":   round(float(np.std(cv_maes)), 2),
        "mean_mape_pct": round(float(np.mean(cv_mapes)), 2),
        "std_mape_pct":  round(float(np.std(cv_mapes)), 2),
        # Confidence score: 1 - MAPE (capped 0-1)
        # e.g. 12% MAPE → 0.88 confidence
        "confidence_score": round(max(0.0, min(1.0, 1 - np.mean(cv_mapes) / 100)), 3),
    }

    return final_model, cv_metrics, importances, feature_cols


# =============================================================================
# FORECASTING: GENERATE PREDICTIONS
# =============================================================================

def generate_forecasts(
    model,
    features_df: pd.DataFrame,
    feature_cols: list[str],
    horizon: int = DEFAULT_HORIZON,
    n_days: int = 14,
) -> pd.DataFrame:
    """
    Uses the trained model to generate price forecasts for the next n_days.

    For each future day, we need to construct a feature vector.
    We use the most recent available data + rolling forward.

    Args:
        model:        Trained XGBoost model
        features_df:  Feature matrix (we use the last row as the base)
        feature_cols: Feature column names (must match training)
        horizon:      Forecast horizon the model was trained on
        n_days:       How many daily forecasts to generate

    Returns:
        DataFrame with columns: forecast_date, predicted_price_ugx,
        lower_bound_ugx, upper_bound_ugx, confidence_score
    """
    # Get the last known feature vector as starting point
    last_row = features_df[feature_cols].iloc[-1].copy()
    last_price = float(features_df["retail_price_ugx"].iloc[-1])
    last_date = features_df.index[-1]

    forecasts = []

    for day_offset in range(1, n_days + 1):
        forecast_date = last_date + timedelta(days=day_offset + horizon - 1)

        # Update calendar features for the forecast date
        fd = pd.Timestamp(forecast_date)
        last_row["month_sin"] = np.sin(2 * np.pi * fd.month / 12)
        last_row["month_cos"] = np.cos(2 * np.pi * fd.month / 12)
        last_row["week_sin"]  = np.sin(2 * np.pi * fd.isocalendar()[1] / 52)
        last_row["week_cos"]  = np.cos(2 * np.pi * fd.isocalendar()[1] / 52)
        last_row["day_of_year"] = fd.dayofyear

        uganda_season_map = {
            1: 0, 2: 0, 3: 3, 4: 1, 5: 1, 6: 0,
            7: 0, 8: 3, 9: 2, 10: 2, 11: 2, 12: 3
        }
        last_row["uganda_season"] = uganda_season_map.get(fd.month, 3)

        # Point forecast
        X_pred = last_row.values.reshape(1, -1)
        predicted = float(model.predict(X_pred)[0])

        # Confidence interval: ±10% of prediction as a simple proxy
        # In production, use quantile regression or conformal prediction
        margin = predicted * 0.10
        lower  = max(0, predicted - margin)
        upper  = predicted + margin

        # Round to nearest 100 UGX (market convention)
        forecasts.append({
            "forecast_date":        forecast_date.date(),
            "predicted_price_ugx":  round(predicted / 100) * 100,
            "lower_bound_ugx":      round(lower / 100) * 100,
            "upper_bound_ugx":      round(upper / 100) * 100,
        })

    return pd.DataFrame(forecasts)


# =============================================================================
# SAVE & LOAD ARTIFACTS
# =============================================================================

def save_model(model, crop_name: str, feature_cols: list, metrics: dict) -> Path:
    """
    Saves the trained model as a pickle file with metadata.
    Filename includes version number so old models are preserved.

    Returns the path to the saved model file.
    """
    # Find the next version number
    existing = list(MODELS_DIR.glob(f"{crop_name.lower()}_forecast_v*.pkl"))
    version = len(existing) + 1

    model_path = MODELS_DIR / f"{crop_name.lower()}_forecast_v{version}.pkl"

    payload = {
        "model":         model,
        "feature_cols":  feature_cols,
        "metrics":       metrics,
        "crop_name":     crop_name,
        "trained_at":    date.today().isoformat(),
        "horizon_days":  metrics["horizon_days"],
    }

    with open(model_path, "wb") as f:
        pickle.dump(payload, f)

    # Also save as "latest" — what the API loads by default
    latest_path = MODELS_DIR / f"{crop_name.lower()}_forecast_latest.pkl"
    with open(latest_path, "wb") as f:
        pickle.dump(payload, f)

    print(f"  ✓ Model saved: {model_path}")
    print(f"  ✓ Latest link: {latest_path}")
    return model_path


def save_metrics(metrics: dict, importances: dict, crop_name: str) -> None:
    """
    Saves evaluation metrics and feature importances as JSON.
    Used for tracking model performance over time and MAAIF reporting.
    """
    output = {
        "crop": crop_name,
        "metrics": metrics,
        "top_10_features": dict(list(importances.items())[:10]),
    }

    metrics_path = EVALUATION_DIR / f"{crop_name.lower()}_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"  ✓ Metrics saved: {metrics_path}")


def save_feature_importance_chart(
    importances: dict, crop_name: str
) -> None:
    """
    Saves a horizontal bar chart of feature importances.
    Include this in the MAAIF presentation — it's the most intuitive
    way to show what drives crop prices.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("  ⚠ matplotlib not installed — skipping chart")
        return

    top_n = 15
    top_features = dict(list(importances.items())[:top_n])

    fig, ax = plt.subplots(figsize=(10, 6))

    features = list(top_features.keys())[::-1]
    values   = list(top_features.values())[::-1]

    bars = ax.barh(features, values, color="#2d7d46", alpha=0.85)

    # Label the bars
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_width() + 0.002, bar.get_y() + bar.get_height() / 2,
            f"{val:.3f}", va="center", ha="left", fontsize=8,
        )

    ax.set_xlabel("Feature Importance (XGBoost gain)", fontsize=11)
    ax.set_title(
        f"AgriGuard — {crop_name} Price Forecast\nTop {top_n} Predictive Features",
        fontsize=13, fontweight="bold",
    )
    ax.set_xlim(0, max(values) * 1.15)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Add a note about what drives prices
    ax.text(
        0.98, 0.02,
        "Lag features = recent price history\n"
        "Rolling means = trend direction\n"
        "Drought days = weather stress signal",
        transform=ax.transAxes, ha="right", va="bottom",
        fontsize=8, color="#555555",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#f0f0f0", alpha=0.7),
    )

    plt.tight_layout()
    chart_path = EVALUATION_DIR / f"{crop_name.lower()}_features.png"
    plt.savefig(chart_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✓ Feature chart: {chart_path}")


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def train_crop_model(
    db,
    crop: Crop,
    weather_df: pd.DataFrame,
    horizon: int = DEFAULT_HORIZON,
) -> dict:
    """
    Full training pipeline for a single crop.

    Steps:
      1. Load price data for this crop
      2. Build feature matrix (lag features, weather, seasonality)
      3. Train XGBoost model with cross-validation
      4. Save model + metrics + feature chart
      5. Return evaluation metrics

    Args:
        db:         SQLAlchemy session
        crop:       Crop ORM object
        weather_df: Pre-loaded weather DataFrame (shared across crops)
        horizon:    Forecast horizon in days

    Returns:
        Dict of evaluation metrics
    """
    print(f"\n{'='*55}")
    print(f"  Training: {crop.name} ({horizon}-day forecast)")
    print(f"{'='*55}")

    # 1. Load prices
    price_df = load_price_data(db, crop.id)
    if price_df.empty:
        print(f"  ⚠ No price data found for {crop.name}. Skipping.")
        return {}

    print(f"  Price records : {len(price_df):,}")
    print(f"  Date range    : {price_df['price_date'].min().date()} → "
          f"{price_df['price_date'].max().date()}")

    # 2. Build features (national average model)
    features_df = build_features(price_df, weather_df, horizon=horizon)

    if len(features_df) < MIN_TRAINING_SAMPLES:
        print(f"  ⚠ Only {len(features_df)} samples after feature engineering. "
              f"Need {MIN_TRAINING_SAMPLES}. Run seed_prices.py --days 365.")
        return {}

    print(f"  Training samples : {len(features_df):,}")
    print(f"  Features         : {len(get_feature_columns(features_df))}")

    # 3. Train
    model, metrics, importances, feature_cols = train_model(features_df, horizon)

    # 4. Save artifacts
    save_model(model, crop.name, feature_cols, metrics)
    save_metrics(metrics, importances, crop.name)
    save_feature_importance_chart(importances, crop.name)

    # 5. Quick sanity check: show sample forecast
    print(f"\n  Sample {horizon}-day forecast:")
    forecast_df = generate_forecasts(model, features_df, feature_cols, horizon)
    latest_price = float(price_df["retail_price_ugx"].iloc[-1])
    print(f"    Latest actual price : UGX {latest_price:,.0f}")
    for _, row in forecast_df.head(3).iterrows():
        print(
            f"    {row['forecast_date']} → "
            f"UGX {row['predicted_price_ugx']:,.0f} "
            f"[{row['lower_bound_ugx']:,.0f} – {row['upper_bound_ugx']:,.0f}]"
        )

    # Confidence interpretation for MAAIF
    confidence = metrics["confidence_score"]
    label = "High ✓" if confidence >= 0.85 else "Medium ⚠" if confidence >= 0.65 else "Low ✗"
    print(f"\n  Confidence : {confidence:.2f} ({label})")
    print(f"  MAPE       : {metrics['mean_mape_pct']:.1f}% average error")

    return metrics


def main(crop_name: str = None, horizon: int = DEFAULT_HORIZON):
    """
    Entry point. Trains models for all crops or a single specified crop.
    """
    print(f"\n🌿 AgriGuard ML Training Pipeline")
    print(f"   Horizon    : {horizon} days")
    print(f"   Database   : {settings.db_host}/{settings.db_name}\n")

    # Connect to DB
    db_engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=db_engine)
    db = Session()

    try:
        # Load weather once — shared across all crop models
        print("📡 Loading weather data...")
        weather_df = load_weather_data(db)
        if weather_df.empty:
            print("  ⚠ No weather data found.")
            print("  Run: python scripts/fetch_weather.py first.")
            print("  Continuing without weather features...\n")

        # Get crops to train
        crop_query = db.query(Crop).filter(Crop.is_active == True)
        if crop_name:
            crop_query = crop_query.filter(Crop.name.ilike(f"%{crop_name}%"))

        crops = crop_query.all()

        if not crops:
            print(f"❌ No crops found matching '{crop_name}'")
            return

        print(f"🌱 Training models for {len(crops)} crop(s):\n"
              f"   {', '.join(c.name for c in crops)}\n")

        all_metrics = {}

        for crop in crops:
            try:
                metrics = train_crop_model(db, crop, weather_df, horizon)
                if metrics:
                    all_metrics[crop.name] = metrics
            except Exception as e:
                print(f"\n  ❌ Failed to train {crop.name}: {e}")
                continue

        # Final summary
        print(f"\n\n{'='*55}")
        print(f"  TRAINING COMPLETE — {len(all_metrics)} models trained")
        print(f"{'='*55}")
        print(f"  {'Crop':<18} {'MAPE':>8} {'MAE (UGX)':>12} {'Confidence':>12}")
        print(f"  {'-'*50}")
        for name, m in all_metrics.items():
            print(
                f"  {name:<18} "
                f"{m['mean_mape_pct']:>7.1f}% "
                f"{m['mean_mae_ugx']:>12,.0f} "
                f"{m['confidence_score']:>12.2f}"
            )
        print(f"\n  Models saved to : {MODELS_DIR}/")
        print(f"  Metrics saved to: {EVALUATION_DIR}/")
        print(f"\n Ready! Start the API to serve forecasts:")
        print(f"   uvicorn app.main:app --reload\n")

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AgriGuard: Train crop price forecasting models"
    )
    parser.add_argument(
        "--crop",
        type=str,
        default=None,
        help="Train a specific crop only e.g. --crop Maize",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=DEFAULT_HORIZON,
        help=f"Forecast horizon in days (default: {DEFAULT_HORIZON})",
    )
    args = parser.parse_args()
    main(crop_name=args.crop, horizon=args.horizon)