"""
Paths and hyperparameters shared across the ml package.

Kept in one place so train.py and the FastAPI inference layer
(backend/app/model.py, backend/app/validator.py) agree on where
data/models live without hardcoding paths in multiple files.
"""
from pathlib import Path

# backend/ml/config.py -> repo root is two levels up
REPO_ROOT = Path(__file__).resolve().parents[2]

DATA_PATH = REPO_ROOT / "data" / "raw" / "wfp_food_prices_uga.csv"
MODELS_DIR = REPO_ROOT / "ml" / "models"
METRICS_PATH = MODELS_DIR / "metrics.json"

PRICE_MODEL_PATH = MODELS_DIR / "price_forecast_xgb.pkl"

FORECAST_HORIZON_WEEKS = 4
TIME_SPLIT_RATIO = 0.8  # time-ordered train/test split, never shuffled
