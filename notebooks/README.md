# AgriGuard Notebooks — Quant Validation Pipeline

These notebooks are the validation environment for AgriGuard's core mission:
tiered, 90-day price forecasting. Run in order — each stage writes its
output to `data/processed/` or `ml/models/`, and the next stage reads it
back rather than recomputing.

| # | Notebook | Produces | Backs |
|---|---|---|---|
| 01 | `01_data_ingestion_eda.ipynb` | `prices_clean.parquet`, `coverage_report.parquet`, `tier_eligibility.parquet`, `commodity_volatility.parquet` | Data quality baseline for everything downstream |
| 02 | `02_feature_engineering.ipynb` | `features_tier_7_14.parquet`, `features_tier_30.parquet`, `features_tier_60_90.parquet`, `feature_encoders.pkl` | The 3 forecast tiers described in the README |
| 03 | `03_walkforward_backtesting.ipynb` | `backtest_results.parquet` | The per-tier MAE/MAPE/R² the README's ML Methodology section reports; source material for `quant/backtesting.py` |
| 04 | `04_prediction_intervals_risk.ipynb` | `risk_scores.parquet` | Prediction intervals + confidence labels; source material for `quant/intervals.py` and `quant/risk_metrics.py` |
| 05 | `05_model_export.ipynb` | `ml/models/price_forecast_tier_*.pkl`, `encoders.pkl`, `risk_scores.json`, `metrics.json` | Deployable artifacts `backend/app/model.py` loads |

## Why this replaces the old notebook set

The previous notebooks (`01_eda.ipynb`, `02_cleaning.ipynb`, `03_modelling.ipynb`,
`AgriGuard_MVP.ipynb`, `price_forecasting_exploration.ipynb`) were early
exploration: hardcoded local paths, empty stubs, and a single Prophet series
validated with one 80/20 split at a single 12-week horizon. They're removed
entirely rather than kept alongside the new set, since they no longer
reflect the tiered, backtested approach the rest of the repo is built around.

## Relationship to `scripts/` and `quant/`

- `scripts/train_models.py` is the production training entry point (what CI
  or a deployment job actually runs). Notebooks 02–05 are where a change to
  feature engineering or model selection gets validated *before* it's
  promoted into that script.
- `quant/` (backend module) is where the backtesting/interval/risk logic
  prototyped in notebooks 03–04 should ultimately live as importable,
  tested functions — shared with the [Vestora](https://github.com/Ve-stora/vestora)
  quant module. Treat these notebooks as the reference implementation to
  port from, not a permanent parallel implementation.

## Running

```bash
# from repo root, with requirements.txt installed
python scripts/download_wfp_data.py   # ensures data/raw/wfp_food_prices_uga.csv exists
jupyter lab notebooks/
```

Run `01` → `02` → `03` → `04` → `05` in order. Each notebook is idempotent —
re-running it overwrites its own output files without needing the others
re-run, as long as their upstream `.parquet`/`.pkl` files already exist.

**Note:** `03_walkforward_backtesting.ipynb` is the slow one — it trains one
XGBoost model per fold, per tier, per crop×market pair. Expect this to take
noticeably longer than the others on the full dataset.
