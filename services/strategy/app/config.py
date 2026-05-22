"""Strategy engine service configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SERVICE_NAME: str = "strategy"
    VERSION: str = "0.1.0"
    SERVICE_PORT: int = 8002
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379/0"
    TRADING_MODE: str = "paper"

    # ── Binance ────────────────────────────────────────────────────────────────
    BINANCE_TESTNET: bool = True

    # ── Strategy symbol lists ─────────────────────────────────────────────────
    # Spot symbols used by funding arb (perp derived: BTC/USDT → BTC/USDT:USDT)
    FUNDING_ARB_SYMBOLS: str = "BTC/USDT,ETH/USDT,SOL/USDT"
    # Spot symbols used by stat arb (spot vs perp z-score)
    STAT_ARB_SYMBOLS: str = "BTC/USDT,ETH/USDT"

    # ── Funding arb parameters ────────────────────────────────────────────────
    # Minimum net edge (bps per 8h period) to generate a signal
    FUNDING_ARB_MIN_EDGE_BPS: float = 5.0
    # Round-trip fee budget: taker on both spot and perp (2 × ~7.5 bps)
    FUNDING_ARB_FEE_BPS: float = 15.0
    # How long (seconds) to cache funding rates before re-fetching Binance API
    FUNDING_ARB_POLL_SECONDS: int = 30

    # ── Stat arb parameters ───────────────────────────────────────────────────
    # Number of ticks to use for rolling z-score calculation
    STAT_ARB_WINDOW: int = 100
    # Generate signal when |z-score| exceeds this threshold
    STAT_ARB_ENTRY_Z: float = 2.0
    # Minimum net edge (bps) after fees to emit a signal
    STAT_ARB_MIN_EDGE_BPS: float = 3.0
    # Round-trip fee budget for stat arb trades
    STAT_ARB_FEE_BPS: float = 10.0

    # ── Swing parameters ──────────────────────────────────────────────────────
    # Implied minimum edge for swing trades driven by TradingView signals.
    # TV has already done TA — we trust the signal and assign this placeholder edge.
    SWING_MIN_EDGE_BPS: float = 5.0

    # ── TradingView signal mode ───────────────────────────────────────────────
    # TV_SIGNAL_MODE=True  → bot ONLY trades when TradingView fires a webhook.
    #                         Autonomous market-data loops are disabled.
    # TV_SIGNAL_MODE=False → autonomous loops also run (original behaviour).
    #                         TV signals still work in addition.
    TV_SIGNAL_MODE: bool = True

    # Redis consumer group for the signals:tradingview stream
    TV_CONSUMER_GROUP: str = "strategy"
    TV_CONSUMER_NAME: str = "strategy-tv-1"

    # Maximum age (seconds) of a TV signal before it is considered stale and skipped
    TV_SIGNAL_MAX_AGE_SECONDS: int = 60

    # ── Computed properties ───────────────────────────────────────────────────
    @property
    def funding_arb_symbol_list(self) -> list[str]:
        return [s.strip() for s in self.FUNDING_ARB_SYMBOLS.split(",") if s.strip()]

    @property
    def stat_arb_symbol_list(self) -> list[str]:
        return [s.strip() for s in self.STAT_ARB_SYMBOLS.split(",") if s.strip()]

    @property
    def binance_futures_base_url(self) -> str:
        if self.BINANCE_TESTNET:
            return "https://testnet.binancefuture.com"
        return "https://fapi.binance.com"


settings = Settings()
