# Deprecated ML pipelines (archived 2026-09)

AgriGuard had accumulated **four separate price-forecasting implementations**
(see the pre-archive `ml/README.md`, preserved in git history). Only one —
`scripts/train_models.py` — was ever loaded by the running API
(`backend/app/model.py`). The other three were complete, in some cases
well-tested, but never wired to anything live. Kept as-is, they were a real
risk for this project: a reviewer or collaborator cloning the repo (e.g. for
the Africa Prize submission) would find four contradictory "the" model
implementations with no signal about which one is real.

This directory preserves them, unmodified, for reference:

| Folder here | Was at | Produced | Status when archived |
|---|---|---|---|
| `backend-ml/` | `backend/ml/` | `ml/models/price_forecast_xgb.pkl` | Complete, self-consistent, backtested by `ml-evaluation/`. Not loaded by any live router. |
| `ml-training/` | `ml/training/` | Prophet models per crop×market (`train_price_model.py`) and `ml/saved_models/{crop}_forecast_v{N}.pkl` (`train_forecast.py`) | Standalone; not imported by anything else. `feature_engineering.py` and `metrics_log.py` were also unused helpers kept here for reference. |
| `ml-evaluation/` | `ml/evaluation/` | Backtest report for `backend-ml/`'s output specifically | Only ever evaluated the already-orphaned `backend-ml/` pipeline. |

**Nothing imports these anymore** — confirmed via repo-wide grep before the
move — and `pytest.ini` never discovered `ml/training` or `ml/evaluation` as
test paths, so this is not a test-coverage loss.

## What's live now

- **Point prediction** (single price estimate): `scripts/train_models.py` →
  `ml/models/price_forecast_model.pkl` + `encoders.pkl`, loaded by
  `backend/app/model.py`.
- **Multi-day forecast curve** (what `/forecasts/*` actually serves):
  `backend/app/routers/forecasts.py` — Prophet (or linear-extrapolation
  fallback) fit per request, optionally corrected with a per-request XGBoost
  residual model. See that file's module docstring.
- **Offline validation / model-selection / risk layer**: `quant/` — walk-forward
  backtesting, prediction intervals, per-series risk scoring, and Prophet-vs-
  XGBoost model selection, fed by `scripts/build_quant_features.py`. Not yet
  consulted by either live path above — see `quant/README.md`.

If you want to resurrect anything here rather than write it fresh, do it by
copying it back out and re-wiring it deliberately — don't just delete this
`archive/` folder without checking `ml/README.md`'s history first.
