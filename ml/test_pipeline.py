import numpy as np
import pandas as pd

from ml.pipeline import forecast_series


def test_forecast_is_daily_nonnegative_and_uncertainty_widens():
    dates = pd.date_range("2023-01-01", periods=30, freq="30D")
    prices = 1000 + np.arange(len(dates)) * 4
    result = forecast_series(pd.DataFrame({"date": dates, "price": prices}), 14)

    assert len(result.forecast) == 14
    assert result.forecast["ds"].diff().dropna().dt.days.eq(1).all()
    assert (result.forecast["yhat"] >= 0).all()
    widths = result.forecast["yhat_upper"] - result.forecast["yhat_lower"]
    assert widths.iloc[-1] > widths.iloc[0]
    assert result.folds > 0
