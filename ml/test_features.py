import pandas as pd

from quant.features import add_lag_features


def test_rolling_features_only_use_prior_observations():
    frame = pd.DataFrame(
        {
            "commodity": ["Maize"] * 3,
            "market": ["Owino"] * 3,
            "date": pd.date_range("2024-01-01", periods=3, freq="D"),
            "price": [10.0, 20.0, 30.0],
        }
    )
    result = add_lag_features(frame, lag_steps=(1,), roll_window=2)
    assert pd.isna(result.loc[0, "price_roll_2_avg"])
    assert result.loc[2, "price_roll_2_avg"] == 15.0
