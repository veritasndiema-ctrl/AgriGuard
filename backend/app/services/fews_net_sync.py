"""
backend/app/services/fews_net_sync.py — Live sync for FEWS NET (FDW) prices
================================================================================
Why this exists: WFP's Uganda price CSV (services/wfp_sync.py) is the deep
historical backbone — HDX's "Uganda - Food Prices" resource actually spans
2006–present (confirmed against the live resource_show metadata on
2026-08-26; earlier revisions of this docstring undersold it as "2018–
present", which was never true of the upstream data itself — see
scripts/download_wfp_data.py for the one place that WAS artificially
short, now fixed to match) — but is itself only updated monthly upstream
and sometimes lags by several weeks before HDX republishes it. FEWS NET's
Data Warehouse (FDW) tracks largely the same staple-food
markets in Uganda but is a separate collection pipeline with its own update
cadence, so blending it in gives AgriGuard a second, independent check on
"what are prices doing right now" instead of relying on one upstream source.

This module is intentionally the "recent window" feed, not a replacement:
  - WFP        → deep history, coarse freshness
  - FEWS NET   → shallow history (fews_net_lookback_days), better freshness

Design mirrors wfp_sync.py on purpose (same shape: check → download →
validate → atomic swap → invalidate caches) so the two sources are operated
identically from main.py's scheduler and forecasts.py's /sync endpoints.

FDW API reference (public docs, confirmed 2026-08-26):
  https://help.fews.net/fdw/fews-net-api
  https://help.fews.net/fdw/api-authentication
  - Base URL:      https://fdw.fews.net/api
  - Time series:   GET  /marketpricefacts.csv?country_code=UG&fields=simple
  - Auth (opt.):   POST /api-token-auth/ {username, password} -> {"token": ...}
                   then header Authorization: JWT <token> (12h expiry)
  - Unauthenticated requests are allowed and return public data only, which
    is sufficient for Uganda staple food prices — FEWS_NET_USERNAME/PASSWORD
    in settings are optional and only needed for permissioned series.

Known uncertainty, handled defensively: FDW's exact column names for
`fields=simple` marketpricefacts extracts aren't pinned down in the public
docs the way WFP's are. _normalise() below matches on substrings the same
way routers/forecasts.py::load_price_data() already does for WFP, and
_validate_csv_bytes() refuses to swap in a file that doesn't resolve to the
required columns — so a schema drift on FEWS NET's end fails safe (keeps
the last-known-good FEWS NET CSV, or simply leaves the WFP-only view intact
if no FEWS NET CSV has ever synced successfully) rather than corrupting
what forecasts.py serves.
"""

import io
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.app.core.config import settings

logger = logging.getLogger(__name__)

FDW_BASE = "https://fdw.fews.net/api"
FDW_TOKEN_URL = "https://fdw.fews.net/api-token-auth/"
FDW_PRICES_URL = f"{FDW_BASE}/marketpricefacts.csv"

DATA_PATH = Path(settings.fews_net_data_path)
STATE_PATH = DATA_PATH.parent / ".fews_net_sync_state.json"

_RETRYABLE = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
)

# In-process token cache — FDW tokens are valid 12h, well beyond one sync
# cycle, so there's no need to re-auth on every request.
_token_cache: dict = {"token": None, "fetched_at": None}


# =============================================================================
# Local sync-state bookkeeping (same shape as wfp_sync.py)
# =============================================================================

def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("fews_net sync state file unreadable — treating as first run.")
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def last_sync_info() -> dict:
    """What the API/UI can show for 'last updated' — no network call."""
    return _load_state()


def is_available() -> bool:
    """Whether a validated FEWS NET CSV exists yet for load_price_data() to blend in."""
    return DATA_PATH.exists()


# =============================================================================
# Auth (optional)
# =============================================================================

@_RETRYABLE
def _fetch_token() -> Optional[str]:
    if not (settings.fews_net_username and settings.fews_net_password):
        return None
    resp = requests.post(
        FDW_TOKEN_URL,
        data={"username": settings.fews_net_username, "password": settings.fews_net_password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("token")


def _auth_headers() -> dict:
    if not (settings.fews_net_username and settings.fews_net_password):
        return {}
    now = datetime.now(timezone.utc)
    if _token_cache["token"] and _token_cache["fetched_at"] and (
        now - _token_cache["fetched_at"] < timedelta(hours=11)
    ):
        return {"Authorization": f"JWT {_token_cache['token']}"}
    try:
        token = _fetch_token()
    except Exception as exc:
        logger.warning("FEWS NET token auth failed — continuing unauthenticated (public data only): %s", exc)
        return {}
    if not token:
        return {}
    _token_cache["token"] = token
    _token_cache["fetched_at"] = now
    return {"Authorization": f"JWT {token}"}


# =============================================================================
# Fetch
# =============================================================================

@_RETRYABLE
def _fetch_prices_csv(start_date: str) -> bytes:
    resp = requests.get(
        FDW_PRICES_URL,
        params={
            "country_code": "UG",
            "fields": "simple",
            "start_date": start_date,
        },
        headers=_auth_headers(),
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def _lookback_start_date() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=settings.fews_net_lookback_days)).strftime("%Y-%m-%d")


# =============================================================================
# Normalise to AgriGuard's common schema — mirrors
# routers/forecasts.py::load_price_data()'s WFP normalisation, but matches
# FDW's own column vocabulary (product/market/value/period_date instead of
# WFP's cmname/mktname/price/date).
# =============================================================================

def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    rename_map: dict[str, str] = {}
    for col in df.columns:
        if col in ("period_date", "start_date", "date") and "date" not in rename_map.values():
            rename_map[col] = "date"
        elif col in ("product_name", "product", "commodity") and "commodity" not in rename_map.values():
            rename_map[col] = "commodity"
        elif col in ("market_name", "market", "admin_1", "location_name") and "market" not in rename_map.values():
            rename_map[col] = "market"
        elif col in ("value", "price") and "price" not in rename_map.values():
            rename_map[col] = "price"
        elif col in ("currency", "currency_name") and "currency" not in rename_map.values():
            rename_map[col] = "currency"
        elif col in ("unit_of_measure", "unit", "um_name") and "unit" not in rename_map.values():
            rename_map[col] = "unit"
        elif col in ("price_type",) and "price_type" not in rename_map.values():
            rename_map[col] = "price_type"

    df.rename(columns=rename_map, inplace=True)

    required = {"date", "commodity", "market", "price"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"FEWS NET extract is missing expected columns after normalisation: {sorted(missing)} "
            f"(found: {sorted(df.columns.tolist())})"
        )

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df.dropna(subset=["date", "price"], inplace=True)
    df = df[df["price"] > 0]

    df["commodity"] = df["commodity"].astype(str).str.strip().str.title()
    df["market"] = df["market"].astype(str).str.strip().str.title()

    if "currency" not in df.columns:
        df["currency"] = "UGX"
    if "unit" not in df.columns:
        df["unit"] = "KG"
    if "price_type" not in df.columns:
        df["price_type"] = "Retail"

    return df.reset_index(drop=True)


def _validate_csv_bytes(raw: bytes) -> pd.DataFrame:
    """
    Parses + normalises the downloaded extract, raising on anything that
    would leave load_price_data() worse off than not blending FEWS NET in
    at all. Returns the normalised DataFrame so sync_if_updated() doesn't
    have to re-parse it.
    """
    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    if len(df) == 0:
        raise ValueError("FEWS NET extract has no rows — refusing to install.")
    normalised = _normalise(df)
    if len(normalised) == 0:
        raise ValueError("FEWS NET extract had rows but none survived cleaning — refusing to install.")
    return normalised


# =============================================================================
# Cache invalidation — same target as wfp_sync.py so either source updating
# picks up on the next request.
# =============================================================================

def _invalidate_forecast_cache() -> None:
    from backend.app.routers import forecasts as forecasts_router_module

    forecasts_router_module.load_price_data._cache = None
    forecasts_router_module._FORECAST_CACHE.clear()


# =============================================================================
# Public entry point
# =============================================================================

def sync_if_updated(force: bool = False) -> bool:
    """
    Pull the latest FEWS NET Uganda market prices, validate, and swap in
    atomically. Unlike WFP there's no cheap metadata-only endpoint to check
    first — FDW's own dataset is small once scoped to fews_net_lookback_days,
    so this just re-fetches that window each cycle and compares row-count +
    max(date) against the last sync to decide whether anything changed.

    Returns True if new/changed data was installed, False otherwise (already
    current, or the sync failed safely — the last-known-good CSV, if any, is
    never touched until a validated replacement is ready).
    """
    try:
        raw = _fetch_prices_csv(_lookback_start_date())
        normalised = _validate_csv_bytes(raw)
    except Exception as exc:
        logger.warning("FEWS NET sync failed — keeping existing feed (if any). %s", exc)
        return False

    state = _load_state()
    new_row_count = len(normalised)
    new_max_date = normalised["date"].max().strftime("%Y-%m-%d")

    if (
        not force
        and state.get("row_count") == new_row_count
        and state.get("max_date") == new_max_date
        and DATA_PATH.exists()
    ):
        logger.info("FEWS NET price data unchanged — no sync needed.")
        return False

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv", dir=str(DATA_PATH.parent))
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            normalised.to_csv(f, index=False)
        os.replace(tmp_path, DATA_PATH)  # atomic on the same filesystem
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    state.update(
        {
            "row_count": new_row_count,
            "max_date": new_max_date,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _save_state(state)

    _invalidate_forecast_cache()
    logger.info(
        "FEWS NET price data synced — %d observations (through %s) written to %s",
        new_row_count, new_max_date, DATA_PATH,
    )
    return True
