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

    # ── Capital controls (Phase 4 — controlled live). 0 = disabled ──────────────
    # Hard notional caps on OPEN exposure; a new order is rejected if it would push
    # exposure over the cap. Absolute daily-loss limit AUTO-HALTS (complements the
    # drawdown-% halt). Default OFF (0) so paper behaviour is unchanged — set these
    # before going live, starting small and widening on evidence.
    RISK_MAX_GROSS_EXPOSURE_USD: float = 0.0      # global open-notional cap
    RISK_MAX_STRATEGY_EXPOSURE_USD: float = 0.0   # per-strategy open-notional cap
    RISK_DAILY_LOSS_LIMIT_USD: float = 0.0        # absolute daily realised loss → halt

    @property
    def position_usd(self) -> float:
        """Estimated per-trade notional — the increment used for exposure caps."""
        return self.PAPER_CAPITAL_USD * self.RISK_MAX_PER_TRADE_PCT

    @property
    def exposure_caps_enabled(self) -> bool:
        return self.RISK_MAX_GROSS_EXPOSURE_USD > 0 or self.RISK_MAX_STRATEGY_EXPOSURE_USD > 0

    @property
    def is_paper(self) -> bool:
        return self.TRADING_MODE == "paper"


settings = Settings()
