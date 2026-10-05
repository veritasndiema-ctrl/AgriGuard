"""
AgriGuard MVP FastAPI Entry Point
==================================

Purpose:
- Serve crop price predictions and conveyance
- Validate farmer inputs
- Provide stable API for Streamlit frontend

Design principle:
👉 Maximum reliability for live demo
"""

import contextlib
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.core.config import settings
from backend.app.database import create_tables
from backend.app import models as _models  # noqa: F401 — registers ORM models on Base.metadata (incl. WeatherReading) before create_tables() runs below
from backend.app.schemas import (
    PricePredictionRequest,
    PricePredictionResponse,
)

from backend.app.validator import validate_input
from backend.app.model import predict_price, status as model_status, ModelNotReadyError
from backend.app.services import data_sources

from backend.app.routers.forecasts import router as forecasts_router
from backend.app.routers.markets import router as markets_router
from backend.app.routers.ussd import router as ussd_router
from backend.app.routers.weather import router as weather_router
from backend.app.routers.prices import router as prices_router

logger = logging.getLogger(__name__)

# routers/prices.py was previously NOT wired in: it imported a nonexistent
# top-level `app` package (fixed — now uses `backend.app.*` like every other
# router), and separately depended on backend/app/schemas/price.py, which was
# unimportable regardless of import root because a flat backend/app/schemas.py
# sitting next to the backend/app/schemas/ package shadowed it (fixed — that
# file's content moved into backend/app/schemas/__init__.py so schemas.price
# and schemas.weather are real submodules now). No Postgres dependency either:
# database.py's settings.database_url defaults to SQLite, same as weather.py.
# See ml/README.md-equivalent history in this file's git log / commit
# messages for the full trail; routers/weather.py's comment above this one
# is now stale and has been folded into this note.


# =============================================================================
# APP INITIALIZATION
# =============================================================================

app = FastAPI(
    title="AgriGuard MVP",
    description="AI-powered Crop Price Forecasting, Conveyance & Market Intelligence System",
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(forecasts_router)
app.include_router(markets_router)
app.include_router(ussd_router)
app.include_router(weather_router)
app.include_router(prices_router)


# =============================================================================
# STARTUP — ensure the weather tables exist on a fresh SQLite dev DB, and
# kick off the background WFP price-data sync scheduler
# =============================================================================
# markets/forecasts/ussd are pure-CSV routers (see their imports) — weather
# is the first wired-in router that actually touches the DB, so nothing
# before it needed this. create_all() is idempotent (CREATE TABLE IF NOT
# EXISTS semantics), so this is safe to run on every startup, including
# against an already-migrated Postgres/MySQL DB in production — it no-ops
# there too.

_scheduler: BackgroundScheduler | None = None


@contextlib.contextmanager
def _quiet_sql_echo():
    """
    Background sync jobs run on every source's own interval (see
    data_sources.py) plus once immediately on boot, and each can touch
    hundreds of rows in one pass (weather_sync.py alone upserts ~100
    rows/market x 8 markets). With DEBUG=true (SQLAlchemy's echo=True, see
    database.py), every one of those rows' queries got printed — turning
    ordinary startup into a wall of raw SQL before the console was usable
    for anything else. This mutes SQL echo only for the duration of a
    scheduled job; DEBUG=true still shows full SQL for anything you trigger
    interactively (hitting an endpoint by hand, the dashboard's "Check for
    updates now" button, etc.) — nothing about that behavior changes.
    """
    engine_logger = logging.getLogger("sqlalchemy.engine")
    previous_level = engine_logger.level
    engine_logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        engine_logger.setLevel(previous_level)


def _scheduled_job(sync_fn):
    """Wraps a sync module's sync_if_updated for the scheduler: quiets SQL
    echo (see _quiet_sql_echo) and makes sure one source's unexpected
    exception logs cleanly instead of potentially wedging the scheduler
    thread — sync_if_updated() already catches its own known failure
    modes and returns False, so this is just a last-resort backstop."""
    def _job():
        with _quiet_sql_echo():
            try:
                sync_fn()
            except Exception:
                logger.exception("Background sync job failed unexpectedly: %s", sync_fn.__module__)
    return _job


@app.on_event("startup")
def on_startup() -> None:
    create_tables()

    global _scheduler
    # Generic over backend.app.services.data_sources.SOURCE_REGISTRY instead
    # of one hand-rolled add_job() block per source (previously WFP and
    # FEWS NET each got their own copy-pasted block here). Registering a
    # new ACTIVE source now means adding one entry to data_sources.py, not
    # editing this function — see that module's docstring for the full
    # active/catalogued split.
    active = data_sources.active_sources()
    enabled = [s for s in active if s.enabled_flag]
    if enabled and _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True)

        for source in enabled:
            job_id = source.name.lower().replace(" ", "_").replace("(", "").replace(")", "")
            _scheduler.add_job(
                _scheduled_job(source.sync_module.sync_if_updated),
                "interval",
                hours=source.interval_hours_flag,
                id=job_id,
                next_run_time=datetime.now(),  # also check once immediately on boot
                max_instances=1,
                coalesce=True,
            )
            logger.info(
                "%s sync scheduler started — checking every %.1fh",
                source.name, source.interval_hours_flag,
            )

        _scheduler.start()

    skipped = [s for s in active if not s.enabled_flag]
    for source in skipped:
        logger.info("%s sync is disabled via settings — not scheduled.", source.name)


@app.on_event("shutdown")
def on_shutdown() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)


# =============================================================================
# CORS (MVP SAFE)
# =============================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # MVP: avoid config failures
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# GLOBAL ERROR HANDLER (PREVENT DEMO CRASHES)
# =============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Log the real exception server-side (stack trace, message, everything)
    # for us to debug — but never hand str(exc) straight to the client. This
    # used to leak Python tracebacks / file paths / column names to whatever
    # was calling the API (dashboard, USSD gateway, mobile app), none of
    # which a farmer on the other end can do anything with.
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Something went wrong",
            "detail": "We hit an unexpected problem handling that request. Please try again in a moment.",
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


# =============================================================================
# HEALTH CHECK (FOR DEMO CONFIDENCE)
# =============================================================================

@app.get("/health")
def health_check():
    from backend.app.routers.forecasts import get_data_freshness

    freshness = get_data_freshness()
    return {
        "status": "ok",
        "app": "AgriGuard MVP",
        "version": settings.app_version,
        "ml_ready": bool(model_status()["price_model"] and model_status()["encoders"]),
        "validator_ready": True,
        "price_data_as_of": freshness.price_latest_date,
        "price_data_lag_days": freshness.price_lag_days,
        "price_data_freshness": freshness.price_freshness,
        "weather_last_synced_at": freshness.weather_synced_at,
        "timestamp": datetime.utcnow().isoformat(),
    }


# =============================================================================
# ROOT
# =============================================================================

@app.get("/")
def root():
    return {
        "message": "Welcome to AgriGuard MVP API",
        "docs": "/docs",
        "health": "/health"
    }


# =============================================================================
# PRICE PREDICTION ENDPOINT (CORE MVP FEATURE)
# =============================================================================

@app.post("/api/v1/predict", response_model=PricePredictionResponse)
def predict_price_endpoint(payload: PricePredictionRequest):

    # Step 1: Validate input
    validation = validate_input(payload.dict())

    if not validation["is_valid"]:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid input",
                "details": validation["errors"],
                "confidence": validation["confidence"]
            }
        )

    # Step 2: parse "YYYY-MM-DD" into the year/month predict_price expects
    try:
        target_date = datetime.strptime(payload.date, "%Y-%m-%d")
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid input", "details": ["date must be YYYY-MM-DD"]},
        )

    # Step 3: run ML prediction (model.py works in commodity/market terms)
    try:
        result = predict_price(
            commodity=payload.crop,
            market=payload.region,
            year=target_date.year,
            month=target_date.month,
        )
    except ModelNotReadyError as e:
        # e's own message is developer-facing (file paths, "Run scripts/...
        # first") — log it for us, tell the caller something they can act on.
        logger.error("Prediction model not ready: %s", e)
        return JSONResponse(
            status_code=503,
            content={
                "error": "Prediction service unavailable",
                "detail": "Price predictions aren't available right now. Please try again shortly.",
            },
        )
    except ValueError as e:
        # e's own message dumps the full list of known markets/commodities
        # from the encoder — useful in logs, not something to hand a
        # USSD/mobile user as-is.
        logger.info("Prediction rejected — unrecognised input: %s", e)
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid input",
                "detail": (
                    f"'{payload.crop}' or '{payload.region}' isn't recognised. "
                    "Use GET /forecasts/commodities to see supported crops and markets."
                ),
            },
        )

    # Step 4: adapt model.py's output shape into the PricePredictionResponse the API promises
    predicted = result["predicted_price_ugx"]
    lag1 = result.get("price_lag1", predicted)
    if predicted > lag1 * 1.02:
        trend, recommendation = "up", "STORE"
    elif predicted < lag1 * 0.98:
        trend, recommendation = "down", "SELL"
    else:
        trend, recommendation = "stable", "HOLD"

    # The interval is calibrated from held-out training error in metrics.json,
    # rather than a fixed percentage that is equally confident for every crop.
    interval_width = (result["upper_bound_ugx"] - result["lower_bound_ugx"]) / predicted if predicted else 1.0
    confidence = max(0.0, min(1.0, 1 - interval_width))

    return PricePredictionResponse(
        crop=payload.crop,
        region=payload.region,
        date=payload.date,
        predicted_price=predicted,
        currency=result["currency"],
        trend=trend,
        recommendation=recommendation,
        confidence=round(confidence, 2),
        timestamp=datetime.utcnow(),
    )
