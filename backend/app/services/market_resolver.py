"""
backend/app/services/market_resolver.py — shared crop/market resolution
================================================================================
WFP's Uganda feed is messy in ways that silently produce wrong farmer-facing
answers:

  * the plain label "Maize" is a legacy series that ended years ago, while the
    current series is "Maize (White)";
  * refugee-settlement markets dominate the newest observations but are not
    where a typical smallholder sells;
  * "Kampala" / "Kabale" (the old defaults) have no recent observations.

Every router (USSD, /forecasts, /markets) resolves crops and markets through
this module so they all return the same market, the same crop and the same
numbers.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

import pandas as pd

# A crop/market with no observation in this window is considered stale and is
# never silently preferred over a current one.
RECENT_DAYS: int = 180

# What farmers type -> the WFP label of the raw crop (not the processed product).
CROP_ALIASES: dict[str, str] = {
    "maize": "Maize (White)",
    "cassava": "Cassava (Fresh)",
}


def is_refugee_market(name: str) -> bool:
    return "refugee" in str(name).lower()


def _latest(df: pd.DataFrame) -> pd.Timestamp:
    return df["date"].max()


def _is_recent(df: pd.DataFrame, commodity: str) -> bool:
    mask = df["commodity"].str.lower() == commodity.lower()
    if not mask.any():
        return False
    return df.loc[mask, "date"].max() >= _latest(df) - timedelta(days=RECENT_DAYS)


def match_commodity(df: pd.DataFrame, user_input: str) -> Optional[str]:
    """
    Best matching commodity label in `df` for what the user typed, or None.

    Order: crop alias (if current) -> exact -> starts-with -> contains. Within
    a tier, shorter labels win, and a variant with CURRENT data beats a stale
    one (so "Maize" never resolves to the dead pre-2019 series).
    """
    clean = (user_input or "").strip().lower()
    if not clean:
        return None
    labels = list(df["commodity"].unique())
    lower = {c.lower(): c for c in labels}

    # Exact alias, or a typo-prefix of one ("maiz" -> maize) of >= 3 letters.
    alias = CROP_ALIASES.get(clean) or next(
        (v for k, v in CROP_ALIASES.items() if len(clean) >= 3 and k.startswith(clean)),
        None,
    )
    if alias and alias.lower() in lower and _is_recent(df, lower[alias.lower()]):
        return lower[alias.lower()]

    ordered: list[str] = []
    for test in (
        lambda c: c.lower() == clean,
        lambda c: c.lower().startswith(clean),
        lambda c: clean in c.lower(),
    ):
        ordered += sorted((c for c in labels if test(c) and c not in ordered), key=len)
    if not ordered:
        return None
    return next((c for c in ordered if _is_recent(df, c)), ordered[0])


def resolve_commodity(df: pd.DataFrame, user_input: str) -> str:
    """Like match_commodity but falls back to the typed text (title-cased)."""
    return match_commodity(df, user_input) or (user_input or "").strip().title()


def regular_markets(
    df: pd.DataFrame,
    commodity: str,
    min_obs: int = 1,
    limit: Optional[int] = None,
) -> list[str]:
    """
    Markets for `commodity` that have CURRENT data, excluding refugee
    settlements, ordered by (most recent observation, observation count).
    """
    sub = df[df["commodity"].str.lower() == commodity.lower()]
    if sub.empty:
        return []
    cutoff = _latest(df) - timedelta(days=RECENT_DAYS)
    rows = []
    for mkt, g in sub.groupby("market"):
        if is_refugee_market(mkt) or len(g) < min_obs:
            continue
        last = g["date"].max()
        if last < cutoff:
            continue
        rows.append((mkt, last, len(g)))
    rows.sort(key=lambda r: (r[1], r[2]), reverse=True)
    names = [r[0] for r in rows]
    return names[:limit] if limit else names


def best_market(df: pd.DataFrame, commodity: str, min_obs: int = 4) -> Optional[str]:
    """Most sensible single market for a crop: current, regular, enough history."""
    picks = regular_markets(df, commodity, min_obs=min_obs, limit=1)
    if picks:
        return picks[0]
    # Nothing current: fall back to the market with the most recent data at all.
    sub = df[df["commodity"].str.lower() == commodity.lower()]
    if sub.empty:
        return None
    return sub.groupby("market")["date"].max().sort_values().index[-1]
