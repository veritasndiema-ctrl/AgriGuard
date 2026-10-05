"""
frontend/errors.py — Turn backend errors into something a user can read
================================================================================
Both dashboard.py and price_forecast.py had their own api-calling helper,
and both surfaced failures the same unhelpful way: dashboard.py showed
`f"HTTP {r.status_code}: {r.text[:200]}"` (a raw truncated response body —
JSON braces, field names, and all) and price_forecast.py's generic
`except Exception as e: st.error(f"API error: {e}")` showed things like
"API error: 422 Client Error: Unprocessable Entity for url: ..." — the
`requests` library's own technical summary, not the friendly `detail`
message the backend actually put in the response body (see
backend/app/routers/forecasts.py's `_friendly_error` and
backend/app/main.py's exception handlers for the other half of this fix).

Both pages now call the two functions below instead.
"""

from __future__ import annotations

import requests

_GENERIC_SERVER_ERROR = "Something went wrong on our end. Please try again in a moment."
_GENERIC_CLIENT_ERROR = "We couldn't complete that request. Please check your inputs and try again."


def humanize_response_error(response: requests.Response) -> str:
    """
    Given a non-2xx `requests.Response`, return the backend's own friendly
    `detail`/`error` message when present (FastAPI's HTTPException responses
    are `{"detail": "..."}`, and AgriGuard's routers now write farmer-facing
    text there deliberately) — otherwise a generic message keyed off the
    status code, never the raw response body.
    """
    try:
        body = response.json()
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, str) and detail.strip():
            return detail
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, str) and error.strip():
            return error
    except (ValueError, AttributeError):
        pass  # response body wasn't JSON — fall through to a generic message

    if response.status_code >= 500:
        return _GENERIC_SERVER_ERROR
    if response.status_code == 404:
        return "We couldn't find that. Please check the crop and market and try again."
    return _GENERIC_CLIENT_ERROR


def humanize_exception(exc: Exception) -> str:
    """Same idea for exceptions raised before a response ever came back
    (connection refused, DNS failure, timeout, ...)."""
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "Cannot reach the AgriGuard backend — is it running?"
    if isinstance(exc, requests.exceptions.Timeout):
        return "That request took too long and timed out. Please try again."
    return "Something went wrong. Please try again."
