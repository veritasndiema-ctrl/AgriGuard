"""
backend/app/services/food_scope.py — AgriGuard's single food-only price scope
================================================================================
AgriGuard forecasts crop/food prices for farmers — not batteries, charcoal,
soap, or exercise books. WFP's Uganda price feed carries a real "category"
column, and roughly a fifth of its rows are "non-food" items. This module is
the one place that decides what counts as food: `routers/forecasts.py` and
`routers/markets.py` both loaded and normalised WFP data independently
(`markets.py`'s own docstring calls this out as a known duplication —
"In production, move it to app/services/price_service.py"), and food-only
filtering was first added only to `forecasts.py`. Left that way, `markets.py`
would keep serving non-food items into the dashboard's commodity counts,
movers, and national summary even though `/forecasts/*` had already stopped
forecasting them — the exact kind of drift that let a wash basin end up on a
forecast chart in the first place. Both routers import from here now.

Two-layer filter:
  1. WFP's own `category` column (FOOD_CATEGORIES) — authoritative when
     present.
  2. A commodity-name keyword blocklist (NON_FOOD_COMMODITY_KEYWORDS) — used
     only when no category column exists at all (e.g. FEWS NET's "simple"
     fields extract has no category field to filter on).
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

FOOD_CATEGORIES: set[str] = {
    "cereals and tubers",
    "pulses and nuts",
    "oil and fats",
    "vegetables and fruits",
    "miscellaneous food",
    "meat, fish and eggs",
    "milk and dairy",
}

NON_FOOD_COMMODITY_KEYWORDS: tuple[str, ...] = (
    "exchange rate", "fuel", "diesel", "petrol", "wage", "soap", "charcoal",
    "firewood", "battery", "batteries", "basin", "hoe", "exercise book",
    "school", "jerry can",
)


def is_food_commodity(name: str) -> bool:
    lowered = str(name).lower()
    return not any(kw in lowered for kw in NON_FOOD_COMMODITY_KEYWORDS)


def filter_food_only(df: pd.DataFrame, source_label: str = "") -> pd.DataFrame:
    """
    Returns `df` restricted to food commodities, preferring the `category`
    column when present and falling back to the commodity-name keyword net
    otherwise. Logs how many rows were dropped (info level) — silent for a
    zero-row source (e.g. an empty FEWS NET feed) rather than logging noise
    every sync cycle.
    """
    if df.empty:
        return df

    before = len(df)
    if "category" in df.columns:
        df = df[df["category"].astype(str).str.strip().str.lower().isin(FOOD_CATEGORIES)]
    else:
        df = df[df["commodity"].apply(is_food_commodity)]

    dropped = before - len(df)
    if dropped:
        label = f" ({source_label})" if source_label else ""
        logger.info(
            "Filtered %d non-food observations%s — kept %d food rows.",
            dropped, label, len(df),
        )
    return df
