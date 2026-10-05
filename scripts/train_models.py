"""
scripts/train_models.py — AgriGuard ML Model Training Pipeline
===============================================================
Trains and saves:
  1. price_forecast_model.pkl  — XGBoost regressor for crop price prediction

Usage:
    python scripts/train_models.py
    python scripts/train_models.py --data data/raw/wfp_food_prices_uga.csv
    python scripts/train_models.py --data data/raw/wfp_food_prices_uga.csv --out ml/models

Pipeline:
    load CSV → clean → feature engineering → train → evaluate → save

Evaluation metrics are printed to stdout and saved to ml/models/metrics.json.
These metrics are what you'll cite in your EPFL project write-up.
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBRegressor

# Allow `python scripts/train_models.py` (not just `python -m
# scripts.train_models`) to find backend.app.services.food_scope below —
# same bootstrap scripts/build_quant_features.py already uses.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.services.food_scope import filter_food_only  # noqa: E402

warnings.filterwarnings("ignore")

DEFAULT_DATA  = Path("data/raw/wfp_food_prices_uga.csv")
DEFAULT_OUTDIR = Path("ml/models")


# =============================================================================
# 1. DATA LOADING & CLEANING
# =============================================================================

def load_and_clean(data_path: Path) -> pd.DataFrame:
    print(f"\n📂 Loading data: {data_path}")
    df = pd.read_csv(data_path)
    df.columns = [c.lower().strip() for c in df.columns]

    print(f"   Raw rows: {len(df):,}")

    # Normalise column names (WFP CSV uses slightly different names across years)
    rename_map = {
        "cmname":    "commodity",
        "mktname":   "market",
        "admname":   "region",
        "adm1name":  "region",
        "ptname":    "pricetype",
        "um":        "unit",
        "mp_price":  "price",
    }
    df.rename(columns=rename_map, inplace=True)

    # Keep only retail prices (most consistent signal for farmers)
    if "pricetype" in df.columns:
        df = df[df["pricetype"].str.lower().str.contains("retail", na=False)]

    # Parse date
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "price", "commodity", "market"])

    # Numeric price
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df[df["price"] > 0]

    # Remove extreme outliers (> 5 std dev per commodity).
    # Deliberately NOT df.groupby("commodity").apply(...): pandas changed
    # whether the grouping column survives that call (a FutureWarning even
    # on the pinned pandas==2.3.3; pandas 3.x drops "commodity" from the
    # result outright, breaking every column reference below it). This
    # vectorized transform()-based version has no such ambiguity and is
    # markedly faster on 8k+ rows besides.
    grp_price = df.groupby("commodity")["price"]
    grp_mean = grp_price.transform("mean")
    grp_std = grp_price.transform("std")
    # std is NaN for a singleton group and 0 for a constant-price group —
    # both mean "nothing to compare against", so keep those rows as-is,
    # matching the original per-group "if std > 0 else group" behavior.
    keep = grp_std.isna() | (grp_std == 0) | ((df["price"] - grp_mean).abs() <= 5 * grp_std)
    df = df[keep].reset_index(drop=True)

    # Food-only scope (see backend/app/services/food_scope.py). Without
    # this, the trained model's own label encoder learns non-food WFP items
    # (Basin, Batteries, Soap, ...) as valid commodities — which no amount
    # of filtering in the API layer alone can undo once they're baked into
    # encoders.pkl. This is the one place that actually has to happen for
    # /api/v1/predict to stop being able to forecast a wash basin's price.
    before = len(df)
    df = filter_food_only(df, source_label="scripts/train_models.py")
    print(f"   Food-only : dropped {before - len(df):,} non-food rows")

    print(f"   Clean rows: {len(df):,}")
    print(f"   Crops     : {df['commodity'].nunique()}")
    print(f"   Markets   : {df['market'].nunique()}")
    print(f"   Date range: {df['date'].min().date()} → {df['date'].max().date()}")
    return df


# =============================================================================
# 2. FEATURE ENGINEERING
# =============================================================================

def build_features(df: pd.DataFrame):
    """
    Time-series features for XGBoost.

    Column names and lag/roll/pct windows are NOT a free choice — they must
    exactly match what backend/app/model.py::predict_price() builds at
    inference time (market_enc, commodity_enc, price_lag{1,3,6,12},
    price_roll{3,6,12}, price_pct{1,12}), since that's the only consumer of
    this model. A trained model whose training-time feature set differs
    from its inference-time feature set silently produces garbage
    predictions — XGBoost only cares about *which array position* it was
    trained on, not the column name, so a mismatch never raises, it just
    predicts wrong. See predict_price()'s _lag/_roll_mean/_pct_change
    helpers for the exact fallback semantics mirrored below.
    """
    df = df.copy()

    # Temporal features
    df["year"]    = df["date"].dt.year
    df["month"]   = df["date"].dt.month
    df["quarter"] = df["date"].dt.quarter

    # Cyclic encoding of month (captures seasonality smoothly)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)

    # Label-encode categorical columns. Names ("commodity_enc"/"market_enc")
    # and the encoder dict keys in save_artifacts() below must match
    # predict_price()'s row dict and its le_commodity/le_market lookups.
    le_crop   = LabelEncoder()
    le_market = LabelEncoder()
    df["commodity_enc"] = le_crop.fit_transform(df["commodity"].astype(str))
    df["market_enc"]    = le_market.fit_transform(df["market"].astype(str))

    df = df.sort_values(["commodity", "market", "date"]).reset_index(drop=True)
    price_by_group = df.groupby(["commodity", "market"])["price"]

    # predict_price()'s fallback for "not enough history yet" is the mean
    # price over that crop×market's *entire* available history (it has no
    # notion of a growing window — hist is the full CSV, filtered only by
    # crop+market). Matching that exactly means the fallback here is each
    # group's overall mean, not an expanding/backward-looking mean.
    group_mean = price_by_group.transform("mean")

    # price_lag{n}: the price n observations before this row within the
    # same crop×market series — mirrors hist["price"].iloc[-n] at
    # inference (the nth-most-recent known price).
    for n in (1, 3, 6, 12):
        df[f"price_lag{n}"] = price_by_group.shift(n).fillna(group_mean)

    # price_roll{n}: mean of the n observations immediately *preceding*
    # this row (shift(1) first, so the row's own price is never included)
    # — mirrors hist["price"].tail(n).mean() at inference.
    for n in (3, 6, 12):
        rolled = df.groupby(["commodity", "market"])["price"].transform(
            lambda s, n=n: s.shift(1).rolling(n, min_periods=1).mean()
        )
        df[f"price_roll{n}"] = rolled.fillna(group_mean)

    # price_pct{n}: % change from n periods before the last known price to
    # the last known price itself — mirrors _pct_change()'s
    # (hist[-1] - hist[-(n+1)]) / hist[-(n+1)], expressed here in terms of
    # this row's two preceding lags (shift(1) vs shift(1+n)).
    for n in (1, 12):
        recent = price_by_group.shift(1)
        older  = price_by_group.shift(1 + n)
        df[f"price_pct{n}"] = ((recent - older) / (older + 1e-9)).fillna(0.0)

    feature_cols = [
        "market_enc", "commodity_enc",
        "year", "month", "quarter", "month_sin", "month_cos",
        "price_lag1", "price_lag3", "price_lag6", "price_lag12",
        "price_roll3", "price_roll6", "price_roll12",
        "price_pct1", "price_pct12",
    ]

    X = df[feature_cols]
    y = df["price"]

    return X, y, le_crop, le_market, feature_cols


# =============================================================================
# 3. TRAIN PRICE FORECAST MODEL (XGBoost)
# =============================================================================

def train_price_model(X, y):
    print("\n🤖 Training XGBoost price forecast model…")

    # TRUE chronological split. X is sorted by (commodity, market, date), so a
    # positional shuffle=False split would hold out whole crops, not the most
    # recent months. Split on the calendar instead: the latest 20% of months.
    month_key = X["year"] * 12 + X["month"]
    cutoff = month_key.quantile(0.8)
    train_mask = month_key <= cutoff
    X_train, X_test = X[train_mask], X[~train_mask]
    y_train, y_test = y[train_mask], y[~train_mask]

    model = XGBRegressor(
        n_estimators=400,
        learning_rate=0.05,
        max_depth=6,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbosity=0,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    preds = model.predict(X_test)

    mae  = mean_absolute_error(y_test, preds)
    rmse = mean_squared_error(y_test, preds) ** 0.5
    r2   = r2_score(y_test, preds)
    mape = np.mean(np.abs((y_test - preds) / y_test.clip(lower=1))) * 100

    # Naive baseline: "next price = last observed price" (price_lag1).
    naive = X_test["price_lag1"]
    naive_mae = mean_absolute_error(y_test, naive)
    naive_mape = np.mean(np.abs((y_test - naive) / y_test.clip(lower=1))) * 100
    print(f"   Naive (last price) MAE: {naive_mae:,.2f} UGX | MAPE: {naive_mape:.2f}%")

    print(f"   MAE  : {mae:,.2f} UGX")
    print(f"   RMSE : {rmse:,.2f} UGX")
    print(f"   MAPE : {mape:.2f}%")
    print(f"   R²   : {r2:.4f}")

    metrics = {
        "model":      "XGBoostRegressor",
        "n_train":    int(len(X_train)),
        "n_test":     int(len(X_test)),
        "MAE_UGX":    round(mae, 2),
        "RMSE_UGX":   round(rmse, 2),
        "MAPE_pct":   round(mape, 2),
        "R2":         round(r2, 4),
        "split":      "chronological (latest 20% of months held out)",
        "naive_MAE_UGX":  round(naive_mae, 2),
        "naive_MAPE_pct": round(naive_mape, 2),
    }
    return model, metrics


# =============================================================================
# 4. SAVE ARTIFACTS
# =============================================================================

def save_artifacts(outdir: Path, price_model, le_crop, le_market,
                   feature_cols, price_metrics):
    outdir.mkdir(parents=True, exist_ok=True)

    price_path = outdir / "price_forecast_model.pkl"
    meta_path  = outdir / "metrics.json"

    joblib.dump(price_model, price_path, compress=3)

    # Keys here ("market", "commodity", "price_features") must match
    # backend/app/model.py::predict_price()'s _encoders.get(...) calls
    # exactly — that's the only code that reads this file.
    joblib.dump({"market": le_market, "commodity": le_crop,
                 "price_features": feature_cols},
                outdir / "encoders.pkl", compress=3)

    all_metrics = {
        "price_forecast": price_metrics,
    }
    with open(meta_path, "w") as f:
        json.dump(all_metrics, f, indent=2)
    # ml/models/ is gitignored; keep a committed, reviewable copy of the numbers
    # quoted in the README / pitch next to the code that produced them.
    committed = outdir.parent / "metrics.json"
    with open(committed, "w") as f:
        json.dump(all_metrics, f, indent=2)

    print(f"\n✅ Saved:")
    print(f"   {price_path}  ({price_path.stat().st_size / 1024:.0f} KB)")
    print(f"   {meta_path}")


# =============================================================================
# MAIN
# =============================================================================

def main(data_path: Path, outdir: Path):
    print("=" * 55)
    print("  AgriGuard — ML Model Training Pipeline")
    print("=" * 55)

    if not data_path.exists():
        print(f"\n❌ Data file not found: {data_path}")
        print("   Run first:  python scripts/download_wfp_data.py")
        raise SystemExit(1)

    df = load_and_clean(data_path)
    X, y, le_crop, le_market, feature_cols = build_features(df)

    price_model, price_metrics = train_price_model(X, y)

    save_artifacts(
        outdir, price_model,
        le_crop, le_market, feature_cols,
        price_metrics,
    )

    print("\n🎉 Training complete. Models ready for inference.")
    print(f"   MAPE: {price_metrics['MAPE_pct']}%  |  R²: {price_metrics['R2']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train AgriGuard ML models")
    parser.add_argument("--data", "-d", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out",  "-o", type=Path, default=DEFAULT_OUTDIR)
    args = parser.parse_args()
    main(args.data, args.out)
