"""
AgriGuard Services Package

Business logic services for:
- Price intelligence
- Forecasting
"""

import logging

logger = logging.getLogger(__name__)

# ForecastService (forecast_service.py) is dead code — see that module's own
# header comment: "not currently wired into the running app". It imports
# `prophet` unconditionally at module scope, which meant importing ANYTHING
# under backend.app.services (wfp_sync, fews_net_sync, data_sources,
# food_scope, quant_bridge — none of which need Prophet at import time)
# hard-failed with ModuleNotFoundError whenever prophet wasn't installed.
# That's the opposite of how Prophet is treated everywhere else in this
# codebase (routers/forecasts.py imports it lazily inside prophet_forecast()
# specifically so a missing prophet degrades to linear_extrapolation instead
# of crashing). Matching that here rather than letting one unused class
# block every real service in this package.
try:
    from .forecast_service import ForecastService
    __all__ = ["ForecastService"]
except ImportError as exc:  # pragma: no cover — exercised whenever prophet isn't installed
    logger.info("ForecastService unavailable (%s) — it isn't used by the live app anyway.", exc)
    __all__ = []
