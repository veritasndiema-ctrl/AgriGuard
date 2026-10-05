import pandas as pd
import numpy as np
from prophet import Prophet
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error
import joblib
from pathlib import Path

def load_and_clean(path: str, commodity: str, market: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df = df[
        (df["commodity"].str.lower() == commodity.lower()) &
        (df["market"].str.lower() == market.lower())
    ].copy()
    
    df = df[["date", "price"]].dropna()
    df = df.rename(columns={"date": "ds", "price": "y"})
    df = df.sort_values("ds").reset_index(drop=True)
    return df

def train_and_evaluate(df: pd.DataFrame) -> tuple:
    # 80/20 train-test split
    split = int(len(df) * 0.8)
    train, test = df[:split], df[split:]
    
    model = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        seasonality_mode="multiplicative",  # prices are multiplicative
    )
    model.add_seasonality(name="monthly", period=30.5, fourier_order=5)
    model.fit(train)
    
    # Evaluate on test set
    future = model.make_future_dataframe(periods=len(test), freq="W")
    forecast = model.predict(future)
    preds = forecast.tail(len(test))["yhat"].values
    
    mae = mean_absolute_error(test["y"], preds)
    mape = mean_absolute_percentage_error(test["y"], preds)
    print(f"MAE: {mae:.2f} UGX | MAPE: {mape:.2%}")
    
    # Retrain on full data
    model.fit(df)
    return model, {"mae": mae, "mape": mape}

def save_model(model, commodity: str, market: str):
    out_dir = Path("ml/models")
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = f"{commodity}_{market}".lower().replace(" ", "_")
    joblib.dump(model, out_dir / f"{slug}_prophet.pkl")
    print(f"Model saved: {slug}_prophet.pkl")

if __name__ == "__main__":
    df = load_and_clean(
        "data/raw/wfp_food_prices_uga.csv",
        commodity="Maize",
        market="Kampala"
    )
    print(f"Training on {len(df)} records from {df['ds'].min()} to {df['ds'].max()}")
    model, metrics = train_and_evaluate(df)
    save_model(model, "maize", "kampala")