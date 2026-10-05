"""
Backtests a saved price forecast model against the most recent slice
of price history and prints an error report -- for checking a model
hasn't drifted before it's promoted into backend/ml/models/ for the
API to serve.

Run from repo root:
    python -m ml.evaluation.evaluate_forecasts --weeks 8
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.ml.features import build_feature_matrix  # noqa: E402
from backend.ml.price_forecast_model import FEATURE_COLUMNS, PriceForecastModel  # noqa: E402

DATA_PATH = REPO_ROOT / "data" / "raw" / "wfp_food_prices_uga.csv"
MODEL_PATH = REPO_ROOT / "ml" / "models" / "price_forecast_xgb.pkl"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--weeks", type=int, default=8, help="how many of the most recent weeks to backtest against"
    )
    parser.add_argument("--model", type=Path, default=MODEL_PATH)
    args = parser.parse_args()

    if not args.model.exists():
        raise FileNotFoundError(f"{args.model} not found -- train a model first")

    raw_df = pd.read_csv(DATA_PATH, parse_dates=["date"])
    feature_df, _ = build_feature_matrix(raw_df)
    feature_df = feature_df.sort_values("date")

    holdout = feature_df.groupby(["crop", "market"]).tail(args.weeks)

    model = PriceForecastModel.load(args.model)
    preds = model.model.predict(holdout[FEATURE_COLUMNS])
    actual = holdout["price"]

    print(f"Backtest over last {args.weeks} weeks per crop/market ({len(holdout)} rows)")
    print(f"MAE:  {mean_absolute_error(actual, preds):.1f}")
    print(f"MAPE: {mean_absolute_percentage_error(actual, preds):.3f}")
    print(f"R2:   {r2_score(actual, preds):.3f}")

    # Per-crop breakdown surfaces crops the model is quietly bad at,
    # which the single aggregate MAE above would hide.
    holdout = holdout.assign(pred=preds, abs_err=(holdout["price"] - preds).abs())
    per_crop = holdout.groupby("crop")["abs_err"].mean().sort_values(ascending=False)
    print("\nMean absolute error by crop:")
    print(per_crop.to_string())


if __name__ == "__main__":
    main()