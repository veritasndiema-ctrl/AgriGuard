"""
AgriGuard configuration.
Reads from (in priority order):
  1. Environment variables
  2. backend/.env
  3. config/.env
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]

# Load .env files — later calls override earlier ones (env vars win over files)
for env_path in [ROOT / "config" / ".env", ROOT / "backend" / ".env"]:
    if env_path.exists():
        load_dotenv(env_path, override=False)


class Settings:
    # app
    app_name:   str  = "AgriGuard"
    app_version: str = "0.1.0"
    app_env:    str  = os.getenv("APP_ENV",    "development")
    debug:      bool = os.getenv("DEBUG",      "false").lower() == "true"
    secret_key: str  = os.getenv("SECRET_KEY", "dev-secret-key")
    log_level:  str  = os.getenv("LOG_LEVEL",  "INFO")

    # backend
    backend_host: str = os.getenv("BACKEND_HOST", "0.0.0.0")
    backend_port: int = int(os.getenv("BACKEND_PORT", "8000"))
    backend_url:  str = os.getenv("BACKEND_URL",  "http://localhost:8000")
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:8501")

    # data / models
    price_data_path: str = os.getenv(
        "AGRIGUARD_PRICE_DATA",
        str(ROOT / "data" / "raw" / "wfp_food_prices_uga.csv"),
    )
    model_dir: str = os.getenv("MODEL_DIR", str(ROOT / "ml" / "models"))

    # WFP Uganda price data — live sync (see backend/app/services/wfp_sync.py)
    wfp_sync_enabled: bool = os.getenv("WFP_SYNC_ENABLED", "true").lower() == "true"
    wfp_sync_interval_hours: float = float(os.getenv("WFP_SYNC_INTERVAL_HOURS", "6"))

    # FEWS NET Data Warehouse (FDW) — supplementary, fresher-cadence price feed
    # blended on top of WFP (see backend/app/services/fews_net_sync.py). WFP
    # remains the historical backbone; FEWS NET fills in more recent months.
    fews_net_data_path: str = os.getenv(
        "AGRIGUARD_FEWS_NET_DATA",
        str(ROOT / "data" / "raw" / "fews_net_prices_uga.csv"),
    )
    fews_net_sync_enabled: bool = os.getenv("FEWS_NET_SYNC_ENABLED", "true").lower() == "true"
    fews_net_sync_interval_hours: float = float(os.getenv("FEWS_NET_SYNC_INTERVAL_HOURS", "6"))
    # Optional — FDW returns public-only data with no credentials, which is
    # sufficient for Uganda staple food prices. Set these to unlock any
    # permissioned series the account has access to.
    fews_net_username: str = os.getenv("FEWS_NET_USERNAME", "")
    fews_net_password: str = os.getenv("FEWS_NET_PASSWORD", "")
    # How far back to pull on each sync. FDW's full history isn't needed here
    # (WFP already covers it) — this just keeps requests small and the feed
    # focused on what it's actually for: recent, fresher-than-WFP prices.
    fews_net_lookback_days: int = int(os.getenv("FEWS_NET_LOOKBACK_DAYS", "730"))

    # Open-Meteo weather — live sync (see backend/app/services/weather_sync.py).
    # Open-Meteo has no "did anything change" metadata endpoint like HDX/FDW
    # does, so each cycle just re-fetches a trailing window of history plus
    # the 16-day forecast, rather than a check-then-download two-step.
    weather_sync_enabled: bool = os.getenv("WEATHER_SYNC_ENABLED", "true").lower() == "true"
    weather_sync_interval_hours: float = float(os.getenv("WEATHER_SYNC_INTERVAL_HOURS", "6"))
    weather_sync_lookback_days: int = int(os.getenv("WEATHER_SYNC_LOOKBACK_DAYS", "90"))

    # Anthropic
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # database (optional)
    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./agriguard_dev.db",   # SQLite fallback for dev
    )

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key and not self.anthropic_api_key.startswith("sk-ant-..."))


settings = Settings()