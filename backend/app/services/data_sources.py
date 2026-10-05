"""
backend/app/services/data_sources.py — Registry of AgriGuard's auto-synced data sources
================================================================================
Covers both of AgriGuard's live feed types — crop/food prices (WFP, FEWS
NET) and weather (Open-Meteo) — plus credible sources logged for the future
that aren't wired up yet. "Market intelligence" (routers/markets.py) isn't
listed separately: every one of its endpoints derives from the same price
CSV WFP/FEWS NET sync keeps current, so it's already covered by those two
entries rather than needing its own sync module.
Problem this replaces: WFP (wfp_sync.py) and FEWS NET (fews_net_sync.py) were
each wired into main.py's startup scheduler by hand, as two near-identical
copy-pasted blocks (see the git history of main.py::on_startup). Adding a
third source meant a third copy-paste, and there was nowhere that answered
"what sources does AgriGuard actually pull from, and what's the plan for
more" other than reading both modules' docstrings.

This module is that place. SOURCE_REGISTRY is the single list main.py's
scheduler now iterates (see on_startup()) — registering a new source means
adding one DataSource entry here, not another hand-rolled scheduler block.

Two kinds of entries:
  - ACTIVE sources have a real sync_module (wfp_sync.py-shaped: exposes
    `sync_if_updated(force: bool) -> bool` and `last_sync_info() -> dict`)
    and auto-sync on a schedule, same as WFP/FEWS NET always have.
  - CATALOGUED sources are credible, publicly documented sources for Uganda
    food/crop prices that AgriGuard does not yet pull from. They're logged
    here — with real URLs and an honest note on why they're not wired up
    yet — rather than either being silently absent or backed by an
    integration that hasn't actually been verified against the live API.
    Turning one into ACTIVE means writing a sync module matching the same
    shape as wfp_sync.py/fews_net_sync.py and swapping its status here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import ModuleType
from typing import Optional

from backend.app.core.config import settings
from backend.app.services import wfp_sync, fews_net_sync, weather_sync


class SourceStatus(str, Enum):
    ACTIVE = "active"          # Auto-syncing on a schedule today
    CATALOGUED = "catalogued"  # Credible, logged, not yet integrated


@dataclass
class DataSource:
    name: str
    url: str
    status: SourceStatus
    cadence_note: str            # How often the upstream itself publishes/updates
    credibility_note: str        # Why this source is trustworthy for Uganda food prices
    sync_module: Optional[ModuleType] = None   # wfp_sync.py-shaped module, ACTIVE only
    enabled_flag: bool = True                   # settings.<x>_sync_enabled, ACTIVE only
    interval_hours_flag: float = 6.0             # settings.<x>_sync_interval_hours, ACTIVE only
    catalogued_note: str = ""                    # Why this one isn't wired up yet, CATALOGUED only
    scope: str = "Uganda"                        # Geographic scope of the feed


SOURCE_REGISTRY: list[DataSource] = [
    DataSource(
        name="WFP Uganda Food Prices",
        url="https://data.humdata.org/dataset/wfp-food-prices-for-uganda",
        status=SourceStatus.ACTIVE,
        cadence_note="Published monthly on HDX",
        credibility_note=(
            "UN World Food Programme's own price-collection pipeline; deep "
            "historical backbone (2006-present) for Uganda market prices."
        ),
        sync_module=wfp_sync,
        enabled_flag=settings.wfp_sync_enabled,
        interval_hours_flag=settings.wfp_sync_interval_hours,
    ),
    DataSource(
        name="FEWS NET Data Warehouse (FDW)",
        url="https://fdw.fews.net/api",
        status=SourceStatus.ACTIVE,
        cadence_note="Continuously updated; AgriGuard pulls the trailing window on each sync",
        credibility_note=(
            "USAID-funded famine early warning system; independent collection "
            "pipeline from WFP, so it's a second, fresher-cadence check on the "
            "same staple-food markets rather than a duplicate of WFP's own feed."
        ),
        sync_module=fews_net_sync,
        enabled_flag=settings.fews_net_sync_enabled,
        interval_hours_flag=settings.fews_net_sync_interval_hours,
    ),
    DataSource(
        name="Open-Meteo Weather",
        url="https://open-meteo.com",
        status=SourceStatus.ACTIVE,
        cadence_note=(
            "Historical archive finalises within a few days; 16-day forecast "
            "reissued daily — this sync re-pulls both on every cycle rather "
            "than checking for a change first (see weather_sync.py)."
        ),
        credibility_note=(
            "Free, no-API-key weather reanalysis + forecast service built on "
            "national meteorological models (ECMWF, DWD, NOAA, etc.); the "
            "temperature/rainfall/evapotranspiration inputs "
            "GET /weather/analytics/drought-risk scores its drought signal from."
        ),
        sync_module=weather_sync,
        enabled_flag=settings.weather_sync_enabled,
        interval_hours_flag=settings.weather_sync_interval_hours,
        scope="Uganda (8 markets: Kampala, Gulu, Mbarara, Mbale, Kasese, Lira, Jinja, Arua)",
    ),
    DataSource(
        name="FAO GIEWS Food Price Monitoring and Analysis (FPMA)",
        url="https://fpma.fao.org",
        status=SourceStatus.CATALOGUED,
        cadence_note="Updated as national sources report, typically monthly",
        credibility_note=(
            "UN FAO's own global food-price monitoring tool; covers domestic "
            "staple-food prices including Uganda, cross-checkable against WFP/FEWS NET."
        ),
        catalogued_note=(
            "FPMA's public interface is a data-browsing tool, not a documented "
            "stable REST endpoint — needs a confirmed extract format before a "
            "wfp_sync.py-shaped sync module can be written safely."
        ),
    ),
    DataSource(
        name="World Bank Commodity Markets (\"Pink Sheet\")",
        url="https://www.worldbank.org/en/research/commodity-markets",
        status=SourceStatus.CATALOGUED,
        cadence_note="Monthly Excel release",
        credibility_note=(
            "World Bank's benchmark global commodity price series — not "
            "Uganda-specific, but a credible external check on whether a local "
            "price move tracks a global one (e.g. maize, wheat) or is local."
        ),
        catalogued_note=(
            "Global, not Uganda-market-level — useful as a supplementary "
            "cross-check signal, not a drop-in replacement/addition to the "
            "market x commodity feed forecasts.py serves today."
        ),
        scope="Global",
    ),
    DataSource(
        name="Uganda Bureau of Statistics (UBOS)",
        url="https://www.ubos.org",
        status=SourceStatus.CATALOGUED,
        cadence_note="Statistical Abstracts and CPI releases, periodic",
        credibility_note=(
            "Uganda's official national statistics body — the authoritative "
            "domestic source for CPI and food-price-index figures."
        ),
        catalogued_note=(
            "Publishes as reports/CSV bulletins rather than a queryable API; "
            "would need a scheduled document-scrape + parse, a different shape "
            "of sync module than wfp_sync.py/fews_net_sync.py's metadata-poll pattern."
        ),
    ),
    DataSource(
        name="RATIN (Regional Agricultural Trade Intelligence Network)",
        url="https://www.ratin.net",
        status=SourceStatus.CATALOGUED,
        cadence_note="Updated by EAGC as wholesale market reports arrive, roughly weekly",
        credibility_note=(
            "Run by the Eastern Africa Grain Council; originated as a joint "
            "FEWS NET/USAID RATES/EAGC effort specifically for cross-border "
            "grain trade intelligence in Kenya, Uganda, Tanzania, Burundi, and "
            "Rwanda — maize, beans, and rice wholesale prices at a regional-"
            "trade level WFP/FEWS NET don't cover the same way."
        ),
        catalogued_note=(
            "Website-based market reports and bulletins, no documented public "
            "REST API found — same document-based integration shape as UBOS, "
            "not the metadata-poll pattern wfp_sync.py/fews_net_sync.py use."
        ),
        scope="East Africa (Uganda, Kenya, Tanzania, Burundi, Rwanda)",
    ),
]


def active_sources() -> list[DataSource]:
    return [s for s in SOURCE_REGISTRY if s.status == SourceStatus.ACTIVE]


def catalogued_sources() -> list[DataSource]:
    return [s for s in SOURCE_REGISTRY if s.status == SourceStatus.CATALOGUED]
