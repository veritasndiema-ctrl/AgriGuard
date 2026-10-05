"""Reliable, backtested price forecasting primitives used by the API.

The WFP series are irregular and usually monthly, so a daily Prophet fit is
not a sound default.  This module uses simple candidates that remain valid
for sparse, irregular observations, selects their weights with rolling-origin
validation, and calibrates intervals from held-out errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ForecastResult:
    forecast: pd.DataFrame
    model: str
    mae: float
    folds: int


def _clean(series: pd.DataFrame) -> pd.DataFrame:
    frame = series[["date", "price"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame = frame.dropna().query("price > 0").sort_values("date")
    # Multiple source reports on a date are not independent observations.
    return frame.groupby("date", as_index=False)["price"].median()


def _level(history: pd.DataFrame, dates: pd.DatetimeIndex) -> np.ndarray:
    window = history["price"].tail(min(6, len(history)))
    return np.repeat(float(window.median()), len(dates))


def _drift(history: pd.DataFrame, dates: pd.DatetimeIndex) -> np.ndarray:
    prices = history["price"].to_numpy(float)
    times = history["date"].astype("int64").to_numpy(float) / 86_400_000_000_000
    window = min(8, len(prices))
    x, y = times[-window:], prices[-window:]
    slope = np.polyfit(x - x[-1], y, 1)[0] if window >= 2 and np.ptp(x) else 0.0
    # Damping prevents a short-lived spike from becoming an unbounded forecast.
    slope *= 0.35
    horizon_days = (dates - history["date"].iloc[-1]).days.to_numpy(float)
    level = float(np.median(prices[-min(3, len(prices)) :]))
    return np.maximum(0.0, level + slope * horizon_days)


def _seasonal(history: pd.DataFrame, dates: pd.DatetimeIndex) -> np.ndarray:
    current = float(history["price"].iloc[-1])
    if len(history) < 18:
        return np.repeat(current, len(dates))
    by_month = history.assign(month=history["date"].dt.month).groupby("month")["price"].median()
    global_level = float(history["price"].tail(12).median())
    if global_level <= 0:
        return np.repeat(current, len(dates))
    ratios = (by_month / global_level).clip(0.75, 1.35)
    return np.array([current * float(ratios.get(d.month, 1.0)) for d in dates])


def _candidates(history: pd.DataFrame, dates: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    return {
        "robust_ensemble": _level(history, dates),
        "damped_trend": _drift(history, dates),
        "seasonal": _seasonal(history, dates),
    }


def _validate(history: pd.DataFrame, horizon: int) -> tuple[dict[str, float], list[float], int]:
    """Score candidates on rolling origins without leaking future rows."""
    if len(history) < 8:
        return {"robust_ensemble": 1.0}, [], 0
    errors: dict[str, list[float]] = {name: [] for name in _candidates(history, pd.date_range(history.date.iloc[-1], periods=1))}
    residuals: list[float] = []
    folds = 0
    step = max(1, min(horizon, 4))
    start = max(6, len(history) - 36)
    for end in range(start, len(history), step):
        if end >= len(history):
            break
        train = history.iloc[:end]
        test = history.iloc[end : min(len(history), end + horizon)]
        if test.empty:
            continue
        preds = _candidates(train, pd.DatetimeIndex(test["date"]))
        actual = test["price"].to_numpy(float)
        for name, values in preds.items():
            errors[name].extend(np.abs(actual - values).tolist())
        residuals.extend((actual - preds["robust_ensemble"]).tolist())
        folds += 1
    scores = {name: float(np.mean(values)) for name, values in errors.items() if values}
    return scores or {"robust_ensemble": 1.0}, residuals, folds


def forecast_series(series: pd.DataFrame, horizon: int) -> ForecastResult:
    """Return a daily forecast with rolling-origin-selected candidates."""
    history = _clean(series)
    if history.empty:
        raise ValueError("Cannot forecast an empty price series")
    anchor = max(history["date"].max(), pd.Timestamp.now().normalize())
    dates = pd.date_range(anchor + pd.Timedelta(days=1), periods=horizon, freq="D")
    scores, residuals, folds = _validate(history, horizon)
    candidates = _candidates(history, dates)
    names = list(candidates)
    weights = np.array([1.0 / max(scores.get(name, max(scores.values())), 1e-6) for name in names])
    weights /= weights.sum()
    yhat = np.maximum(0.0, sum(weight * candidates[name] for weight, name in zip(weights, names)))
    scale = float(np.quantile(np.abs(residuals), 0.8)) if residuals else float(history["price"].tail(6).std() or history["price"].iloc[-1] * 0.1)
    scale = max(scale, float(history["price"].iloc[-1]) * 0.02)
    growth = np.sqrt(np.arange(1, horizon + 1))
    frame = pd.DataFrame({
        "ds": dates,
        "yhat": yhat,
        "yhat_lower": np.maximum(0.0, yhat - scale * growth),
        "yhat_upper": yhat + scale * growth,
    })
    chosen = "ensemble" if len(set(names)) > 1 else names[0]
    return ForecastResult(frame, chosen, min(scores.values()), folds)
