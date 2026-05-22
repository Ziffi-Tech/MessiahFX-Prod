"""Backtest service configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SERVICE_NAME: str = "backtest"
    VERSION: str = "0.1.0"
    SERVICE_PORT: int = 8008
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = ""
    REDIS_URL: str = "redis://redis:6379/0"

    # Data source — Binance public API (no auth required)
    BINANCE_BASE_URL: str = "https://api.binance.com"
    BINANCE_FAPI_URL: str = "https://fapi.binance.com"

    # Simulation defaults
    DEFAULT_CAPITAL_USD: float = 5000.0
    DEFAULT_POSITION_PCT: float = 0.01       # 1% per trade = $50
    DEFAULT_TAKER_FEE_BPS: float = 7.5       # Binance taker
    DEFAULT_STAT_ARB_WINDOW: int = 100        # rolling z-score window
    DEFAULT_STAT_ARB_ENTRY_Z: float = 2.0
    DEFAULT_STAT_ARB_EXIT_Z: float = 0.5
    DEFAULT_FUNDING_MIN_EDGE_BPS: float = 5.0

    # Maximum candles to download per request (Binance limit = 1000)
    MAX_CANDLES_PER_FETCH: int = 1000
    # Maximum total candles for a single backtest (memory guard)
    MAX_TOTAL_CANDLES: int = 50_000


settings = Settings()
