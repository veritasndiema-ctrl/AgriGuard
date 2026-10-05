# ml/

**Last verified against the repo Sep 2026.**

This directory used to describe four separate, competing price-forecasting
implementations. Three of them were never loaded by anything live and have
been moved to `archive/deprecated-ml-pipelines/` (see that folder's README
for the full history and why).

## What's here now

**`scripts/train_models.py` is the only training pipeline, and it's the one
the FastAPI backend actually uses.** It writes
`ml/models/price_forecast_model.pkl` + `ml/models/encoders.pkl`, and
`backend/app/model.py` loads exactly those two files by name at request
time.

```bash
# from repo root, after scripts/download_wfp_data.py has produced
# data/raw/wfp_food_prices_uga.csv
python scripts/train_models.py
```

This feeds the **point-prediction** endpoint in `backend/app/model.py` —
a single price estimate for a given crop×market. Its interval is calibrated
from held-out MAE saved in `metrics.json`, not a fixed ±10% band.

The multi-day forecast curve served by `backend/app/routers/forecasts.py`
uses the shared [`ml/pipeline.py`](./pipeline.py) ensemble. It performs
rolling-origin validation on the requested series, combines a robust level,
damped trend, and monthly seasonal candidate, and widens intervals with
lead time. Prophet remains an explicit fallback only when the shared
pipeline cannot be imported or the input is invalid.

## `quant/`

`quant/` supplies the heavier offline XGBoost evaluation and interval
calibration used as an enhancement by the live router. Its rolling features
are strictly causal: rolling statistics are shifted before aggregation so
the target row cannot leak into training.

## `ml/models/`

Where `scripts/train_models.py` writes its two `.pkl` files and
`metrics.json`. Kept empty in git (`.gitkeep`) since trained artifacts
aren't committed.
