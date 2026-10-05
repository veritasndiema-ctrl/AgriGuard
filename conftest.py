"""Root pytest config: tests always read the CSV committed in the repo, never
whatever AGRIGUARD_PRICE_DATA happens to point to on a developer machine."""
import os
from pathlib import Path

os.environ["AGRIGUARD_PRICE_DATA"] = str(
    Path(__file__).resolve().parent / "data" / "raw" / "wfp_food_prices_uga.csv"
)
