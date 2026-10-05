"""
scripts/build_quant_features.py — Production feature generation for quant/
================================================================================
Fills the gap `quant/README.md` used to paper over: before this script,
`data/processed/features_{tier}.parquet` was only ever produced by manually
running `notebooks/02_feature_engineering.ipynb`, so `quant/backtesting.py`
had no real production input — only a notebook-only one. This script is that
production input.

Reuses `scripts/train_models.py::load_and_clean` for cleaning (the one
pipeline actually wired into the live API — see `ml/README.md`), so the
price data quant/ backtests against is cleaned identically to what the live
point-prediction model trains on. Feature *construction* on top of that
(tiered lags, one row set per horizon) is quant-specific and lives in
`quant/features.py` — see that module's docstring for why it's deliberately
separate from `train_models.py::build_features`.

Usage:
    python scripts/build_quant_features.py
    python scripts/build_quant_features.py --data data/raw/wfp_food_prices_uga.csv \\
        --out data/processed

Output (in --out, default data/processed/):
    features_tier_7_14.parquet
    features_tier_30.parquet
    features_tier_60_90.parquet
    feature_encoders.pkl
    prices_clean.parquet   (also written — quant.risk_metrics needs the
                             cleaned price series, not just tiered features)
"""

import argparse
import sys
from pathlib import Path

# Allow `python scripts/build_quant_features.py` (not just `python -m
# scripts.build_quant_features`) to find the repo-root packages below —
# matches how this script is documented to be run, consistent with
# scripts/train_models.py's own invocation style.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant.features import build_tier_features, save_tier_features  # noqa: E402
from scripts.train_models import load_and_clean  # noqa: E402

DEFAULT_DATA = Path("data/raw/wfp_food_prices_uga.csv")
DEFAULT_OUT = Path("data/processed")


def main(data_path: Path, out_dir: Path) -> None:
    print(f"\n📂 Loading + cleaning: {data_path}")
    prices_clean = load_and_clean(data_path)

    out_dir.mkdir(parents=True, exist_ok=True)
    prices_clean.to_parquet(out_dir / "prices_clean.parquet", index=False)
    print(f"   Saved {out_dir / 'prices_clean.parquet'}")

    print("\n🧮 Building tiered features (quant/features.py)…")
    tier_frames, le_crop, le_market = build_tier_features(prices_clean)
    for tier, frame in tier_frames.items():
        print(f"   {tier:12s} → {len(frame):,} rows")

    save_tier_features(tier_frames, le_crop, le_market, out_dir)
    print(f"\n✅ Wrote features_{{tier}}.parquet + feature_encoders.pkl to {out_dir}")
    print("   Next: python -m quant.backtesting  (or see quant/README.md's Usage section)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build quant/'s tiered feature inputs")
    parser.add_argument("--data", "-d", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out", "-o", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.data.exists():
        raise SystemExit(
            f"❌ Data file not found: {args.data}\n"
            "   Run first:  python scripts/download_wfp_data.py"
        )

    main(args.data, args.out)
