"""
CLI entry point: trains the price forecaster from the WFP CSV, saves
.pkl artifacts to ml/models/, and writes ml/models/metrics.json.

STATUS: not currently wired into the running API. This was written to
replace scripts/train_models.py (same data in, cleaner code living next
to the model class it trains), but backend/app/model.py -- the module the
FastAPI app actually loads its model from at request time -- still reads
scripts/train_models.py's output artifacts (ml/models/price_forecast_model.pkl
+ encoders.pkl), not this file's (ml/models/price_forecast_xgb.pkl, saved as
a single combined dict via PriceForecastModel.save()). Until backend/app/model.py
is repointed at this output, running this script has no effect on the live
API -- see ml/README.md for the full pipeline map and status of each one.

Run from repo root:
    python -m backend.ml.train

Expects data/raw/wfp_food_prices_uga.csv to already exist -- run
scripts/download_wfp_data.py first if it doesn't.
"""
import json
from datetime import datetime, timezone

import pandas as pd

from .config import DATA_PATH, METRICS_PATH, MODELS_DIR, PRICE_MODEL_PATH
from .price_forecast_model import PriceForecastModel


def main() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if not DATA_PATH.exists():
        raise FileNotFoundError(
            f"{DATA_PATH} not found -- run scripts/download_wfp_data.py first"
        )

    print(f"Loading price history from {DATA_PATH}")
    raw_df = pd.read_csv(DATA_PATH, parse_dates=["date"])

    print("Training price forecast model (XGBoost)...")
    forecaster = PriceForecastModel()
    price_metrics = forecaster.train(raw_df)
    forecaster.save(PRICE_MODEL_PATH)
    print(f"  MAE={price_metrics['mae']:.1f}  MAPE={price_metrics['mape']:.3f}  R2={price_metrics['r2']:.3f}")

    metrics = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "price_forecast": price_metrics,
    }
    METRICS_PATH.write_text(json.dumps(metrics, indent=2))
    print(f"Wrote metrics to {METRICS_PATH}")


if __name__ == "__main__":
    main()
