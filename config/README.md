# config/

Environment configuration for AgriGuard. This directory holds **templates**
only — the actual `config/.env` you run against is git-ignored and never
committed.

## Files

| File | Purpose |
|---|---|
| `.env.example` | Development template. Copy to `.env` and fill in. Safe defaults (SQLite, debug on, dev secret key). |
| `.env.production.example` | Production template. `DEBUG=false`, no working default secret/DB — every sensitive value is `CHANGE_ME` on purpose so the app fails fast instead of silently running with a dev secret in prod. |
| `.env` | **Not committed.** Your real, local values. Created by you (or `run.sh`) from one of the templates above. |

## Setup

```bash
cp config/.env.example config/.env
# edit config/.env — everything has a working dev default
```

`run.sh` does this automatically on first run if `config/.env` doesn't exist yet.

## How config is loaded

`backend/app/core/config.py` is the single source of truth for settings at
runtime. It resolves values in this priority order (highest wins):

1. **Real process environment variables** — what you'd set with `export FOO=bar`,
   in a systemd `EnvironmentFile`, or in `docker-compose.yml`'s `environment:` block.
2. **`config/.env`** — this is what you're editing locally.
3. **`backend/.env`** — legacy fallback some older tooling still checks; prefer
   `config/.env` for anything new.

This means `docker-compose.yml`'s `environment:` block always overrides
whatever is in `config/.env` inside a container — that's intentional, since
compose sets container-internal values (like `BACKEND_URL=http://backend:8000`)
that wouldn't make sense on a bare-metal run.

## Variable reference

| Variable | Default (dev) | Consumed by | Notes |
|---|---|---|---|
| `APP_ENV` | `development` | `core/config.py` | `production` flips `is_production` on the settings object |
| `DEBUG` | `true` | `core/config.py` | Keep `false` in production |
| `SECRET_KEY` | dev placeholder | `core/config.py` | Generate per-environment: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `LOG_LEVEL` | `INFO` | `core/config.py` | `DEBUG`\|`INFO`\|`WARNING`\|`ERROR` |
| `BACKEND_HOST` / `BACKEND_PORT` | `0.0.0.0` / `8000` | `core/config.py`, `run.sh`, `docker-compose.yml` | |
| `BACKEND_URL` | `http://localhost:8000` | frontend pages (`frontend/pages/*.py`) to reach the API | |
| `FRONTEND_URL` | `http://localhost:8501` | `core/config.py` (CORS allow-list) | |
| `AGRIGUARD_PRICE_DATA` | `data/raw/wfp_food_prices_uga.csv` | `model.py`, `routers/forecasts.py`, `routers/markets.py`, `routers/ussd.py`, `backend/ml/config.py` | Must point at the file produced by `scripts/download_wfp_data.py` — see `data/README.md` |
| `MODEL_DIR` | `ml/models` | `model.py`, `backend/ml/config.py`, `scripts/train_models.py` | Repo-root artifact store, gitignored, produced by `scripts/train_models.py`. Distinct from `backend/ml/`, which is the importable package |
| `DATABASE_URL` | `sqlite:///./agriguard_dev.db` | `core/config.py`, `database.py` | SQLite is dev-only; use MySQL (`aiomysql`/`PyMySQL`, already in `requirements.txt`) in production. The `prices` router that owns this layer is not currently wired into `main.py` — see root `README.md` → Known Issues |
| `AT_API_KEY` / `AT_USERNAME` | empty | `routers/ussd.py` (real gateway path only) | Not needed for `/ussd/simulate`, the endpoint the Streamlit USSD simulator and local dev use |

## A bug this pass fixed

`MODEL_DIR`'s dev default previously pointed at `backend/ml/models` in this
template while every consumer (`model.py`, `backend/ml/config.py`,
`scripts/train_models.py`) actually reads/writes the repo-root `ml/models/`.
Left as-is, a fresh clone following the old template would train models into
one directory and have the API look for them in another. Fixed here; also see
the root `CHANGES.md`-equivalent notes in the commit-messages file for the
matching filename bug this pass found in `ml/`.

## Secrets hygiene

- Never commit `config/.env` or `backend/.env` — both are in `.gitignore`.
- Rotate `SECRET_KEY` per environment; don't reuse dev/staging/prod values.
- In production, inject secrets via your deployment platform (Docker/Compose
  secrets, a systemd `EnvironmentFile` at `0600`, or a secrets manager) rather
  than hand-editing `config/.env.production.example` with real values.
