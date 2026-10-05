"""
Price forecasting models: XGBoost primary, Prophet fallback.

Why two models (see README "Price Forecasting" section):
- XGBoost uses the engineered lag/rolling/temporal features and is
  the primary forecaster. It needs a few months of per-crop/market
  history before its lag features stop being NaN.
- Prophet only needs a single (date, price) series, so it's the
  fallback for thin-history crop/market pairs where XGBoost's
  features would mostly be missing. It also gives a confidence
  interval for free, which the README lists as a use case.
"""
from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score
from xgboost import XGBRegressor

from .features import build_feature_matrix

FEATURE_COLUMNS = [
    "year",
    "month_sin",
    "month_cos",
    "day_of_year",
    "price_lag_1m",
    "price_lag_3m",
    "price_lag_6m",
    "price_roll_3m_avg",
    "crop_encoded",
    "market_encoded",
]


class PriceForecastModel:
    def __init__(self):
        self.model = XGBRegressor(
            n_estimators=400,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
        )
        self.encoders = None

    def train(self, raw_df: pd.DataFrame) -> dict:
        df, encoders = build_feature_matrix(raw_df)
        self.encoders = encoders

        # Time-ordered 80/20 split -- never shuffle price history, or
        # the model "sees the future" during validation and the
        # metrics.json numbers become meaningless.
        df = df.sort_values("date")
        split_idx = int(len(df) * 0.8)
        train_df, test_df = df.iloc[:split_idx], df.iloc[split_idx:]

        X_train, y_train = train_df[FEATURE_COLUMNS], train_df["price"]
        X_test, y_test = test_df[FEATURE_COLUMNS], test_df["price"]

        self.model.fit(X_train, y_train)
        preds = self.model.predict(X_test)

        return {
            "mae": float(mean_absolute_error(y_test, preds)),
            "mape": float(mean_absolute_percentage_error(y_test, preds)),
            "r2": float(r2_score(y_test, preds)),
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
        }

    def predict(self, feature_row: pd.DataFrame) -> float:
        return float(self.model.predict(feature_row[FEATURE_COLUMNS])[0])

    def save(self, path: Path) -> None:
        joblib.dump({"model": self.model, "encoders": self.encoders}, path)

    @classmethod
    def load(cls, path: Path) -> "PriceForecastModel":
        payload = joblib.load(path)
        instance = cls()
        instance.model = payload["model"]
        instance.encoders = payload["encoders"]
        return instance


class ProphetForecastModel:
    """Single-series fallback for crop/market pairs with too little history."""

    def __init__(self):
        self._model = None

    def train(self, series_df: pd.DataFrame):
        from prophet import Prophet  # optional dependency, imported lazily

        prophet_df = series_df.rename(columns={"date": "ds", "price": "y"})[["ds", "y"]]
        self._model = Prophet(interval_width=0.8, yearly_seasonality=True)
        self._model.fit(prophet_df)
        return self._model

    def predict(self, periods_weeks: int = 4) -> pd.DataFrame:
        if self._model is None:
            raise RuntimeError("ProphetForecastModel.train() must be called first")
        future = self._model.make_future_dataframe(periods=periods_weeks, freq="W")
        forecast = self._model.predict(future)
        return forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(periods_weeks)
