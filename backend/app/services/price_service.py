"""
services/price_service.py — AgriGuard Price Business Logic
============================================================
The service layer sits between the API routers and the database.

Why a service layer?
  - Routers handle HTTP (request/response, status codes)
  - Models handle DB structure (columns, relationships)
  - Services handle BUSINESS LOGIC (calculations, queries, rules)
  - Keeps routers thin and testable
  - The same service can be called by the router, a CLI script,
    or a background task — no HTTP context needed

All raw SQLAlchemy queries live here, not in the routers.

Author: AgriGuard Team
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, and_, desc, select
from sqlalchemy.orm import Session, joinedload

# NOTE (scoped fix, see backend/app/main.py and README "Known issues"):
# this file originally imported from a nonexistent top-level `app` package.
# The weather-specific pieces of that were blocking WeatherReading from
# ever being usable here, so those two lines are corrected to the
# `backend.app.*` root that actually resolves given this repo's real
# WORKDIR/PYTHONPATH (see backend/Dockerfile). `PriceForecast` and the
# schema names below (MarketComparisonItem, PriceTrendPoint) still don't
# exist under those exact names in schemas/price.py — that mismatch predates
# this pass and is a price/forecast-layer issue, not a weather one, so it's
# left as-is rather than silently papered over. This file still can't be
# imported end-to-end until that's reconciled.
from backend.app.core.config import Settings
from backend.app.models.price import (
    Crop,
    CropPrice,
    DataQuality,
    Market,
    PriceUnit,
    UgandaRegion,
)
from backend.app.models.weather import WeatherReading
from backend.app.schemas.price import (
    CropPriceCreate,
    CropPriceUpdate,
    CropSummary,
    MarketComparisonResponse,
    MarketSummary,
    PriceFilterParams,
    PriceTrendResponse,
)


class PriceService:
    """
    All price-related business logic in one class.
    Instantiated per-request via FastAPI dependency injection.
    """

    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    # =========================================================================
    # LOOKUP HELPERS
    # =========================================================================

    def get_crop_or_404(self, crop_id: int) -> Crop:
        """
        Fetch a crop by ID. Raises 404 if not found.
        Used by routers to validate foreign keys before insert.
        """
        crop = self.db.get(Crop, crop_id)
        if not crop:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Crop with id={crop_id} not found. "
                       "Use GET /crops to see available crops.",
            )
        return crop

    def get_market_or_404(self, market_id: int) -> Market:
        """Fetch a market by ID. Raises 404 if not found."""
        market = self.db.get(Market, market_id)
        if not market:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Market with id={market_id} not found. "
                       "Use GET /markets to see available markets.",
            )
        return market

    def get_price_by_id(self, price_id: int) -> Optional[CropPrice]:
        """Fetch a single price record with crop and market pre-loaded."""
        return (
            self.db.query(CropPrice)
            .options(
                joinedload(CropPrice.crop),    # Pre-load crop — avoids N+1 query
                joinedload(CropPrice.market),  # Pre-load market
            )
            .filter(CropPrice.id == price_id)
            .first()
        )

    def find_duplicate(self, price_data: CropPriceCreate) -> Optional[CropPrice]:
        """
        Check if a price record already exists for the same
        crop/market/date/unit combination.
        Used to prevent duplicate submissions.
        """
        return (
            self.db.query(CropPrice)
            .filter(
                CropPrice.price_date == price_data.price_date,
                CropPrice.crop_id == price_data.crop_id,
                CropPrice.market_id == price_data.market_id,
                CropPrice.unit == price_data.unit,
            )
            .first()
        )

    # =========================================================================
    # CORE CRUD
    # =========================================================================

    def get_prices(
        self, filters: PriceFilterParams
    ) -> tuple[list[CropPrice], int]:
        """
        Fetch paginated, filtered price records.
        Returns (list_of_records, total_count).
        The total_count is needed by the API to tell the client
        how many pages exist.
        """
        query = (
            self.db.query(CropPrice)
            .options(
                joinedload(CropPrice.crop),
                joinedload(CropPrice.market),
            )
            # Exclude flagged records from normal listing
            .filter(CropPrice.quality != DataQuality.FLAGGED)
        )

        # Apply optional filters
        if filters.crop_id:
            query = query.filter(CropPrice.crop_id == filters.crop_id)

        if filters.market_id:
            query = query.filter(CropPrice.market_id == filters.market_id)

        if filters.region:
            # Join to markets table to filter by region
            query = query.join(Market).filter(Market.region == filters.region)

        if filters.start_date:
            query = query.filter(CropPrice.price_date >= filters.start_date)

        if filters.end_date:
            query = query.filter(CropPrice.price_date <= filters.end_date)

        if filters.quality:
            query = query.filter(CropPrice.quality == filters.quality)

        # Get total before pagination (for PaginatedResponse.total)
        total = query.count()

        # Apply pagination and ordering (most recent first)
        prices = (
            query
            .order_by(desc(CropPrice.price_date), CropPrice.crop_id)
            .limit(filters.limit)
            .offset(filters.offset)
            .all()
        )

        return prices, total

    def create_price(self, price_data: CropPriceCreate) -> CropPrice:
        """
        Insert a new price record.
        Also computes price_per_kg_ugx if the unit has a known kg conversion.
        """
        # Convert Pydantic schema to dict for the ORM model
        price_dict = price_data.model_dump()

        # Compute normalized price per kg for ML feature engineering
        price_dict["price_per_kg_ugx"] = self._compute_price_per_kg(
            price=price_data.retail_price_ugx or price_data.wholesale_price_ugx,
            unit=price_data.unit,
        )

        new_price = CropPrice(**price_dict)
        self.db.add(new_price)
        self.db.commit()
        self.db.refresh(new_price)

        # Reload with relationships for the response
        return self.get_price_by_id(new_price.id)

    def update_price(
        self, price: CropPrice, update_data: CropPriceUpdate
    ) -> CropPrice:
        """
        Apply partial updates to a price record.
        Only updates fields that were actually provided (not None).
        """
        update_dict = update_data.model_dump(exclude_unset=True)

        for field, value in update_dict.items():
            setattr(price, field, value)

        # Recompute price_per_kg if prices changed
        if "retail_price_ugx" in update_dict or "wholesale_price_ugx" in update_dict:
            price.price_per_kg_ugx = self._compute_price_per_kg(
                price=price.retail_price_ugx or price.wholesale_price_ugx,
                unit=price.unit,
            )

        self.db.commit()
        self.db.refresh(price)
        return price

    def flag_price(self, price: CropPrice, reason: str) -> None:
        """
        Soft-delete: mark a record as FLAGGED with the reason in notes.
        Never hard-delete price data — MAAIF needs the audit trail.
        """
        price.quality = DataQuality.FLAGGED
        # Append reason to notes, preserving existing notes
        existing_notes = price.notes or ""
        price.notes = f"{existing_notes} | FLAGGED: {reason}".strip(" |")
        self.db.commit()

    # =========================================================================
    # ANALYTICS QUERIES
    # =========================================================================

    def get_latest_prices(
        self,
        crop_id: Optional[int] = None,
        region: Optional[UgandaRegion] = None,
    ) -> list[CropPrice]:
        """
        Returns the most recent price record per (crop, market) pair.

        Uses a subquery to find the max date per group, then joins back
        to get the full record. This is more efficient than Python-side
        grouping for large datasets.
        """
        # Subquery: max date per crop/market combo
        subq = (
            self.db.query(
                CropPrice.crop_id,
                CropPrice.market_id,
                func.max(CropPrice.price_date).label("max_date"),
            )
            .filter(CropPrice.quality != DataQuality.FLAGGED)
            .group_by(CropPrice.crop_id, CropPrice.market_id)
            .subquery()
        )

        # Main query: join to subquery to get full records
        query = (
            self.db.query(CropPrice)
            .options(
                joinedload(CropPrice.crop),
                joinedload(CropPrice.market),
            )
            .join(
                subq,
                and_(
                    CropPrice.crop_id == subq.c.crop_id,
                    CropPrice.market_id == subq.c.market_id,
                    CropPrice.price_date == subq.c.max_date,
                ),
            )
        )

        if crop_id:
            query = query.filter(CropPrice.crop_id == crop_id)

        if region:
            query = query.join(Market).filter(Market.region == region)

        return query.order_by(CropPrice.crop_id, CropPrice.market_id).all()

    def get_price_trend(
        self,
        crop_id: int,
        market_id: Optional[int],
        start_date: date,
        end_date: date,
        interval: str = "week",
    ) -> PriceTrendResponse:
        """
        Computes a time-series of average prices aggregated by interval.

        Aggregation is done in the DB (not Python) for performance.
        PostgreSQL's date_trunc() groups dates into week/month buckets.

        Returns PriceTrendResponse ready to be serialised by FastAPI.
        """
        # PostgreSQL date_trunc truncates a date to the start of its period
        # e.g. date_trunc('week', '2024-03-15') → '2024-03-11' (Monday)
        date_bucket = func.date_trunc(interval, CropPrice.price_date).label("bucket")

        query = (
            self.db.query(
                date_bucket,
                func.avg(CropPrice.retail_price_ugx).label("avg_retail"),
                func.avg(CropPrice.wholesale_price_ugx).label("avg_wholesale"),
                func.count(CropPrice.id).label("num_obs"),
            )
            .filter(
                CropPrice.crop_id == crop_id,
                CropPrice.price_date >= start_date,
                CropPrice.price_date <= end_date,
                CropPrice.quality != DataQuality.FLAGGED,
            )
        )

        if market_id:
            query = query.filter(CropPrice.market_id == market_id)

        results = (
            query
            .group_by("bucket")
            .order_by("bucket")
            .all()
        )

        crop = self.get_crop_or_404(crop_id)
        market = self.get_market_or_404(market_id) if market_id else None

        data_points = [
            PriceTrendPoint(
                date=row.bucket.date() if hasattr(row.bucket, "date") else row.bucket,
                avg_retail_ugx=round(float(row.avg_retail), 2) if row.avg_retail else None,
                avg_wholesale_ugx=round(float(row.avg_wholesale), 2) if row.avg_wholesale else None,
                num_observations=row.num_obs,
            )
            for row in results
        ]

        return PriceTrendResponse(
            crop=CropSummary.model_validate(crop),
            market=MarketSummary.model_validate(market) if market else None,
            unit=crop.default_unit,
            period_start=start_date,
            period_end=end_date,
            data_points=data_points,
        )

    def compare_markets(
        self,
        crop_id: int,
        price_date: Optional[date] = None,
        unit: Optional[PriceUnit] = None,
    ) -> MarketComparisonResponse:
        """
        Fetches the latest price for a crop at every active market
        and computes each market's deviation from the national average.

        This is MAAIF's key spatial analysis tool — shows at a glance
        which regions have supply shortages (high prices) vs surpluses.
        """
        # Use provided date or fall back to most recent data date
        if not price_date:
            latest = (
                self.db.query(func.max(CropPrice.price_date))
                .filter(
                    CropPrice.crop_id == crop_id,
                    CropPrice.quality != DataQuality.FLAGGED,
                )
                .scalar()
            )
            price_date = latest or date.today()

        # Get prices for all markets on or near that date (±7 days tolerance)
        query = (
            self.db.query(CropPrice)
            .options(joinedload(CropPrice.market))
            .filter(
                CropPrice.crop_id == crop_id,
                CropPrice.price_date >= price_date - timedelta(days=7),
                CropPrice.price_date <= price_date,
                CropPrice.quality != DataQuality.FLAGGED,
            )
        )

        if unit:
            query = query.filter(CropPrice.unit == unit)

        # Get one row per market — the most recent within the 7-day window
        all_prices = query.order_by(
            CropPrice.market_id, desc(CropPrice.price_date)
        ).all()

        # Deduplicate — keep only the latest per market
        seen_markets: set[int] = set()
        latest_per_market: list[CropPrice] = []
        for p in all_prices:
            if p.market_id not in seen_markets:
                latest_per_market.append(p)
                seen_markets.add(p.market_id)

        # Compute national average retail price
        retail_prices = [
            float(p.retail_price_ugx)
            for p in latest_per_market
            if p.retail_price_ugx is not None
        ]
        national_avg = (
            sum(retail_prices) / len(retail_prices) if retail_prices else None
        )

        # Build comparison items
        items = []
        for price in latest_per_market:
            deviation = None
            if national_avg and price.retail_price_ugx:
                deviation = round(
                    (float(price.retail_price_ugx) - national_avg) / national_avg * 100,
                    2,
                )

            items.append(
                MarketComparisonItem(
                    market=MarketSummary.model_validate(price.market),
                    latest_date=price.price_date,
                    retail_price_ugx=price.retail_price_ugx,
                    wholesale_price_ugx=price.wholesale_price_ugx,
                    price_vs_national_avg_pct=deviation,
                )
            )

        # Sort: most expensive markets first (food security priority)
        items.sort(
            key=lambda x: float(x.retail_price_ugx or 0),
            reverse=True,
        )

        crop = self.get_crop_or_404(crop_id)
        return MarketComparisonResponse(
            crop=CropSummary.model_validate(crop),
            comparison_date=price_date,
            unit=unit or crop.default_unit,
            national_avg_retail_ugx=round(national_avg, 2) if national_avg else None,
            markets=items,
        )

    def get_price_alerts(
        self,
        threshold_pct: float = 20.0,
        lookback_days: int = 30,
        region: Optional[UgandaRegion] = None,
    ) -> list[dict]:
        """
        Identifies crop/market combinations where prices have spiked.

        Algorithm:
          1. Compute average price for each crop/market in the PREVIOUS period
          2. Get the latest price for each crop/market
          3. Flag where: latest > previous_avg × (1 + threshold_pct/100)

        Returns a list of alert dicts sorted by severity (biggest spike first).
        These power the food security early warning panel on the dashboard.
        """
        today = date.today()
        period_end = today - timedelta(days=1)           # yesterday
        period_start = today - timedelta(days=lookback_days)
        prev_period_start = period_start - timedelta(days=lookback_days)

        # Average prices in the PREVIOUS period (baseline)
        baseline_q = (
            self.db.query(
                CropPrice.crop_id,
                CropPrice.market_id,
                func.avg(CropPrice.retail_price_ugx).label("baseline_avg"),
            )
            .filter(
                CropPrice.price_date >= prev_period_start,
                CropPrice.price_date < period_start,
                CropPrice.retail_price_ugx.isnot(None),
                CropPrice.quality != DataQuality.FLAGGED,
            )
            .group_by(CropPrice.crop_id, CropPrice.market_id)
            .subquery()
        )

        # Latest prices in CURRENT period
        current_q = (
            self.db.query(
                CropPrice.crop_id,
                CropPrice.market_id,
                func.avg(CropPrice.retail_price_ugx).label("current_avg"),
            )
            .filter(
                CropPrice.price_date >= period_start,
                CropPrice.price_date <= period_end,
                CropPrice.retail_price_ugx.isnot(None),
                CropPrice.quality != DataQuality.FLAGGED,
            )
            .group_by(CropPrice.crop_id, CropPrice.market_id)
            .subquery()
        )

        # Join and compute spike percentage
        results = (
            self.db.query(
                current_q.c.crop_id,
                current_q.c.market_id,
                current_q.c.current_avg,
                baseline_q.c.baseline_avg,
                (
                    (current_q.c.current_avg - baseline_q.c.baseline_avg)
                    / baseline_q.c.baseline_avg * 100
                ).label("change_pct"),
            )
            .join(
                baseline_q,
                and_(
                    current_q.c.crop_id == baseline_q.c.crop_id,
                    current_q.c.market_id == baseline_q.c.market_id,
                ),
            )
            .filter(
                (current_q.c.current_avg - baseline_q.c.baseline_avg)
                / baseline_q.c.baseline_avg * 100
                >= threshold_pct
            )
            .order_by(desc("change_pct"))
            .all()
        )

        alerts = []
        for row in results:
            crop = self.db.get(Crop, row.crop_id)
            market = self.db.get(Market, row.market_id)

            # Apply region filter if requested
            if region and market and market.region != region:
                continue

            alerts.append({
                "crop_id": row.crop_id,
                "crop_name": crop.name if crop else "Unknown",
                "market_id": row.market_id,
                "market_name": market.name if market else "Unknown",
                "region": market.region if market else None,
                "baseline_avg_ugx": round(float(row.baseline_avg), 2),
                "current_avg_ugx": round(float(row.current_avg), 2),
                "change_pct": round(float(row.change_pct), 2),
                "severity": (
                    "CRITICAL" if row.change_pct >= 50
                    else "HIGH" if row.change_pct >= 30
                    else "MEDIUM"
                ),
                "lookback_days": lookback_days,
            })

        return alerts

    # =========================================================================
    # PRIVATE HELPERS
    # =========================================================================

    def _compute_price_per_kg(
        self,
        price: Optional[Decimal],
        unit: PriceUnit,
    ) -> Optional[Decimal]:
        """
        Normalises any price to UGX per kg for cross-crop ML features.

        Conversion factors for Uganda's standard market units.
        Sources: MAAIF standard weights, Uganda Bureau of Standards.
        """
        if price is None:
            return None

        # kg equivalent for each unit
        KG_CONVERSIONS: dict[PriceUnit, float] = {
            PriceUnit.KG: 1.0,
            PriceUnit.GRAM: 0.001,
            PriceUnit.TONNE: 1000.0,
            PriceUnit.BAG_90KG: 90.0,
            PriceUnit.BAG_50KG: 50.0,
            PriceUnit.CRATE: 25.0,    # Standard tomato crate ~25kg
            PriceUnit.BUNCH: 35.0,    # Matooke bunch average ~35kg
            PriceUnit.LITRE: 1.03,    # Approx for milk (density ~1.03)
            PriceUnit.PIECE: None,    # Can't convert pieces to kg
        }

        kg_equiv = KG_CONVERSIONS.get(unit)
        if kg_equiv is None:
            return None  # Unit not convertible to kg

        return Decimal(str(round(float(price) / kg_equiv, 2)))