"""
services/weather_service.py — AgriGuard Weather Business Logic
=================================================================
Service layer for weather_readings, mirroring services/price_service.py:
routers stay thin, raw SQLAlchemy queries live here, and the same service
can be called from the API, `scripts/load_weather.py`, or a notebook.

This is also where the analytics `fetch_weather.py`'s docstring promised
but nothing ever implemented — drought-risk scoring and heavy-rain alerts —
finally live. See `get_drought_risk()` and `get_heavy_rain_alerts()`.

Author: AgriGuard Team
"""

from datetime import date, timedelta
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import and_, desc, func
from sqlalchemy.orm import Session, joinedload

from backend.app.core.config import Settings
from backend.app.models.price import Market, UgandaRegion
from backend.app.models.weather import WeatherReading
from backend.app.schemas.weather import (
    DroughtRiskItem,
    DroughtRiskResponse,
    HeavyRainAlertItem,
    HeavyRainAlertResponse,
    WeatherFilterParams,
    WeatherReadingCreate,
    WeatherTrendPoint,
    WeatherTrendResponse,
)


class WeatherService:
    """All weather-related business logic, instantiated per-request."""

    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    # =========================================================================
    # LOOKUP HELPERS
    # =========================================================================

    def get_market_or_404(self, market_id: int) -> Market:
        market = self.db.get(Market, market_id)
        if not market:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Market with id={market_id} not found. "
                       "Use GET /markets to see available markets.",
            )
        return market

    def get_reading_by_id(self, reading_id: int) -> Optional[WeatherReading]:
        return (
            self.db.query(WeatherReading)
            .options(joinedload(WeatherReading.market))
            .filter(WeatherReading.id == reading_id)
            .first()
        )

    def find_existing(
        self, market_id: int, reading_date: date, is_forecast: bool
    ) -> Optional[WeatherReading]:
        """
        Look up an existing row for (market, date, historical-vs-forecast).
        Used to make ingestion idempotent — a re-run of fetch_weather.py
        should UPDATE the existing row, not insert a duplicate.
        """
        return (
            self.db.query(WeatherReading)
            .filter(
                WeatherReading.market_id == market_id,
                WeatherReading.reading_date == reading_date,
                WeatherReading.is_forecast == is_forecast,
            )
            .first()
        )

    # =========================================================================
    # CORE CRUD
    # =========================================================================

    def get_readings(
        self, filters: WeatherFilterParams
    ) -> tuple[list[WeatherReading], int]:
        """Paginated, filtered weather readings. Returns (records, total_count)."""
        query = self.db.query(WeatherReading).options(joinedload(WeatherReading.market))

        if filters.market_id:
            query = query.filter(WeatherReading.market_id == filters.market_id)

        if filters.region:
            query = query.join(Market).filter(Market.region == filters.region)

        if filters.start_date:
            query = query.filter(WeatherReading.reading_date >= filters.start_date)

        if filters.end_date:
            query = query.filter(WeatherReading.reading_date <= filters.end_date)

        if filters.is_forecast is not None:
            query = query.filter(WeatherReading.is_forecast == filters.is_forecast)

        total = query.count()

        readings = (
            query
            .order_by(desc(WeatherReading.reading_date), WeatherReading.market_id)
            .limit(filters.limit)
            .offset(filters.offset)
            .all()
        )

        return readings, total

    def upsert_reading(self, data: WeatherReadingCreate) -> WeatherReading:
        """
        Insert a new reading, or update it in place if one already exists
        for (market_id, reading_date, is_forecast). This is the method
        `scripts/load_weather.py` calls per row — safe to re-run daily.
        """
        self.get_market_or_404(data.market_id)

        existing = self.find_existing(data.market_id, data.reading_date, data.is_forecast)
        payload = data.model_dump()

        if existing:
            for field, value in payload.items():
                setattr(existing, field, value)
            self.db.commit()
            self.db.refresh(existing)
            return existing

        new_reading = WeatherReading(**payload)
        self.db.add(new_reading)
        self.db.commit()
        self.db.refresh(new_reading)
        return new_reading

    def bulk_upsert(self, rows: list[WeatherReadingCreate]) -> dict:
        """
        Upserts many readings in one call — what `scripts/load_weather.py`
        and `services/weather_sync.py`'s scheduled sync both use. Commits
        once at the end rather than per-row, since a 365-day x 8-market
        historical file is ~2,900 rows and per-row commits would be
        needlessly slow.

        Groups by market_id and fetches each market's existing rows for the
        batch's date range in a single query, instead of one SELECT per row
        (the previous version did exactly that — harmless on a one-off CLI
        load, but weather_sync.py now calls this on every scheduled run with
        ~100 rows/market, which turned into hundreds of individual SELECTs
        per cycle, drowning out everything else in the console with
        settings.debug=True's SQL echo). This does 1 validity check + 1
        SELECT per market instead — ~9 queries total for an 8-market batch
        instead of 800+.

        Returns a small summary dict rather than the full list of ORM
        objects, since the caller (a CLI script or the sync job) only needs
        counts.
        """
        if not rows:
            return {"created": 0, "updated": 0, "skipped": 0, "total": 0}

        created, updated, skipped = 0, 0, 0

        by_market: dict[int, list[WeatherReadingCreate]] = {}
        for row in rows:
            by_market.setdefault(row.market_id, []).append(row)

        valid_market_ids = {
            m_id for (m_id,) in
            self.db.query(Market.id).filter(Market.id.in_(by_market.keys())).all()
        }

        for market_id, market_rows in by_market.items():
            if market_id not in valid_market_ids:
                skipped += len(market_rows)
                continue

            dates = {r.reading_date for r in market_rows}
            existing_rows = (
                self.db.query(WeatherReading)
                .filter(
                    WeatherReading.market_id == market_id,
                    WeatherReading.reading_date.in_(dates),
                )
                .all()
            )
            existing_by_key = {(e.reading_date, e.is_forecast): e for e in existing_rows}

            for row in market_rows:
                key = (row.reading_date, row.is_forecast)
                payload = row.model_dump()
                existing = existing_by_key.get(key)
                if existing:
                    for field, value in payload.items():
                        setattr(existing, field, value)
                    updated += 1
                else:
                    self.db.add(WeatherReading(**payload))
                    created += 1

        self.db.commit()
        return {"created": created, "updated": updated, "skipped": skipped, "total": len(rows)}

    # =========================================================================
    # ANALYTICS QUERIES
    # =========================================================================

    def get_latest_readings(
        self,
        region: Optional[UgandaRegion] = None,
        is_forecast: bool = False,
    ) -> list[WeatherReading]:
        """Most recent reading per market (historical by default)."""
        subq = (
            self.db.query(
                WeatherReading.market_id,
                func.max(WeatherReading.reading_date).label("max_date"),
            )
            .filter(WeatherReading.is_forecast == is_forecast)
            .group_by(WeatherReading.market_id)
            .subquery()
        )

        query = (
            self.db.query(WeatherReading)
            .options(joinedload(WeatherReading.market))
            .join(
                subq,
                and_(
                    WeatherReading.market_id == subq.c.market_id,
                    WeatherReading.reading_date == subq.c.max_date,
                    WeatherReading.is_forecast == is_forecast,
                ),
            )
        )

        if region:
            query = query.join(Market).filter(Market.region == region)

        return query.order_by(WeatherReading.market_id).all()

    def get_forecast(self, market_id: int, days: int = 16) -> list[WeatherReading]:
        """Upcoming forecast rows for a market, soonest first, capped at `days`."""
        self.get_market_or_404(market_id)
        return (
            self.db.query(WeatherReading)
            .filter(
                WeatherReading.market_id == market_id,
                WeatherReading.is_forecast == True,  # noqa: E712
                WeatherReading.reading_date >= date.today(),
            )
            .order_by(WeatherReading.reading_date)
            .limit(days)
            .all()
        )

    def get_weather_trend(
        self,
        market_id: Optional[int],
        start_date: date,
        end_date: date,
        interval: str = "week",
    ) -> WeatherTrendResponse:
        """
        Aggregated weather trend over time, bucketed by day/week/month.

        Bucketed in Python rather than via SQL `date_trunc()` — that
        function is Postgres/MySQL-only and this app's default
        `DATABASE_URL` is SQLite (see core/config.py), so a SQL-side
        `date_trunc()` (the approach `PriceService.get_price_trend()` uses)
        would raise on the DB this actually runs against today. Weather
        read volume is small (hundreds of rows per market, not millions),
        so pulling the window and bucketing here is cheap and portable
        across every dialect this project might run on.
        """
        query = self.db.query(WeatherReading).filter(
            WeatherReading.reading_date >= start_date,
            WeatherReading.reading_date <= end_date,
            WeatherReading.is_forecast == False,  # noqa: E712 — trends are historical only
        )

        market_name = None
        if market_id:
            self.get_market_or_404(market_id)
            query = query.filter(WeatherReading.market_id == market_id)
            market_name = self.db.get(Market, market_id).name

        readings = query.order_by(WeatherReading.reading_date).all()

        def bucket_key(d: date) -> str:
            if interval == "day":
                return d.isoformat()
            if interval == "month":
                return d.strftime("%Y-%m")
            # default / "week": ISO year-week, e.g. "2026-W24"
            iso_year, iso_week, _ = d.isocalendar()
            return f"{iso_year}-W{iso_week:02d}"

        buckets: dict[str, list[WeatherReading]] = {}
        for r in readings:
            buckets.setdefault(bucket_key(r.reading_date), []).append(r)

        def avg(values: list) -> Optional[float]:
            clean = [v for v in values if v is not None]
            return round(sum(clean) / len(clean), 1) if clean else None

        def total(values: list) -> Optional[float]:
            clean = [v for v in values if v is not None]
            return round(sum(clean), 1) if clean else None

        points = [
            WeatherTrendPoint(
                period=period,
                avg_temp_max_c=avg([r.temp_max_c for r in rows]),
                avg_temp_min_c=avg([r.temp_min_c for r in rows]),
                total_rainfall_mm=total([r.rainfall_mm for r in rows]),
                avg_water_balance_mm=avg([r.water_balance_mm for r in rows]),
                n_obs=len(rows),
            )
            for period, rows in sorted(buckets.items())
        ]

        return WeatherTrendResponse(
            market_id=market_id,
            market_name=market_name,
            interval=interval,
            period_start=start_date,
            period_end=end_date,
            points=points,
        )

    def get_drought_risk(
        self,
        region: Optional[UgandaRegion] = None,
        lookback_days: int = 30,
        deficit_threshold_mm: float = -3.0,
    ) -> DroughtRiskResponse:
        """
        Scores each market's drought stress over the lookback window.

        This is the analysis `fetch_weather.py`'s docstring describes —
        "drought → supply drop → price spike" — surfaced as an actual
        queryable signal instead of just a comment. Uses the same
        water-balance deficit logic as `WeatherReading.is_drought_stress`.

        risk_level thresholds (share of days in deficit over the window):
          < 20%  -> LOW
          20-40% -> MODERATE
          40-65% -> HIGH
          >= 65% -> SEVERE
        """
        today = date.today()
        window_start = today - timedelta(days=lookback_days)

        # Deliberately scored in Python rather than via a SQL boolean-sum:
        # SQLite vs Postgres/MySQL disagree on CAST(bool AS int) semantics,
        # and this runs over hundreds of rows per market, not millions, so
        # the portability is worth the trivial cost.
        readings_query = self.db.query(WeatherReading).filter(
            WeatherReading.reading_date >= window_start,
            WeatherReading.reading_date <= today,
            WeatherReading.is_forecast == False,  # noqa: E712
        )
        if region:
            readings_query = readings_query.join(Market).filter(Market.region == region)

        readings = readings_query.all()

        by_market: dict[int, list[WeatherReading]] = {}
        for r in readings:
            by_market.setdefault(r.market_id, []).append(r)

        items: list[DroughtRiskItem] = []
        for market_id, rows in by_market.items():
            market = self.db.get(Market, market_id)
            if not market:
                continue

            deficit_days = sum(
                1 for r in rows
                if r.water_balance_mm is not None and r.water_balance_mm < deficit_threshold_mm
            )
            valid_balances = [r.water_balance_mm for r in rows if r.water_balance_mm is not None]
            avg_balance = round(sum(valid_balances) / len(valid_balances), 2) if valid_balances else None
            latest_date = max((r.reading_date for r in rows), default=None)

            deficit_share = deficit_days / len(rows) if rows else 0.0
            if deficit_share >= 0.65:
                risk_level = "SEVERE"
            elif deficit_share >= 0.40:
                risk_level = "HIGH"
            elif deficit_share >= 0.20:
                risk_level = "MODERATE"
            else:
                risk_level = "LOW"

            items.append(
                DroughtRiskItem(
                    market_id=market_id,
                    market_name=market.name,
                    region=market.region,
                    deficit_days=deficit_days,
                    lookback_days=lookback_days,
                    avg_water_balance_mm=avg_balance,
                    latest_reading_date=latest_date,
                    risk_level=risk_level,
                )
            )

        # Worst risk first — that's what a food-security dashboard wants up top
        severity_order = {"SEVERE": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}
        items.sort(key=lambda x: (severity_order[x.risk_level], -x.deficit_days))

        return DroughtRiskResponse(
            generated_for_date=today,
            threshold_mm=deficit_threshold_mm,
            lookback_days=lookback_days,
            markets=items,
        )

    def get_heavy_rain_alerts(
        self,
        threshold_mm: float = 50.0,
        lookback_days: int = 7,
        include_forecast: bool = True,
    ) -> HeavyRainAlertResponse:
        """
        Recent or upcoming heavy-rain days (>threshold_mm) — flooding-risk
        signal for MAAIF's early-warning panel, using the same >50mm
        threshold `fetch_weather.py`'s summary report already flags.
        """
        today = date.today()
        window_start = today - timedelta(days=lookback_days)
        window_end = today + timedelta(days=16) if include_forecast else today

        query = (
            self.db.query(WeatherReading)
            .options(joinedload(WeatherReading.market))
            .filter(
                WeatherReading.rainfall_mm > threshold_mm,
                WeatherReading.reading_date >= window_start,
                WeatherReading.reading_date <= window_end,
            )
        )
        if not include_forecast:
            query = query.filter(WeatherReading.is_forecast == False)  # noqa: E712

        rows = query.order_by(desc(WeatherReading.rainfall_mm)).all()

        alerts = [
            HeavyRainAlertItem(
                market_id=r.market_id,
                market_name=r.market.name,
                region=r.market.region,
                reading_date=r.reading_date,
                rainfall_mm=r.rainfall_mm,
                is_forecast=r.is_forecast,
            )
            for r in rows
        ]

        return HeavyRainAlertResponse(
            threshold_mm=threshold_mm,
            lookback_days=lookback_days,
            alerts=alerts,
        )
