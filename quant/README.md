# quant/

AgriGuard's backtesting, prediction-interval, and risk-scoring layer.
The methodology was validated in `notebooks/03_walkforward_backtesting.ipynb`,
`notebooks/04_prediction_intervals_risk.ipynb`, and
`notebooks/05_model_export.ipynb` (see `notebooks/README.md` for the full
01→05 pipeline) — this is the production implementation of that methodology.

Shared discipline with the [Vestora](https://github.com/Ve-stora/vestora)
quant module — see the "Quant" section of `requirements.txt`
(`statsmodels`, `arch`, `mapie`, `pyarrow`).

**Status: implemented and tested, not yet wired into either live forecasting
path** (`backend/app/model.py`'s point prediction, or
`backend/app/routers/forecasts.py`'s per-request forecast curve). See
`ml/README.md` for how those two fit together and what's still missing to
connect them to this layer.

## Layout

```
quant/
├── __init__.py
├── features.py          # tiered feature engineering — canonical source, see below
├── backtesting.py        # walk-forward CV, per tier x crop x market
├── intervals.py           # conformal-style prediction intervals per tier
├── risk_metrics.py       # commodity volatility + per-series risk score
├── model_selection.py     # XGBoost vs Prophet, chosen per crop x market
├── tests/
│   ├── __init__.py
│   ├── test_backtesting.py
│   ├── test_intervals.py
│   ├── test_risk_metrics.py
│   └── test_model_selection.py
└── README.md
```

## Pipeline

```
data/raw/wfp_food_prices_uga.csv
        │
        ▼
scripts/build_quant_features.py                 (production — no notebook needed)
        │  → data/processed/prices_clean.parquet
        │  → data/processed/features_{tier}.parquet   (via quant.features)
        │  → data/processed/feature_encoders.pkl
        ▼
quant.backtesting.run_backtest()
        │  → data/processed/backtest_results.parquet
        │  → data/processed/backtest_residuals.parquet   (fold-level residuals)
        │
        ├──▶ quant.intervals.add_interval_columns()       → interval_halfwidth_ugx
        ├──▶ quant.risk_metrics.build_risk_report()        → risk_scores.parquet
        └──▶ quant.model_selection.select_model_per_group() → chosen_model per series
```

`quant/features.py` is the canonical implementation of the tiered
feature set (7-14 / 30 / 60-90 day). `notebooks/02_feature_engineering.ipynb`
explored this logic first; `scripts/build_quant_features.py` and the notebook
should both import from `quant/features.py` rather than each keeping their
own copy — that duplication is exactly how the notebook's markdown ended up
claiming (incorrectly) that it mirrored `scripts/train_models.py::build_features`.
It doesn't, and isn't meant to: `train_models.py` builds a different, single
flat feature set for the separate point-prediction model. See
`quant/features.py`'s module docstring.

## Usage

```bash
# 1. Generate the tiered feature files from raw data (no notebook required)
python scripts/build_quant_features.py

# 2. Run the backtest / interval / risk / model-selection layer
```

```python
from pathlib import Path
from quant.backtesting import run_backtest, summarize_by_tier
from quant.intervals import add_interval_columns
from quant.risk_metrics import build_risk_report
from quant.model_selection import select_model_per_group

processed_dir = Path("data/processed")

backtest_results, residuals = run_backtest(processed_dir)
print(summarize_by_tier(backtest_results))

with_intervals = add_interval_columns(backtest_results, residuals)
```

**Performance note:** `run_backtest` refits a model on every walk-forward
fold, for every crop×market pair, for every tier. On the full WFP Uganda
dataset (37 crops × 42 markets) this is slow enough to be impractical to run
inline in a request path or a quick CI check — budget for it as a scheduled
offline job, not something triggered per API call. Restricting `tiers` to
one tier, or filtering `processed_dir`'s feature files to specific
crop×market pairs first, are the easiest ways to get a fast sanity check
locally.

## Notes on production-readiness

- **`quant.backtesting.run_backtest`** retains raw fold-level residuals
  (`backtest_residuals.parquet`) — notebook 04 explicitly flags that its own
  mean/std approximation should be replaced "with the true empirical
  residual quantile from stored fold predictions" once available.
  `quant.intervals.add_interval_columns` uses the true residual quantile
  automatically wherever those residuals exist, and falls back to the
  notebook's approximation only where they don't.
- **`quant.model_selection.select_model_per_group`** runs the Prophet
  backtest per crop x market x tier for real. Notebook 05 leaves this as an
  explicit placeholder ("in a full run, `backtest_prophet` would be applied
  per group/tier here") — this module is that full run.
- All four modeling modules import heavy ML dependencies (xgboost, prophet)
  lazily, inside the functions that need them, so `import quant` and unit
  tests that don't touch model fitting work without those packages
  installed.

## Testing

```bash
python -m pytest quant/tests/ -v
```

`quant/tests` is included in `pytest.ini`'s `testpaths`, so a bare
`pytest` from the repo root runs both `tests/` and `quant/tests/`.
