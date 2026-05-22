"""Journal service configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SERVICE_NAME: str = "journal"
    VERSION: str = "0.1.0"
    SERVICE_PORT: int = 8006
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379/0"
    TRADING_MODE: str = "paper"

    # Reconciliation
    RECONCILIATION_INTERVAL_SECONDS: int = 60
    # Trades in pending/open longer than this are marked error by reconciler
    RECONCILIATION_STALE_MINUTES: int = 5


settings = Settings()
