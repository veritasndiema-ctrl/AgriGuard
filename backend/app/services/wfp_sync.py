"""
backend/app/services/wfp_sync.py — Live sync for the WFP Uganda price dataset
================================================================================
Problem this replaces: scripts/download_wfp_data.py only ever ran once — if
data/raw/wfp_food_prices_uga.csv already existed, it left it alone forever.
Keeping it current meant deleting the file and re-running the full download
by hand.

This module instead:
  1. Polls HDX's CKAN `resource_show` API — a ~1KB JSON metadata call
     (last_modified / size / hash), NOT the ~3MB CSV itself — so checking
     "did anything change" is cheap enough to run on a schedule.
  2. Only downloads the actual CSV when that metadata differs from what we
     last synced.
  3. Validates the new file's schema before touching anything live.
  4. Swaps it in atomically (write to a temp file, then os.replace()) so a
     request racing the sync never sees a half-written file.
  5. Invalidates the in-process forecast caches so the next request picks
     up the fresh data immediately, without a backend restart.

Source identifiers below are HDX's real, stable dataset/resource IDs for
"Uganda - Food Prices" (confirmed against
https://data.humdata.org/dataset/wfp-food-prices-for-uganda on 2026-08-24;
that dataset's own metadata lists "Expected Update Frequency: Every month").
CKAN resource IDs are permanent even when the underlying file is replaced,
so these don't need to change when HDX publishes a new month of data.
"""

import io
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.app.core.config import settings

logger = logging.getLogger(__name__)

HDX_PACKAGE_ID = "883929b1-521e-4834-97f5-0ccc2df75b89"
HDX_RESOURCE_ID = "e082d683-cad5-4dcd-bf54-db76ae254d33"
HDX_API_BASE = "https://data.humdata.org/api/3/action"
HDX_DOWNLOAD_URL = (
    f"https://data.humdata.org/dataset/{HDX_PACKAGE_ID}/resource/"
    f"{HDX_RESOURCE_ID}/download/wfp_food_prices_uga.csv"
)

DATA_PATH = Path(settings.price_data_path)
STATE_PATH = DATA_PATH.parent / ".wfp_sync_state.json"

_RETRYABLE = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
)


# =============================================================================
# Local sync-state bookkeeping
# =============================================================================

def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("wfp sync state file unreadable — treating as first run.")
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def last_sync_info() -> dict:
    """What the API/UI can show for 'last updated' — no network call."""
    return _load_state()


# =============================================================================
# HDX metadata check (cheap — no CSV download)
# =============================================================================

@_RETRYABLE
def _fetch_resource_metadata() -> dict:
    resp = requests.get(
        f"{HDX_API_BASE}/resource_show",
        params={"id": HDX_RESOURCE_ID},
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("success"):
        raise RuntimeError(f"HDX API returned success=false: {payload}")
    return payload["result"]


def check_for_update() -> Optional[dict]:
    """
    Returns the new {last_modified, size, url} dict if HDX's resource has
    changed since our last sync, else None. Never touches the CSV body.
    """
    try:
        meta = _fetch_resource_metadata()
    except Exception as exc:
        logger.warning("HDX metadata check failed (will retry next cycle): %s", exc)
        return None

    state = _load_state()
    remote_modified = meta.get("last_modified") or meta.get("revision_last_updated")
    remote_size = meta.get("size")

    if remote_modified == state.get("last_modified") and remote_size == state.get("size"):
        return None  # unchanged since last sync

    return {
        "last_modified": remote_modified,
        "size": remote_size,
        "url": meta.get("url") or HDX_DOWNLOAD_URL,
    }


# =============================================================================
# Download + validate + swap
# =============================================================================

@_RETRYABLE
def _download_csv(url: str) -> bytes:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def _validate_csv_bytes(raw: bytes) -> None:
    """
    Same column-variant check as routers/forecasts.py:load_price_data() —
    catches a malformed or unexpectedly-reshaped upstream file before it
    ever reaches the live dataset.
    """
    df = pd.read_csv(io.BytesIO(raw), nrows=200, low_memory=False)
    if len(df) == 0:
        raise ValueError("Downloaded CSV has no rows — refusing to replace live data.")

    cols = {c.strip().lower().replace(" ", "_") for c in df.columns}
    has_date = any("date" in c for c in cols)
    has_price = "price" in cols
    has_market = any(c in cols for c in ("market", "mktname", "market_name"))
    has_commodity = any(c in cols for c in ("commodity", "cmname", "cm_name", "item"))

    if not (has_date and has_price and has_market and has_commodity):
        raise ValueError(
            f"Downloaded CSV is missing expected columns (found: {sorted(cols)}) "
            "— refusing to replace live data."
        )


def _invalidate_forecast_cache() -> None:
    """Drop cached DataFrame + forecast results so the next request re-reads the fresh CSV."""
    from backend.app.routers import forecasts as forecasts_router_module

    forecasts_router_module.load_price_data._cache = None
    forecasts_router_module._FORECAST_CACHE.clear()


def sync_if_updated(force: bool = False) -> bool:
    """
    Full pipeline: check -> download -> validate -> atomic swap -> invalidate
    caches. Returns True if new data was installed, False if already current
    (or the sync failed safely — the last-known-good CSV is never touched
    until a validated replacement is ready).
    """
    if force:
        update = {"last_modified": None, "size": None, "url": HDX_DOWNLOAD_URL}
    else:
        update = check_for_update()
        if update is None:
            logger.info("WFP price data unchanged — no sync needed.")
            return False

    try:
        raw = _download_csv(update["url"])
        _validate_csv_bytes(raw)
    except Exception as exc:
        logger.error("WFP sync failed — keeping existing dataset. %s", exc)
        return False

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv", dir=str(DATA_PATH.parent))
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(raw)
        os.replace(tmp_path, DATA_PATH)  # atomic on the same filesystem
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    state = _load_state()
    state.update(
        {
            "last_modified": update.get("last_modified"),
            "size": update.get("size") or len(raw),
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _save_state(state)

    _invalidate_forecast_cache()
    logger.info("WFP price data synced — %d bytes written to %s", len(raw), DATA_PATH)
    return True
