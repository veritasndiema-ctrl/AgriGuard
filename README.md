# 🌾 AgriGuard

> **Agricultural intelligence for Uganda** — crop price conveyance, crop price forecasting (using quant strategies), market intelligence, and weather insights & forecasts for smallholder farmers, delivered via a mobile app (Android and iOS), a desktop app (Windows and Linux), and a USSD interface for feature phones.

Built by **Keith Ndiema Kissa** (2025/BCS/101/PS) · Mbarara University of
Science and Technology, Uganda

## Objectives

AgriGuard exists to serve four things, and nothing else:

1. **Crop price conveyance** — get price data to farmers on whatever device they have (app, desktop, or a feature phone via USSD)
2. **Crop price prediction / forecasting** — XGBoost and Prophet forecasting per crop and market
3. **Market intelligence** — cross-market comparisons, arbitrage signals, biggest movers, national summaries
4. **Weather insights & forecasts** — historical and forecast weather, conveyed alongside price signals

## Status at a glance

| Layer | State |
|---|---|
| FastAPI backend — forecasts, markets, USSD | **Working.** Wired into `main.py`, backed by the committed WFP CSV. |
| Streamlit dashboard | **Working.** Reads from the backend over HTTP. |
| Price forecasting (backtested ensemble / XGBoost) | **Working**, with rolling-origin validation and calibrated intervals. |
| `prices` router (CRUD price observations) | **Working.** Wired into `main.py`. SQLite-backed in dev, same as the rest of the app — no MySQL dependency. |
| Weather data collection | **Working as a standalone script**, not yet joined into the forecasting features. See `data/README.md`. |
| `scripts/validate_data.py` | **Working.** Run it directly: `python scripts/validate_data.py --weather-dir data/processed/weather`. |
| `quant/` package | **Working, fully tested.** Backtesting, prediction intervals (empirical + conformal), and risk metrics — shared discipline with [Vestora](https://github.com/Ve-stora/vestora)'s quant module. `pytest quant/tests/` passes. |
| `mobile/` (Flutter), `desktop/` (Tauri), `shared/api-client/` | **Working.** Forecast, market-intelligence, and alerts screens wired to the FastAPI backend; not yet built/packaged for distribution. |

This section exists so the rest of the README doesn't have to hedge every
claim — anything described below as working, is working; anything listed
above as scaffold is described that way in its own section too.

## Problem

Ugandan farmers face some (if not all) of these compounding challenges:

- **Price blindness** — no reliable way to know if today is a good day to sell
- **Market fragmentation** — price gaps between markets go unexploited because farmers lack data
- **Unpredictable weather** — made worse by information gaps

## Solution

| Module | What it does | How |
|---|---|---|
| **Price Conveyance** | Delivers current and predicted prices to farmers over whatever channel they have | App (mobile/desktop) for internet-enabled devices, USSD for feature phones |
| **Price Forecasting** | Predicts crop prices weeks ahead, per market | XGBoost on WFP price history, Prophet as an alternative single-series forecaster |
| **Market Intelligence** | Cross-market comparisons, biggest movers, national summary | FastAPI serving the WFP dataset with trend analytics |
| **Weather Insights & Forecasts** | Current and historical weather, and a 14-day forecast, per market | Open-Meteo, surfaced in the app alongside price data |

Accessible via an **app** for internet-enabled devices and a **USSD interface** for feature phones
(`/ussd/simulate` locally; a real short-code requires an Africa's Talking
account).

## Architecture

```
┌───────────────────────────────────────────────────────┐
│  Streamlit Frontend  (port 8501)                       │
│  Home · Dashboard · Price Forecast · USSD Simulator     │
└────────────────────┬─────────────────────────────────── ┘
                      │ HTTP / REST
┌────────────────────▼─────────────────────────────────── ┐
│  FastAPI Backend  (port 8000)                            │
│  /forecasts/*  /markets/*  /ussd/*  /prices/*              │
│  /api/v1/predict  /health                                 │
└──────┬─────────────┬──────────────┬───────────────────── ┘
       │              │              │
  XGBoost         Prophet        SQLite (dev)
  .pkl in ml/models/  fallback       / MySQL (prod)
       │
  data/raw/wfp_food_prices_uga.csv  ←  scripts/download_wfp_data.py
```

## Quick Start

### 1. Clone and set up environment

```bash
git clone https://github.com/Agri-Guard/AgriGuard.git
cd AgriGuard
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp config/.env.example config/.env
# edit config/.env — everything has a working dev default
```
See `config/README.md` for the full variable reference and how config
resolution/precedence works.

### 3. Get data and train models

```bash
python scripts/download_wfp_data.py      # writes data/raw/wfp_food_prices_uga.csv
python scripts/train_models.py           # trains XGBoost -> ml/models/
```
See `data/README.md` for the dataset schema, provenance, and what's
git-ignored vs. committed.

### 4a. Run with Docker Compose (recommended)

```bash
docker-compose up --build
```
- Dashboard: http://localhost:8501
- API docs:  http://localhost:8000/docs

To force fresh data + model training first: `docker-compose --profile train run --rm trainer`.

### 4b. Run natively, no Docker

```bash
./run.sh            # sets up venv, installs deps, copies config/.env, runs both services
./run.sh --setup    # also runs download_wfp_data.py + train_models.py first
```

### 4c. Run manually

```bash
# Terminal 1 — backend
uvicorn backend.app.main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend && streamlit run Home.py --server.port 8501
```

## Project Structure

```
AgriGuard/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI entry point + router wiring
│   │   ├── core/config.py     # Settings, reads config/.env — see config/README.md
│   │   ├── model.py           # ML model loader + inference (XGBoost)       # Input validation
│   │   ├── routers/
│   │   │   ├── forecasts.py   # /forecasts/* — XGBoost/Prophet forecasts
│   │   │   ├── markets.py     # /markets/*   — market intelligence
│   │   │   ├── ussd.py        # /ussd/*      — USSD menu tree + simulator (price conveyance)
│   │   │   └── prices.py      # /prices/*    — wired into main.py
│   │   ├── models/, schemas/  # SQLAlchemy ORM + price-domain schemas (used by prices.py)
│   │   ├── database.py        # SQLAlchemy engine/session (used by prices.py)
│   │   └── services/          # price_service.py, forecast_service.py
│   └── ml/                    # importable package: config.py, features.py,
│                               # price_forecast_model.py, train.py
├── frontend/                  # Streamlit: Home.py + pages/{dashboard,price_forecast,
│                               # ussd_simulator}.py
├── ml/                        # training/evaluation workspace (not imported at runtime)
│   ├── training/               # train_price_model.py, feature_engineering.py
│   ├── evaluation/              # evaluate_forecasts.py
│   ├── models/                 # trained .pkl artifacts (gitignored); the committed numbers live in ml/metrics.json
│   └── README.md                # backend/ml/ vs ml/ split, explained
├── quant/                      # backtesting/intervals/risk-metrics for price forecasting — implemented, pytest-covered
├── mobile/                     # Flutter app: forecast, market-intelligence, alerts screens — implemented
├── desktop/                    # Tauri app: forecast + cross-market dashboard — implemented
├── shared/api-client/          # shared TS API client — implemented
├── scripts/
│   ├── download_wfp_data.py    # fetch WFP Uganda CSV from HDX
│   ├── fetch_weather.py        # fetch Open-Meteo weather (not yet joined into ML features)
│   ├── train_models.py         # train XGBoost price forecaster
│   ├── load_data.py            # load prices into a DB (used by the prices layer)
│   └── validate_data.py        # schema/range validation — implemented, run before training
├── notebooks/                  # tiered forecasting validation pipeline, run 01→05 — see notebooks/README.md
├── tests/                      # test_api.py, test_models.py
├── data/                       # WFP prices + Open-Meteo weather — see data/README.md
├── config/                     # env templates — see config/README.md
├── docs/                       # project documentation (Ministry of ICT showcase materials)
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## ML Methodology

### Price Forecasting (XGBoost)

**Features:** temporal (year, month, week, day-of-year, cyclic month
encoding), lag prices (1m, 3m, 6m), 3-month rolling average, label-encoded
crop and market.

**Training split:** chronological: the latest 20% of months are held out
(sorted by date, not by crop/market, so no future data leaks into training).

**Measured metrics** (`ml/metrics.json`, regenerated by `python scripts/train_models.py`):

| Metric | XGBoost | Naive baseline (last price) |
|---|---|---|
| MAE | ~544 UGX/kg | ~554 UGX/kg |
| MAPE | ~12.8% | ~12.8% |
| R² | ~0.90 | n/a |

**Honest reading:** one-step XGBoost point accuracy is on par with the
persistence baseline on this monthly WFP data. The value of the system is
calibrated prediction intervals (`quant/`), cross-market comparison, and
delivery over USSD, not beating "last price" on point error. The earlier
figures (MAPE ~8%, R² ~0.91) were not reproducible and have been removed.

### Demo path (verified)

Run `uvicorn backend.app.main:app --port 8000`, then `GET /ussd/simulate?text=...`:

| `text=` | Result |
|---|---|
| *(empty)* | Main menu |
| `1*Beans*1` | Latest Beans price at Mbarara with 30-day trend |
| `2*Maize` | Best and worst current markets for white maize |
| `3*Maize` | Next-month outlook from the same ensemble that serves `GET /forecasts/Maize` |

USSD, `/forecasts`, and `/markets` resolve crops and markets through one module
(`backend/app/services/market_resolver.py`): "Maize" means the current
*Maize (White)* series, refugee-settlement markets are excluded from
farmer-facing rankings, and a request for a market with no data (e.g. Kampala)
is answered from the best current market with an explicit note. Regression
tests: `tests/test_demo_consistency.py`.

**Data cadence:** WFP publishes monthly. Regular markets were last observed in
April 2026; refugee settlements are newer. Every answer shows its date.

Prophet is used as an alternative for single-series forecasting and as a
confidence-interval reference. `notebooks/` documents a more ambitious
tiered (7/14/30/60–90 day) backtested version of this pipeline that hasn't
been promoted into `scripts/train_models.py` yet — see `notebooks/README.md`.

## API Reference

Full interactive docs at `/docs` when the backend is running.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | System health check |
| POST | `/api/v1/predict` | Quick price prediction |
| GET | `/forecasts/{commodity}` | ML price forecast |
| GET | `/forecasts/commodities` | List available crops/markets |
| GET | `/forecasts/history/{commodity}` | Historical price series |
| GET | `/forecasts/compare/{commodity}` | Compare forecast approaches |
| GET | `/markets/summary/{commodity}` | Best/worst market for a commodity |
| GET | `/markets/overview/{market}` | All commodities at one market |
| GET | `/markets/movers` | Biggest price gainers/losers |
| GET | `/markets/arbitrage/{commodity}` | Cross-market arbitrage opportunities |
| GET | `/markets/national-summary` | All commodities, national snapshot |
| POST | `/ussd/`, `/ussd/simulate` | USSD session handler / local simulator (price conveyance; no Africa's Talking account needed) |
| GET/POST/PUT/DELETE | `/prices/*` | Paginated CRUD over price observations |

## Data Sources

- **[WFP VAM Food Prices — Uganda](https://data.humdata.org/dataset/wfp-food-prices-for-uganda)** (HDX, open license) — historical crop prices, ~40 markets (incl. refugee settlements), ~38 commodities, 2006–present; monthly cadence. Live-synced every `WFP_SYNC_INTERVAL_HOURS` — see `backend/app/services/wfp_sync.py`.
- **[FEWS NET Data Warehouse (FDW)](https://fdw.fews.net/api/marketpricefacts.csv?country_code=UG)** — supplementary, fresher-cadence Uganda market price feed blended on top of WFP wherever the two overlap (FEWS NET wins on overlap). Free, no account required for public data. Live-synced every `FEWS_NET_SYNC_INTERVAL_HOURS` — see `backend/app/services/fews_net_sync.py` and [API docs](https://help.fews.net/fdw/fews-net-api).
- **[Open-Meteo](https://open-meteo.com)** — free daily weather + 16-day forecast, no API key required, currently 8 of 10 markets covered

Full schema, provenance, and refresh commands: `data/README.md`.

## Known Issues

Kept here instead of silently fixed, so anyone picking this up knows what's
real vs. aspirational:

- **`prices` router — no longer an issue.** It's wired into `main.py`. The
  import from a nonexistent top-level `app` package was fixed to
  `backend.app`, and the flat `backend/app/schemas.py` file that shadowed the
  real `schemas/` package was folded into `schemas/__init__.py`. No MySQL
  dependency either — `DATABASE_URL` falls back to SQLite for dev, same as
  every other router.
- **`scripts/validate_data.py` — no longer an issue.** It's fully implemented
  (schema checks, price-bound sanity checks, weather-file validation) and
  passes on the committed datasets. Run it before training:
  `python scripts/validate_data.py --weather-dir data/processed/weather`.
- **Weather data isn't joined into the forecasting features yet.** Collected
  by `scripts/fetch_weather.py`, documented in `data/README.md`, but no
  training or inference code reads it.
- **`quant/`, `mobile/`, `desktop/`, `shared/api-client/` — no longer
  scaffolds.** All four are implemented: `quant/` has backtesting,
  prediction intervals, and risk metrics with a passing pytest suite;
  `mobile/` and `desktop/` are wired to the live FastAPI backend for
  forecasts, market intelligence, and alerts. None have been built into a
  distributable binary/APK yet — that's still open.

## Roadmap

- [ ] Wire the `prices` router in behind a real MySQL service, or drop it
- [ ] Join weather data into the price-forecasting feature set
- [ ] SMS push alerts via Africa's Talking (price conveyance)
- [ ] District-level food security index
- [ ] Build/package `mobile/` and `desktop/` for distribution (APK, MSI/AppImage)
- [ ] Multi-country expansion (Kenya, Tanzania)

## License

Dual-licensed: **AGPL-3.0** for open-source/non-commercial use — see
[LICENSE](LICENSE). A commercial license is available for closed-source
use — see [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md).

## Author

**Keith Ndiema Kissa**
BSc Computer Science · Mbarara University of Science and Technology
[veritasndiema@gmail.com](mailto:veritasndiema@gmail.com) · GitHub: [Agri-Guard](https://github.com/Agri-Guard)
