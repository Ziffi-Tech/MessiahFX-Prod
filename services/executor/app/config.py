"""Executor service configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SERVICE_NAME: str = "executor"
    VERSION: str = "0.1.0"
    SERVICE_PORT: int = 8004
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379/0"

    # CRITICAL — never change to 'live' without full paper validation
    TRADING_MODE: str = "paper"

    # Binance
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    BINANCE_TESTNET: bool = True

    # Oanda (v20 REST API)
    OANDA_API_KEY: str = ""
    OANDA_ACCOUNT_ID: str = ""
    OANDA_ENVIRONMENT: str = "practice"

    # Position sizing — must mirror risk engine settings
    PAPER_CAPITAL_USD: float = 5000.0
    RISK_MAX_PER_TRADE_PCT: float = 0.01   # 1% of capital per trade = $50 default

    # Estimated taker fees per exchange (in bps) — used in paper fill simulation
    BINANCE_TAKER_FEE_BPS: float = 7.5    # 0.075% taker
    OANDA_SPREAD_BPS: float = 10.0        # approximate spread cost for FX

    # MT5 Bridge (Windows-native service)
    # Podman on Windows: host.containers.internal resolves to the Windows host
    # Podman on Linux:   set to the Windows VPS IP where the bridge runs
    MT5_BRIDGE_URL: str = "http://host.containers.internal:8010"
    MT5_BRIDGE_API_KEY: str = ""          # Must match BRIDGE_API_KEY in mt5-bridge/.env
    MT5_SPREAD_BPS: float = 10.0          # Estimated spread cost for MT5 instruments

    # Internal service URLs
    JOURNAL_URL: str = "http://journal:8006"
    RISK_URL: str = "http://risk:8003"

    # Rotation + edge monitoring thresholds (must match strategy service config)
    ROTATION_CONSECUTIVE_LOSS_THRESHOLD: int = 4
    EDGE_MONITOR_WINDOW: int = 20
    EDGE_BASELINE_WIN_RATE: float = 0.55
    EDGE_DECAY_THRESHOLD: float = 0.15

    @property
    def mt5_configured(self) -> bool:
        return bool(self.MT5_BRIDGE_URL)

    @property
    def oanda_rest_url(self) -> str:
        if self.OANDA_ENVIRONMENT == "live":
            return "https://api-fxtrade.oanda.com"
        return "https://api-fxpractice.oanda.com"

    @property
    def is_paper(self) -> bool:
        return self.TRADING_MODE == "paper"

    @property
    def position_usd(self) -> float:
        """Dollar value allocated per trade leg."""
        return self.PAPER_CAPITAL_USD * self.RISK_MAX_PER_TRADE_PCT


settings = Settings()
