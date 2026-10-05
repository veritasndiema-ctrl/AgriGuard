# data/

Dataset card and directory layout for AgriGuard's two data sources: WFP crop
prices (the core dataset every ML model and API route depends on) and
Open-Meteo weather, continuously synced by the running backend.

```
data/
├── raw/
│   ├── wfp_food_prices_uga.csv     # canonical price dataset — see below
│   └── weather/
│       ├── {market}_historical_{fetched_date}.json
│       └── {market}_forecast_{fetched_date}.json
├── processed/
│   └── weather/
│       ├── uganda_weather_historical_{fetched_date}.csv   # all markets, combined
│       └── uganda_weather_forecast_{fetched_date}.csv
└── README.md                        # this file
```

## 1. `raw/wfp_food_prices_uga.csv` — primary dataset

**Source:** [WFP VAM Food Prices — Uganda](https://data.humdata.org/dataset/wfp-food-prices-for-uganda)
(HDX, open license — see the HDX page for current terms). Fetched by
`python scripts/download_wfp_data.py`, which writes this exact filename —
every consumer below hardcodes/defaults to it, so don't rename it without
updating `config/.env`'s `AGRIGUARD_PRICE_DATA`.

**Snapshot in this repo:** a reference snapshot only. Its coverage date is
not a promise of current data. The live backend reports the exact latest
observation through `GET /forecasts/data-status`.

**Schema:**

| Column | Type | Notes |
|---|---|---|
| `date` | date (`YYYY-MM-DD`) | Monthly observation |
| `country` | string | Always `Uganda` |
| `market` | string | One of: Arua, Fort Portal, Gulu, Jinja, Kampala, Kasese, Lira, Mbale, Mbarara, Soroti |
| `category` | string | Always `cereals and tubers` in the current snapshot |
| `commodity` | string | One of: Beans, Cassava, Groundnuts, Maize, Millet, Rice, Sorghum, Sweet Potato |
| `unit` | string | Always `KG` |
| `currency` | string | Always `UGX` |
| `pricetype` | string | Always `Retail` |
| `price` | float | Local-currency price per unit |
| `usdprice` | float | USD-converted price per unit |
| `latitude` / `longitude` | float | Market coordinates |

**Consumers:** `backend/app/model.py`, `backend/app/routers/{forecasts,markets,ussd}.py`,
`backend/ml/{config,train}.py`, `ml/training/*.py`, `ml/evaluation/evaluate_forecasts.py`,
`scripts/train_models.py`. All of these now agree on this one filename —
this pass fixed three modules (`backend/ml/config.py`,
`ml/evaluation/evaluate_forecasts.py`)
that were pointing at a nonexistent `wfp_uganda_prices.csv`, and one
(`ml/training/train_price_model.py`) pointing at a nonexistent
`uganda_food_prices.csv`. Both would have failed with `FileNotFoundError` on
a clean clone.

**Refresh:**
```bash
python scripts/download_wfp_data.py
```

**Versioning:** committed to the repo as a ~770 KB reference snapshot so a
fresh clone can train and demo immediately without an HDX round-trip. If the
dataset grows materially (multi-year backfill, more markets), move to
Git LFS or re-download on CI rather than growing this file indefinitely
in normal git history.

## 2. `raw/fews_net_prices_uga.csv` — supplementary live(r) price feed

**Source:** [FEWS NET Data Warehouse (FDW)](https://fdw.fews.net/api/marketpricefacts.csv?country_code=UG)
— free, no account required for public data (`FEWS_NET_USERNAME`/`PASSWORD`
in `config/.env` are only needed for permissioned series). See
[FDW API docs](https://help.fews.net/fdw/fews-net-api) for the full filter
reference. Synced by `backend/app/services/fews_net_sync.py`.

**Why it exists alongside the WFP CSV above:** WFP is the deep historical
backbone (2006–present upstream) but is itself only refreshed monthly upstream and
can lag before HDX republishes. FEWS NET tracks largely the same Uganda
staple-food markets through an independent collection pipeline, so blending
it in (see `load_price_data()` in `backend/app/routers/{forecasts,markets}.py`)
gives a second, fresher check on current prices without discarding WFP's
history. On any (market, commodity, date) both feeds cover, FEWS NET's value
wins.

**Scope:** only the last `FEWS_NET_LOOKBACK_DAYS` (default 730) is pulled —
this file is deliberately shallow. It is not meant to stand alone; if it's
missing or fails to sync, `load_price_data()` falls back to WFP-only, same
as before this feed existed.

**Schema:** normalised to the same `date, commodity, market, price, currency,
unit, price_type` shape as the WFP CSV (plus a `source` column set to
`FEWS_NET`) so both feeds can be concatenated directly — see
`_normalise()` in `fews_net_sync.py` for the exact FDW column mapping.

**Refresh:** automatic on a `FEWS_NET_SYNC_INTERVAL_HOURS` schedule (default
6h) once the backend is running, or on demand via
`POST /forecasts/sync/fews-net`. Not committed to the repo — it's a live
cache, regenerated on first sync in any environment.

## 3. `raw/weather/` and `processed/weather/` — offline fixtures

`scripts/fetch_weather.py` pulls daily weather (temperature, rainfall,
humidity, wind, evapotranspiration, plus a derived water-balance proxy) from
[Open-Meteo](https://open-meteo.com) for **8** of the 10 WFP markets —
Kampala, Gulu, Mbarara, Mbale, Kasese, Lira, Jinja, Arua. **Fort Portal and
Soroti are not covered yet**; add their coordinates to `MARKETS` in
`scripts/fetch_weather.py` before relying on full national weather coverage.

**Naming:** every file is timestamped with its *fetch* date
(`{market}_{historical|forecast}_{YYYY-MM-DD}.json`), not the data's date
range. The files currently in this repo were fetched 2026-06-14.

**Two very different shelf lives:**
- **`historical`** files cover the trailing 365 days as of the fetch date.
  Their content stays a valid reference sample indefinitely (a labeled record
  of what weather actually happened), so they're kept in git as demo/offline
  fixtures.
- **`forecast`** files are a 16-day-ahead snapshot. These are stale within
  weeks by design and should **not** be treated as current — re-run
  `scripts/fetch_weather.py` rather than trusting a committed forecast file.
  `.gitignore` excludes new forecast snapshots going forward; the ones
  already in this repo predate that rule and are kept only as a schema
  example — do not use them for anything time-sensitive.

**Schema** (`processed/weather/uganda_weather_*.csv`, one row per market-day):
`date, market, region, latitude, longitude, elevation_m, temp_max_c,
temp_min_c, rainfall_mm, rain_mm, precip_hours, humidity_max_pct,
humidity_min_pct, wind_speed_max_kmh, sunshine_seconds,
et0_evapotranspiration_mm, water_balance_mm, fetched_at, data_source`

The live backend does not use these committed files for current weather. It
refreshes Open-Meteo directly through `weather_sync.py`, stores validated
history and the 16-day forecast in the database, and exposes sync status
through `GET /weather/sync/status` and `GET /forecasts/data-status`.

**Refresh:**
```bash
python scripts/fetch_weather.py                    # all 8 covered markets
python scripts/fetch_weather.py --market Kampala    # single market
python scripts/fetch_weather.py --days 90           # shorter history window
python scripts/fetch_weather.py --no-forecast       # skip the 16-day forecast call
```

## Data quality

`scripts/validate_data.py` runs schema/range checks on both datasets before
training (price outlier bounds, date continuity, required-column checks,
weather-file schema checks). It's implemented and passes on the committed
datasets — run it with `python scripts/validate_data.py --weather-dir
data/processed/weather`. `ml/training/*.py` and `backend/ml/train.py` will
still only implicitly notice a malformed row (a crash or a silently bad
model) if you skip this step first.

## What's git-ignored and why

See root `.gitignore`. Summary: the WFP CSV and historical-weather JSON/CSV
are committed as small, stable reference data so the repo is runnable out of
the box. Trained model artifacts (`ml/models/*.pkl`), forecast-weather
snapshots, and anything under `data/processed/` beyond the committed
historical-weather CSVs are regenerated by scripts, not hand-maintained, and
should not be committed.
