"""
app/routers/markets.py — AgriGuard Market Price Intelligence Router
===================================================================
FastAPI router for real-time agricultural market price intelligence.

Provides:
  - Cross-market price comparisons for any commodity
  - Market-level commodity overviews
  - Historical price series (for charts / sparklines)
  - Top price movers (gainers and losers) across all markets
  - Arbitrage opportunity detection between markets
  - National price summary across all commodities

Endpoints:
  GET /markets/summary/{commodity}       → best/worst market + recommendation
  GET /markets/overview/{market}         → all commodities in one market
  GET /markets/history/{commodity}       → historical price series
  GET /markets/movers                    → biggest gainers and losers
  GET /markets/compare/{commodity}       → side-by-side market comparison
  GET /markets/arbitrage/{commodity}     → NEW: profit opportunity analysis
  GET /markets/national-summary          → NEW: snapshot across all commodities

Design notes:
  - load_price_data() is shared with forecasts.py. In production, move it to
    app/services/price_service.py and import from both routers — this avoids
    loading the CSV twice per request. Kept inline here for MVP clarity.
  - All monetary values use round(x, 2). Never return raw float precision to
    the client (e.g. 1249.9999999996).
  - Market list queries are capped at MAX_MARKETS to prevent abuse.
  - Trend computation always uses the last TREND_WINDOW observations so the
    result is not distorted by ancient data points.

Changes from v1:
  - Shared load_price_data() now matches forecasts.py exactly (same column
    normalisation, price_type retail preference, env-var DATA_PATH)
  - Extracted _get_market_price() — was duplicated across 4 routes
  - compute_trend() now uses TREND_WINDOW constant, not a magic "12"
  - Added /arbitrage/{commodity} endpoint
  - Added /national-summary endpoint
  - compare_prices() now deduplicates the market list and caps at MAX_MARKETS
  - build_sell_recommendation() now includes transport cost hint
  - MarketPrice gains `days_since_update` field — staleness warning for dashboard
  - TopMoverItem gains `alert_level` ("high" | "medium" | "low") based on magnitude
  - All bare except replaced with explicit types + structured logging

Author: AgriGuard Team
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.app.services import fews_net_sync
from backend.app.services.food_scope import filter_food_only

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/markets", tags=["Markets"])

# ── Configuration constants ───────────────────────────────────────────────────

DATA_PATH: str = os.environ.get(
    "AGRIGUARD_PRICE_DATA",
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "data", "raw", "wfp_food_prices_uga.csv"
    ),
)

# 12 bi-weekly WFP observations ≈ 6 months of data — enough to see a turn.
TREND_WINDOW: int = 12

# Maximum markets allowed in a single comparison / arbitrage request.
MAX_MARKETS: int = 10

# Price change % thresholds for alert severity levels.
ALERT_HIGH_PCT: float = 15.0
ALERT_MEDIUM_PCT: float = 7.0


# =============================================================================
# Pydantic schemas
# =============================================================================

class MarketPrice(BaseModel):
    """Latest price snapshot for one commodity in one market."""
    market: str
    region: Optional[str]
    latest_price: float
    currency: str
    unit: str
    date_recorded: str
    days_since_update: int          # Staleness indicator for the dashboard
    price_30d_ago: Optional[float]
    price_change_pct: Optional[float]
    trend: str                      # "rising" | "falling" | "stable"
    data_points: int                # Total historical records for this pair


class CommodityMarketSummary(BaseModel):
    """Cross-market price summary for one commodity."""
    commodity: str
    markets: list[MarketPrice]      # Sorted highest → lowest price
    best_market_to_sell: str
    worst_market_to_sell: str
    price_spread: float             # max − min across markets (absolute)
    price_spread_pct: float         # spread as % of national average
    national_avg_price: float
    currency: str
    unit: str
    recommendation: str
    generated_at: str


class MarketOverview(BaseModel):
    """All commodities tracked in a single market."""
    market: str
    region: Optional[str]
    total_commodities_tracked: int
    commodities: list[dict]         # [{commodity, latest_price, currency, unit, trend, date}]
    generated_at: str


class PriceHistoryPoint(BaseModel):
    """A single dated price observation."""
    date: str
    price: float
    market: str


class PriceHistoryResponse(BaseModel):
    """Historical price series for one commodity × market pair."""
    commodity: str
    market: str
    currency: str
    unit: str
    history: list[PriceHistoryPoint]
    min_price: float
    max_price: float
    avg_price: float
    volatility_pct: float           # Coefficient of variation × 100


class TopMoverItem(BaseModel):
    """A commodity × market pair with a notable recent price movement."""
    commodity: str
    market: str
    latest_price: float
    previous_price: float
    change_pct: float
    direction: str                  # "up" | "down"
    alert_level: str                # "high" | "medium" | "low"
    currency: str


class TopMoversResponse(BaseModel):
    """Biggest price gainers and losers over a given period."""
    gainers: list[TopMoverItem]
    losers: list[TopMoverItem]
    period_days: int
    generated_at: str


class ArbitrageOpportunity(BaseModel):
    """
    Profit opportunity from buying in one market and selling in another.
    Does NOT account for transport cost — the recommendation string does.
    """
    commodity: str
    buy_market: str
    sell_market: str
    buy_price: float
    sell_price: float
    gross_margin: float             # sell_price − buy_price
    gross_margin_pct: float         # gross_margin / buy_price × 100
    currency: str
    unit: str
    viable: bool                    # True if margin likely exceeds typical transport cost
    note: str


class NationalCommoditySummary(BaseModel):
    """One row in the national price snapshot."""
    commodity: str
    national_avg_price: float
    min_price: float
    max_price: float
    price_spread_pct: float
    trend: str
    markets_tracked: int
    currency: str
    unit: str


class NationalSummaryResponse(BaseModel):
    """Snapshot of all commodity prices at the national level."""
    commodities: list[NationalCommoditySummary]
    data_as_of: str
    generated_at: str


# =============================================================================
# Data loading (mirrors forecasts.py — move to price_service.py in production)
# =============================================================================

def load_price_data() -> pd.DataFrame:
    """
    Load and normalise the WFP Uganda price CSV.

    Handles multiple WFP column name variants and prefers retail prices
    over wholesale when both are present.

    Raises:
        HTTPException 503 — file not found.
        HTTPException 500 — required columns missing after normalisation.
    """
    try:
        df = pd.read_csv(DATA_PATH, low_memory=False)
    except FileNotFoundError:
        logger.error("WFP price dataset not found at %s", DATA_PATH)
        raise HTTPException(
            status_code=503,
            detail=(
                "Price dataset not found. "
                "Ensure wfp_food_prices_uga.csv is in data/raw/ "
                "or set the AGRIGUARD_PRICE_DATA environment variable."
            ),
        )

    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    rename_map: dict[str, str] = {}
    for col in df.columns:
        if "date" in col and "date" not in rename_map.values():
            rename_map[col] = "date"
        elif col in ("cmname", "commodity", "cm_name", "item") and "commodity" not in rename_map.values():
            rename_map[col] = "commodity"
        elif col in ("mktname", "market", "mkt_name", "market_name") and "market" not in rename_map.values():
            rename_map[col] = "market"
        elif col == "price" and "price" not in rename_map.values():
            rename_map[col] = "price"
        elif col in ("cur", "currency", "currname", "currency_name") and "currency" not in rename_map.values():
            rename_map[col] = "currency"
        elif col in ("unit", "um_name", "umname", "unit_name") and "unit" not in rename_map.values():
            rename_map[col] = "unit"
        elif col in ("adm1name", "region", "admin1") and "region" not in rename_map.values():
            rename_map[col] = "region"
        elif col in ("pricetype", "price_type") and "price_type" not in rename_map.values():
            rename_map[col] = "price_type"

    df.rename(columns=rename_map, inplace=True)

    required = {"date", "commodity", "market", "price"}
    missing = required - set(df.columns)
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"Dataset missing columns after normalisation: {sorted(missing)}. "
                   f"Found: {sorted(df.columns.tolist())}",
        )

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df.dropna(subset=["date", "price"], inplace=True)
    df = df[df["price"] > 0]

    df["commodity"] = df["commodity"].str.strip().str.title()
    df["market"] = df["market"].str.strip().str.title()

    if "currency" not in df.columns:
        df["currency"] = "UGX"
    if "unit" not in df.columns:
        df["unit"] = "KG"
    if "region" not in df.columns:
        df["region"] = None

    # Prefer retail prices
    if "price_type" in df.columns:
        retail = df[df["price_type"].str.lower() == "retail"]
        if not retail.empty:
            df = retail

    # Food-only scope — see services/food_scope.py. Without this,
    # non-food WFP items (soap, batteries, firewood, ...) still show up in
    # this router's commodity counts, movers, and national-summary even
    # after routers/forecasts.py stopped forecasting them.
    df = filter_food_only(df, source_label="markets: WFP")

    df["source"] = "WFP"
    df = df.reset_index(drop=True)

    # Blend in the fresher-cadence FEWS NET feed (see
    # backend/app/services/fews_net_sync.py and forecasts.py::load_price_data
    # for the same pattern with fuller commentary). On overlap FEWS NET wins;
    # FEWS NET doesn't carry a region column, so those rows simply have
    # region=None like any other WFP row missing region already does.
    fews_path = Path(fews_net_sync.DATA_PATH)
    if fews_path.exists():
        try:
            fews_df = pd.read_csv(fews_path, low_memory=False, parse_dates=["date"])
            if "region" not in fews_df.columns:
                fews_df["region"] = None
            # FEWS NET's extract carries no category column, so this goes
            # through food_scope's keyword-net fallback rather than the WFP
            # category allowlist — same food-only scope, different mechanism.
            fews_df = filter_food_only(fews_df, source_label="markets: FEWS NET")
            fews_df["source"] = "FEWS_NET"
            required_fews = {"date", "commodity", "market", "price"}
            if required_fews.issubset(fews_df.columns) and not fews_df.empty:
                combined = pd.concat([fews_df, df], ignore_index=True)
                df = combined.drop_duplicates(
                    subset=["market", "commodity", "date"], keep="first"
                ).sort_values("date").reset_index(drop=True)
        except Exception as exc:
            logger.warning("FEWS NET dataset present but could not be blended in markets.py: %s", exc)

    return df


# =============================================================================
# Shared helpers
# =============================================================================

def compute_trend(prices: pd.Series, window: int = TREND_WINDOW) -> str:
    """
    Determine price direction from the most recent `window` observations.

    Uses a linear slope normalised by the mean price so the threshold is
    scale-invariant (works for both UGX/kg and UGX/bag_90kg).
    """
    tail = prices.iloc[-window:] if len(prices) >= window else prices
    if len(tail) < 2:
        return "stable"
    slope = np.polyfit(range(len(tail)), tail.values, 1)[0]
    mean_price = tail.mean() or 1e-9
    pct_slope = slope / mean_price
    if pct_slope > 0.005:
        return "rising"
    if pct_slope < -0.005:
        return "falling"
    return "stable"


def _alert_level(change_pct: float) -> str:
    """Classify the magnitude of a price movement."""
    abs_pct = abs(change_pct)
    if abs_pct >= ALERT_HIGH_PCT:
        return "high"
    if abs_pct >= ALERT_MEDIUM_PCT:
        return "medium"
    return "low"


def _get_market_price(
    df: pd.DataFrame,
    commodity: str,
    market: str,
) -> Optional[MarketPrice]:
    """
    Build a MarketPrice snapshot for one commodity × market pair.

    Returns None (rather than raising) so callers can silently skip
    markets with no data in bulk operations (compare, movers, etc.).
    """
    subset = df[
        (df["commodity"].str.lower() == commodity.lower())
        & (df["market"].str.lower() == market.lower())
    ].sort_values("date").reset_index(drop=True)

    if subset.empty:
        return None

    latest_row = subset.iloc[-1]
    latest_price = float(latest_row["price"])
    latest_date = latest_row["date"]

    currency = str(latest_row.get("currency") or "UGX")
    unit = str(latest_row.get("unit") or "KG")
    region = latest_row.get("region") if "region" in subset.columns else None

    # Staleness: how many days since the last price was recorded
    days_since_update = (datetime.utcnow().date() - latest_date.date()).days

    # Price 30 days ago — find the closest observation at or before the cutoff
    cutoff_30d = latest_date - timedelta(days=30)
    past = subset[subset["date"] <= cutoff_30d]
    price_30d: Optional[float] = float(past.iloc[-1]["price"]) if not past.empty else None

    change_pct: Optional[float] = None
    if price_30d and price_30d > 0:
        change_pct = round(((latest_price - price_30d) / price_30d) * 100, 2)

    trend = compute_trend(subset["price"])

    return MarketPrice(
        market=market,
        region=region,
        latest_price=round(latest_price, 2),
        currency=currency,
        unit=unit,
        date_recorded=latest_date.strftime("%Y-%m-%d"),
        days_since_update=days_since_update,
        price_30d_ago=round(price_30d, 2) if price_30d is not None else None,
        price_change_pct=change_pct,
        trend=trend,
        data_points=len(subset),
    )


def build_sell_recommendation(
    commodity: str,
    best_market: str,
    worst_market: str,
    spread: float,
    spread_pct: float,
    avg: float,
    currency: str,
) -> str:
    """
    Return a plain-language, farmer-friendly sell advisory.

    Includes a transport cost reminder — critical context for Ugandan
    farmers deciding whether the price difference justifies travel.
    """
    return (
        f"Sell {commodity} in {best_market} for the best price today. "
        f"Avoid {worst_market} — the gap is {currency} {spread:,.0f} "
        f"({spread_pct:.1f}% difference across markets). "
        f"Always check transport costs before moving your harvest — "
        f"a high price means nothing if the lorry fare eats your margin."
    )


# =============================================================================
# Routes
# =============================================================================

@router.get("/summary/{commodity}", response_model=CommodityMarketSummary)
def commodity_market_summary(
    commodity: str,
    markets: Optional[str] = Query(
        default=None,
        description=(
            "Comma-separated markets to compare. "
            "Defaults to all markets with data for this commodity."
        ),
    ),
):
    """
    Compare current prices for one commodity across markets.

    Returns the best and worst markets to sell in, the national average,
    the price spread, and a plain-language farmer recommendation.

    **Example:** `/markets/summary/Maize?markets=Kampala,Mbarara,Gulu,Kabale`
    """
    df = load_price_data()
    commodity_title = commodity.strip().title()

    available_markets = (
        df[df["commodity"].str.lower() == commodity_title.lower()]["market"]
        .unique()
        .tolist()
    )

    if not available_markets:
        raise HTTPException(
            status_code=404,
            detail=f"No market data found for '{commodity_title}'. "
                   f"Use GET /markets/national-summary to see all available commodities.",
        )

    if markets:
        requested = [m.strip().title() for m in markets.split(",") if m.strip()][:MAX_MARKETS]
        market_list = [m for m in requested if m in available_markets]
        if not market_list:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"None of the requested markets have data for '{commodity_title}'. "
                    f"Available markets: {sorted(available_markets)}"
                ),
            )
    else:
        market_list = available_markets

    market_prices: list[MarketPrice] = []
    for mkt in market_list:
        record = _get_market_price(df, commodity_title, mkt)
        if record:
            market_prices.append(record)

    if not market_prices:
        raise HTTPException(
            status_code=404,
            detail=f"Could not retrieve current price data for '{commodity_title}'.",
        )

    prices_values = [m.latest_price for m in market_prices]
    currency = market_prices[0].currency
    unit = market_prices[0].unit
    national_avg = round(float(np.mean(prices_values)), 2)
    spread = round(max(prices_values) - min(prices_values), 2)
    spread_pct = round((spread / (national_avg or 1)) * 100, 1)

    best = max(market_prices, key=lambda m: m.latest_price)
    worst = min(market_prices, key=lambda m: m.latest_price)

    recommendation = build_sell_recommendation(
        commodity_title, best.market, worst.market,
        spread, spread_pct, national_avg, currency,
    )

    logger.info(
        "Market summary | commodity=%s markets=%d best=%s worst=%s spread_pct=%.1f",
        commodity_title, len(market_prices), best.market, worst.market, spread_pct,
    )

    return CommodityMarketSummary(
        commodity=commodity_title,
        markets=sorted(market_prices, key=lambda m: m.latest_price, reverse=True),
        best_market_to_sell=best.market,
        worst_market_to_sell=worst.market,
        price_spread=spread,
        price_spread_pct=spread_pct,
        national_avg_price=national_avg,
        currency=currency,
        unit=unit,
        recommendation=recommendation,
        generated_at=datetime.utcnow().isoformat() + "Z",
    )


@router.get("/overview/{market}", response_model=MarketOverview)
def market_overview(market: str):
    """
    Full price overview of all commodities currently tracked in one market.

    Useful for the "market dashboard" view — a trader in Gulu can see
    everything priced there in one call.

    **Example:** `/markets/overview/Gulu`
    """
    df = load_price_data()
    market_title = market.strip().title()

    subset = df[df["market"].str.lower() == market_title.lower()]
    if subset.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for market '{market_title}'. "
                   f"Use GET /forecasts/commodities to see available markets.",
        )

    region = subset["region"].iloc[-1] if "region" in subset.columns else None
    commodities_in_market = sorted(subset["commodity"].unique().tolist())

    commodity_summaries = []
    for comm in commodities_in_market:
        record = _get_market_price(df, comm, market_title)
        if record:
            commodity_summaries.append(
                {
                    "commodity": comm,
                    "latest_price": record.latest_price,
                    "currency": record.currency,
                    "unit": record.unit,
                    "trend": record.trend,
                    "date_recorded": record.date_recorded,
                    "days_since_update": record.days_since_update,
                }
            )

    return MarketOverview(
        market=market_title,
        region=region,
        total_commodities_tracked=len(commodity_summaries),
        commodities=commodity_summaries,
        generated_at=datetime.utcnow().isoformat() + "Z",
    )


@router.get("/history/{commodity}", response_model=PriceHistoryResponse)
def price_history(
    commodity: str,
    market: str = Query(default="Kampala", description="Market name"),
    days: int = Query(
        default=180,
        ge=30,
        le=1825,
        description="History window in days (30–1825)",
    ),
):
    """
    Fetch historical price series for a commodity in a specific market.

    Used to power line charts and trend visualisations in the dashboard.
    Returns summary statistics (min, max, avg, volatility) alongside
    the raw series.

    **Example:** `/markets/history/Beans?market=Mbarara&days=365`
    """
    df = load_price_data()
    commodity_title = commodity.strip().title()
    market_title = market.strip().title()

    cutoff = df["date"].max() - timedelta(days=days)
    subset = df[
        (df["commodity"].str.lower() == commodity_title.lower())
        & (df["market"].str.lower() == market_title.lower())
        & (df["date"] >= cutoff)
    ].sort_values("date").reset_index(drop=True)

    if subset.empty:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No historical data for '{commodity_title}' in '{market_title}' "
                f"in the last {days} days. "
                f"Try a longer window or use GET /forecasts/commodities to confirm availability."
            ),
        )

    currency = str(subset.iloc[-1].get("currency") or "UGX")
    unit = str(subset.iloc[-1].get("unit") or "KG")
    prices_arr = subset["price"].values
    mean_price = float(np.mean(prices_arr)) or 1e-9
    volatility = round(float(np.std(prices_arr) / mean_price) * 100, 2)

    history = [
        PriceHistoryPoint(
            date=row["date"].strftime("%Y-%m-%d"),
            price=round(float(row["price"]), 2),
            market=market_title,
        )
        for _, row in subset.iterrows()
    ]

    return PriceHistoryResponse(
        commodity=commodity_title,
        market=market_title,
        currency=currency,
        unit=unit,
        history=history,
        min_price=round(float(prices_arr.min()), 2),
        max_price=round(float(prices_arr.max()), 2),
        avg_price=round(float(prices_arr.mean()), 2),
        volatility_pct=volatility,
    )


@router.get("/movers", response_model=TopMoversResponse)
def top_movers(
    period_days: int = Query(
        default=30,
        ge=7,
        le=90,
        description="Lookback period in days (7–90)",
    ),
    top_n: int = Query(
        default=5,
        ge=1,
        le=20,
        description="Number of top movers to return per direction",
    ),
):
    """
    Returns the biggest price gainers and losers across ALL commodities
    and markets over the past `period_days`.

    Drives the AgriGuard dashboard alert feed — shows farmers and traders
    which prices are moving fast right now.

    **Example:** `/markets/movers?period_days=14&top_n=5`
    """
    df = load_price_data()
    latest_date = df["date"].max()
    cutoff = latest_date - timedelta(days=period_days)

    mover_rows = []
    for (commodity, market), group in df.groupby(["commodity", "market"]):
        group = group.sort_values("date")
        recent = group[group["date"] >= cutoff]
        past = group[group["date"] < cutoff]

        if recent.empty or past.empty:
            continue

        latest_p = float(recent.iloc[-1]["price"])
        prev_p = float(past.iloc[-1]["price"])

        if prev_p <= 0:
            continue

        change_pct = round(((latest_p - prev_p) / prev_p) * 100, 2)
        currency = str(recent.iloc[-1].get("currency") or "UGX")

        mover_rows.append(
            {
                "commodity": commodity,
                "market": market,
                "latest_price": round(latest_p, 2),
                "previous_price": round(prev_p, 2),
                "change_pct": change_pct,
                "direction": "up" if change_pct >= 0 else "down",
                "alert_level": _alert_level(change_pct),
                "currency": currency,
            }
        )

    if not mover_rows:
        raise HTTPException(
            status_code=404,
            detail=f"Not enough data to compute movers over the last {period_days} days.",
        )

    movers_df = pd.DataFrame(mover_rows)

    gainers = (
        movers_df[movers_df["direction"] == "up"]
        .sort_values("change_pct", ascending=False)
        .head(top_n)
        .to_dict(orient="records")
    )
    losers = (
        movers_df[movers_df["direction"] == "down"]
        .sort_values("change_pct", ascending=True)
        .head(top_n)
        .to_dict(orient="records")
    )

    logger.info(
        "Top movers | period=%dd gainers=%d losers=%d",
        period_days, len(gainers), len(losers),
    )

    return TopMoversResponse(
        gainers=[TopMoverItem(**g) for g in gainers],
        losers=[TopMoverItem(**l) for l in losers],
        period_days=period_days,
        generated_at=datetime.utcnow().isoformat() + "Z",
    )


@router.get("/compare/{commodity}", response_model=list[MarketPrice])
def compare_prices(
    commodity: str,
    markets: str = Query(
        default="Kampala,Mbarara,Gulu,Kabale,Jinja",
        description=f"Comma-separated markets to compare (max {MAX_MARKETS})",
    ),
):
    """
    Side-by-side latest price comparison for one commodity across markets.

    Results are sorted highest → lowest so the farmer immediately sees
    the best place to sell at the top.

    **Example:** `/markets/compare/Maize?markets=Kampala,Mbarara,Gulu,Kabale`
    """
    df = load_price_data()
    commodity_title = commodity.strip().title()

    # Deduplicate and cap the market list
    market_list = list(dict.fromkeys(
        m.strip().title() for m in markets.split(",") if m.strip()
    ))[:MAX_MARKETS]

    results: list[MarketPrice] = []
    for mkt in market_list:
        record = _get_market_price(df, commodity_title, mkt)
        if record:
            results.append(record)

    if not results:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No price data found for '{commodity_title}' "
                f"in any of the requested markets: {market_list}. "
                f"Use GET /forecasts/commodities to see available options."
            ),
        )

    return sorted(results, key=lambda m: m.latest_price, reverse=True)


@router.get("/arbitrage/{commodity}", response_model=list[ArbitrageOpportunity])
def arbitrage_opportunities(
    commodity: str,
    markets: str = Query(
        default="Kampala,Mbarara,Gulu,Kabale,Jinja,Mbale",
        description=f"Comma-separated markets to analyse (max {MAX_MARKETS})",
    ),
    min_margin_pct: float = Query(
        default=10.0,
        ge=1.0,
        le=100.0,
        description="Minimum gross margin % to include in results",
    ),
):
    """
    Identify buy-low / sell-high opportunities between markets.

    For each pair of markets, calculates the gross margin from buying in
    the cheaper market and selling in the more expensive one.

    Only returns pairs where the gross margin exceeds `min_margin_pct`.
    Results are sorted by margin descending — biggest opportunity first.

    ⚠️ **Important:** Gross margin does not account for transport costs,
    spoilage, or market access. Always verify before acting.

    **Example:** `/markets/arbitrage/Maize?markets=Kampala,Mbarara,Gulu,Kabale`
    """
    df = load_price_data()
    commodity_title = commodity.strip().title()

    market_list = list(dict.fromkeys(
        m.strip().title() for m in markets.split(",") if m.strip()
    ))[:MAX_MARKETS]

    # Collect all available prices
    snapshots: list[MarketPrice] = []
    for mkt in market_list:
        record = _get_market_price(df, commodity_title, mkt)
        if record:
            snapshots.append(record)

    if len(snapshots) < 2:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Need price data for at least 2 markets to compute arbitrage. "
                f"Got data for: {[s.market for s in snapshots]}."
            ),
        )

    opportunities: list[ArbitrageOpportunity] = []
    currency = snapshots[0].currency
    unit = snapshots[0].unit

    # Evaluate every buy × sell pair (excluding same-market)
    for i, buy_snap in enumerate(snapshots):
        for sell_snap in snapshots:
            if buy_snap.market == sell_snap.market:
                continue
            if sell_snap.latest_price <= buy_snap.latest_price:
                continue  # No positive margin

            gross_margin = sell_snap.latest_price - buy_snap.latest_price
            gross_margin_pct = round((gross_margin / buy_snap.latest_price) * 100, 1)

            if gross_margin_pct < min_margin_pct:
                continue

            # Rough viability heuristic: margins below 20 % are often eaten
            # by Uganda's transport costs for inter-district travel.
            viable = gross_margin_pct >= 20.0
            note = (
                f"Strong arbitrage opportunity — verify current transport cost "
                f"from {buy_snap.market} to {sell_snap.market} before acting."
                if viable
                else
                f"Margin may be absorbed by transport costs. "
                f"Only viable if {buy_snap.market} and {sell_snap.market} are nearby."
            )

            opportunities.append(
                ArbitrageOpportunity(
                    commodity=commodity_title,
                    buy_market=buy_snap.market,
                    sell_market=sell_snap.market,
                    buy_price=buy_snap.latest_price,
                    sell_price=sell_snap.latest_price,
                    gross_margin=round(gross_margin, 2),
                    gross_margin_pct=gross_margin_pct,
                    currency=currency,
                    unit=unit,
                    viable=viable,
                    note=note,
                )
            )

    if not opportunities:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No arbitrage opportunities found for '{commodity_title}' "
                f"with a margin above {min_margin_pct}% across the requested markets."
            ),
        )

    return sorted(opportunities, key=lambda o: o.gross_margin_pct, reverse=True)


@router.get("/national-summary", response_model=NationalSummaryResponse)
def national_summary():
    """
    National price snapshot across all tracked commodities.

    Returns one row per commodity with the national average, min, max,
    spread, and trend — computed across all markets in the dataset.

    Useful as the AgriGuard home screen summary and for MAAIF reporting.

    **Example:** `/markets/national-summary`
    """
    df = load_price_data()
    latest_date = df["date"].max()

    # Use only the last 60 days so "latest" prices are actually current
    cutoff = latest_date - timedelta(days=60)
    recent_df = df[df["date"] >= cutoff]

    results: list[NationalCommoditySummary] = []

    for commodity, group in recent_df.groupby("commodity"):
        prices_arr = group["price"].values
        if len(prices_arr) == 0:
            continue

        mean_p = float(np.mean(prices_arr))
        min_p = float(np.min(prices_arr))
        max_p = float(np.max(prices_arr))
        spread_pct = round(((max_p - min_p) / (mean_p or 1)) * 100, 1)
        markets_tracked = int(group["market"].nunique())
        trend = compute_trend(group.sort_values("date")["price"])
        currency = str(group.iloc[-1].get("currency") or "UGX")
        unit = str(group.iloc[-1].get("unit") or "KG")

        results.append(
            NationalCommoditySummary(
                commodity=commodity,
                national_avg_price=round(mean_p, 2),
                min_price=round(min_p, 2),
                max_price=round(max_p, 2),
                price_spread_pct=spread_pct,
                trend=trend,
                markets_tracked=markets_tracked,
                currency=currency,
                unit=unit,
            )
        )

    if not results:
        raise HTTPException(
            status_code=404,
            detail="No recent price data available for national summary.",
        )

    results.sort(key=lambda r: r.commodity)

    logger.info("National summary generated | commodities=%d", len(results))

    return NationalSummaryResponse(
        commodities=results,
        data_as_of=latest_date.strftime("%Y-%m-%d"),
        generated_at=datetime.utcnow().isoformat() + "Z",
    )