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
    SWING_MIN_EDGE_BPS: float = 5.0

    # ── Breakout parameters ───────────────────────────────────────────────────
    BREAKOUT_SYMBOLS: str = "BTC/USDT,ETH/USDT,EUR_USD,GBP_USD"
    BREAKOUT_LOOKBACK: int = 20
    BREAKOUT_ATR_PERIOD: int = 14
    BREAKOUT_ATR_MULT: float = 0.5
    BREAKOUT_MIN_EDGE_BPS: float = 4.0
    BREAKOUT_FEE_BPS: float = 10.0
    # Bar-based detection: real ATR (pandas-ta) on OHLCV candles built from the
    # tick cache, instead of the tick-mid approximation. ON by default — when the
    # 500-tick cache can't build a full lookback of candles (e.g. liquid symbols
    # whose 500 ticks span < a lookback window), the strategy automatically falls
    # back to tick-based detection per symbol, so it never goes silent. Also falls
    # back when pandas-ta is unavailable.
    BREAKOUT_USE_BARS: bool = True
    BREAKOUT_BAR_SECONDS: int = 15

    # ── Mean Reversion Scalp parameters ──────────────────────────────────────
    MEAN_REVERSION_SYMBOLS: str = "BTC/USDT,ETH/USDT,EUR_USD,GBP_USD,EUR_GBP"
    MR_RSI_PERIOD: int = 14
    MR_RSI_OVERSOLD: float = 30.0
    MR_RSI_OVERBOUGHT: float = 70.0
    MR_BB_PERIOD: int = 20
    MR_BB_STD_MULT: float = 2.0
    MR_MIN_EDGE_BPS: float = 3.0
    MR_FEE_BPS: float = 8.0
    # Bar-based RSI + Bollinger via pandas-ta (see BREAKOUT_USE_BARS). ON by
    # default — auto-falls back to tick detection per symbol when bar history is
    # too thin or pandas-ta is unavailable.
    MR_USE_BARS: bool = True
    MR_BAR_SECONDS: int = 15

    # ── Momentum parameters ───────────────────────────────────────────────────
    MOMENTUM_SYMBOLS: str = "BTC/USDT,ETH/USDT,SOL/USDT"
    MOM_ROC_THRESHOLD: float = 0.05
    MOM_ATR_PERIOD: int = 14
    MOM_ATR_STOP_MULT: float = 1.5
    MOM_MIN_EDGE_BPS: float = 5.0
    MOM_FEE_BPS: float = 10.0
    # Bar-based multi-timeframe ROC + ATR via pandas-ta (see BREAKOUT_USE_BARS).
    # ON by default — auto-falls back to tick detection per symbol when bar history
    # is too thin or pandas-ta is unavailable.
    MOM_USE_BARS: bool = True
    MOM_BAR_SECONDS: int = 15

    # ── Risk/reward gate ─────────────────────────────────────────────────────
    # Minimum reward:risk ratio required to emit any signal.
    # Signals where rr_ratio < this are discarded before reaching the risk engine.
    # Set to 0.0 to disable.  Recommended: 1.5 (target at least 1.5× the risk).
    STRATEGY_MIN_RR_RATIO: float = 1.5

    # ── Local regime detector ─────────────────────────────────────────────────
    # How often (seconds) to run the fast local regime detector.
    # This keeps ai:regime:current fresh when ai-filter is offline/cold.
    REGIME_DETECTOR_INTERVAL_SECONDS: int = 60
    REGIME_DETECTOR_ENABLED: bool = True

    # ── Edge / alpha decay monitor ────────────────────────────────────────────
    # Rolling window of trade outcomes per strategy for win-rate tracking.
    EDGE_MONITOR_WINDOW: int = 20
    # Expected healthy win rate across strategies (55%).
    EDGE_BASELINE_WIN_RATE: float = 0.55
    # Emit a decay alert when rolling win rate drops this many pp below baseline.
    EDGE_DECAY_THRESHOLD: float = 0.15

    # ── Strategy rotation ─────────────────────────────────────────────────────
    ROTATION_CONSECUTIVE_LOSS_THRESHOLD: int = 4

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
    def breakout_symbol_list(self) -> list[str]:
        return [s.strip() for s in self.BREAKOUT_SYMBOLS.split(",") if s.strip()]

    @property
    def mean_reversion_symbol_list(self) -> list[str]:
        return [s.strip() for s in self.MEAN_REVERSION_SYMBOLS.split(",") if s.strip()]

    @property
    def momentum_symbol_list(self) -> list[str]:
        return [s.strip() for s in self.MOMENTUM_SYMBOLS.split(",") if s.strip()]

    @property
    def binance_futures_base_url(self) -> str:
        if self.BINANCE_TESTNET:
            return "https://testnet.binancefuture.com"
        return "https://fapi.binance.com"


settings = Settings()
