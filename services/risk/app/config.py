"""Risk engine service configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SERVICE_NAME: str = "risk"
    VERSION: str = "0.1.0"
    SERVICE_PORT: int = 8003
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379/0"
    TRADING_MODE: str = "paper"

    # ── Hard risk limits — override via dashboard, not here ──────────────────
    # These are READ from Redis at runtime. Defaults here are
    # applied when Redis has no existing value (i.e., first startup).
    RISK_MAX_PER_TRADE_PCT: float = 0.01       # 1% of capital per trade
    RISK_MAX_DAILY_DRAWDOWN_PCT: float = 0.03  # 3% daily drawdown halt
    RISK_MAX_OPEN_POSITIONS: int = 5
    RISK_MAX_CONSECUTIVE_LOSSES: int = 5
    RISK_COOLDOWN_MINUTES: int = 30
    PAPER_CAPITAL_USD: float = 5000.0


settings = Settings()
