# backend/app/services/forecast_service.py
#
# NOTE: not currently wired into the running app — backend/app/main.py uses
# backend.app.model.predict_price() + backend.app.routers.forecasts directly
# and never imports this module or the package-level ForecastService it's
# re-exported as (see services/__init__.py). It previously imported a
# top-level `app.config` module that doesn't exist anywhere in this repo
# (the real settings live at backend.app.core.config, and this class never
# actually used `settings` once imported) — that import error meant
# `import backend.app.services` crashed outright. Fixed here so the package
# is at least importable; still unused by the live API.

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from prophet import Prophet
import joblib
import os
from pathlib import Path


class ForecastService:
    """
    Service responsible for agricultural price forecasting using Prophet
    and other time series models.
    """

    def __init__(self):
        self.model_dir = Path("ml/saved_models/forecast")
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.data_path = Path("data/raw/wfp_food_prices_uga.csv")

    def load_data(self) -> pd.DataFrame:
        """Load and preprocess the WFP Uganda food prices dataset."""
        if not self.data_path.exists():
            raise FileNotFoundError(f"Dataset not found at {self.data_path}")

        df = pd.read_csv(self.data_path)

        # Standard WFP columns preprocessing
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')

        # Basic cleaning
        df = df.dropna(subset=['price'])
        df['price'] = pd.to_numeric(df['price'], errors='coerce')

        return df

    def get_historical_prices(
        self,
        commodity: str,
        market: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """Get historical price data for a specific commodity/market."""
        df = self.load_data()

        # Filter by commodity
        df = df[df['commodity'].str.contains(commodity, case=False, na=False)]

        if market:
            df = df[df['market'].str.contains(market, case=False, na=False)]

        if start_date:
            df = df[df['date'] >= start_date]
        if end_date:
            df = df[df['date'] <= end_date]

        return df[['date', 'price', 'commodity', 'market']].copy()

    def train_and_save_model(
        self,
        commodity: str,
        market: Optional[str] = None,
        periods: int = 365
    ) -> str:
        """Train Prophet model and save it."""
        df = self.get_historical_prices(commodity, market)

        if len(df) < 30:
            raise ValueError(f"Not enough data for {commodity} in {market or 'all markets'}")

        # Prepare data for Prophet (needs columns: ds, y)
        prophet_df = df[['date', 'price']].rename(columns={'date': 'ds', 'price': 'y'})

        # Train model
        model = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=True,
            daily_seasonality=False,
            seasonality_mode='multiplicative'
        )
        model.fit(prophet_df)

        # Save model
        model_path = self.model_dir / f"prophet_{commodity.lower().replace(' ', '_')}.joblib"
        joblib.dump(model, model_path)

        return str(model_path)

    def forecast(
        self,
        commodity: str,
        market: Optional[str] = None,
        days_ahead: int = 30,
        return_components: bool = False
    ) -> Dict[str, Any]:
        """
        Generate price forecast for a commodity.
        """
        model_path = self.model_dir / f"prophet_{commodity.lower().replace(' ', '_')}.joblib"

        # Train if model doesn't exist
        if not model_path.exists():
            self.train_and_save_model(commodity, market, periods=days_ahead)

        model = joblib.load(model_path)

        # Create future dataframe
        future = model.make_future_dataframe(periods=days_ahead)

        # Predict
        forecast = model.predict(future)

        # Prepare response
        last_date = forecast['ds'].max() - timedelta(days=days_ahead)
        future_forecast = forecast[forecast['ds'] > last_date].copy()

        result = {
            "commodity": commodity,
            "market": market or "National Average",
            "forecast": [
                {
                    "date": row['ds'].strftime('%Y-%m-%d'),
                    "predicted_price": round(float(row['yhat']), 2),
                    "lower_bound": round(float(row['yhat_lower']), 2),
                    "upper_bound": round(float(row['yhat_upper']), 2),
                }
                for _, row in future_forecast.iterrows()
            ],
            "last_historical_price": float(forecast.iloc[-days_ahead-1]['yhat']),
            "trend": "up" if future_forecast['yhat'].iloc[-1] > future_forecast['yhat'].iloc[0] else "down"
        }

        if return_components:
            result["components"] = model.plot_components(forecast)

        return result

    def get_forecast_summary(self, commodities: List[str]) -> List[Dict]:
        """Get short summary forecast for multiple commodities."""
        summaries = []
        for commodity in commodities:
            try:
                forecast = self.forecast(commodity, days_ahead=14)
                summaries.append({
                    "commodity": commodity,
                    "predicted_price_14d": forecast["forecast"][-1]["predicted_price"],
                    "trend": forecast["trend"]
                })
            except Exception as e:
                summaries.append({"commodity": commodity, "error": str(e)})
        return summaries


# Singleton instance
forecast_service = ForecastService()