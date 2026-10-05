"""
backend/app/validator.py — Input validation for the /api/v1/predict endpoint
==============================================================================
main.py imports `validate_input` and calls it before touching the ML layer
(backend.app.model.predict_price):

    validation = validate_input(payload.dict())
    if not validation["is_valid"]:
        return 400 {"error": ..., "details": validation["errors"], "confidence": validation["confidence"]}

Why this exists as its own step, ahead of predict_price():
  - predict_price() already raises ValueError on an unrecognised commodity/
    market (see model.py), but that error is a flat string — not useful for
    a farmer on a USSD session or a frontend form that wants to say
    "did you mean 'Maize'?".
  - Catching bad input here, with suggestions, keeps that friendlier
    experience in one place instead of duplicating it in every caller.
  - It's cheap: list_commodities()/list_markets() read from the already-
    loaded encoders (or fall back to the committed CSV — see model.py), so
    this never needs its own copy of the known-value lists.

Contract (depended on by backend/app/main.py and tests/test_api.py):
    validate_input(payload: dict) -> {
        "is_valid":   bool,
        "errors":     list[str],          # empty when is_valid is True
        "confidence": float,               # 0.0-1.0, see _overall_confidence()
        "normalized": {                    # best-effort cleanup, for callers
            "crop": str | None,            # that want it; main.py ignores it
            "region": str | None,
        },
    }

Design note on strictness: if the known commodity/market lists come back
empty (e.g. no data file and no trained model yet — a cold-start demo
environment), this deliberately does NOT fail every request. There is
nothing to validate crop/region against yet, so those checks are skipped
rather than rejecting input the model itself hasn't had a chance to judge.
Date format/range checks still apply regardless, since those don't depend
on any data being loaded.
"""
from __future__ import annotations

import difflib
import logging
from datetime import datetime, timedelta
from typing import Optional

from backend.app.model import list_commodities, list_markets

log = logging.getLogger(__name__)

DATE_FORMAT = "%Y-%m-%d"

# How far outside "now" a requested forecast date is still considered
# reasonable. Wide on purpose — this is a sanity check against typos
# (e.g. a stray year like "2099-01-01"), not a tight business rule.
MAX_PAST = timedelta(days=365 * 10)   # WFP history realistically starts ~2010s
MAX_FUTURE = timedelta(days=365 * 5)  # forecasting 5 years out is already a stretch

# A fuzzy match below this ratio isn't worth surfacing as a suggestion —
# it's more likely to confuse than help ("did you mean 'Rice'?" for "xyz").
MIN_SUGGESTION_RATIO = 0.6


def _closest_match(value: str, known: list[str]) -> tuple[Optional[str], float]:
    """
    Case-insensitive exact match first (ratio 1.0); otherwise the best
    difflib suggestion from `known`, if any clears MIN_SUGGESTION_RATIO.
    Returns (match_or_None, confidence).
    """
    if not value or not known:
        return None, 0.0

    value_clean = value.strip().lower()
    lookup = {k.lower(): k for k in known}

    if value_clean in lookup:
        return lookup[value_clean], 1.0

    close = difflib.get_close_matches(value_clean, lookup.keys(), n=1, cutoff=MIN_SUGGESTION_RATIO)
    if not close:
        return None, 0.0

    best = close[0]
    ratio = difflib.SequenceMatcher(None, value_clean, best).ratio()
    return lookup[best], ratio


def _validate_field(
    label: str,
    value: str,
    known: list[str],
    errors: list[str],
) -> tuple[Optional[str], float]:
    """
    Validates one free-text field (crop or region) against a known-value
    list. Appends a human-readable error (with a suggestion when one
    exists) if it isn't an exact match. Returns (exact_match_or_None,
    confidence) — exact_match is only non-None when the field is valid.
    """
    if not value or not value.strip():
        errors.append(f"{label} is required")
        return None, 0.0

    if not known:
        # Nothing to validate against yet (cold start) — accept as-is.
        return value.strip(), 1.0

    match, confidence = _closest_match(value, known)

    if confidence >= 1.0:
        return match, confidence

    if match:
        errors.append(f"Unknown {label} '{value}'. Did you mean '{match}'?")
    else:
        errors.append(f"Unknown {label} '{value}'. Known values: {', '.join(sorted(known))}")
    return None, confidence


def _validate_date(value: str, errors: list[str]) -> float:
    """Validates the YYYY-MM-DD date field. Returns a 0.0/1.0 confidence."""
    if not value or not value.strip():
        errors.append("date is required")
        return 0.0

    try:
        parsed = datetime.strptime(value.strip(), DATE_FORMAT)
    except ValueError:
        errors.append(f"date must be in {DATE_FORMAT} format (got '{value}')")
        return 0.0

    now = datetime.now()
    if parsed < now - MAX_PAST or parsed > now + MAX_FUTURE:
        errors.append(
            f"date '{value}' is outside the supported range "
            f"({(now - MAX_PAST).date()} to {(now + MAX_FUTURE).date()})"
        )
        return 0.0

    return 1.0


def _overall_confidence(scores: list[float]) -> float:
    """Mean of the individual field confidences, clamped to [0, 1]."""
    if not scores:
        return 0.0
    return round(max(0.0, min(1.0, sum(scores) / len(scores))), 2)


def validate_input(payload: dict) -> dict:
    """
    Validates a PricePredictionRequest payload ({crop, region, date}) before
    it reaches the ML layer. See module docstring for the return contract.
    """
    errors: list[str] = []

    crop = payload.get("crop", "")
    region = payload.get("region", "")
    date = payload.get("date", "")

    try:
        known_commodities = list_commodities()
    except Exception as e:  # pragma: no cover — defensive; model.py already
        log.warning(f"Could not load known commodities for validation: {e}")
        known_commodities = []

    try:
        known_markets = list_markets()
    except Exception as e:  # pragma: no cover — same as above
        log.warning(f"Could not load known markets for validation: {e}")
        known_markets = []

    crop_match, crop_conf = _validate_field("crop", crop, known_commodities, errors)
    region_match, region_conf = _validate_field("region", region, known_markets, errors)
    date_conf = _validate_date(date, errors)

    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "confidence": _overall_confidence([crop_conf, region_conf, date_conf]),
        "normalized": {
            "crop": crop_match,
            "region": region_match,
        },
    }
