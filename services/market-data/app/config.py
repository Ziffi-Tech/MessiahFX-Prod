"""Market-data service configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SERVICE_NAME: str = "market-data"
    VERSION: str = "0.1.0"
    SERVICE_PORT: int = 8001
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379/0"
    TRADING_MODE: str = "paper"

    # Binance
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    BINANCE_TESTNET: bool = True
    BINANCE_SPOT_SYMBOLS: str = "BTC/USDT,ETH/USDT,SOL/USDT"
    BINANCE_PERP_SYMBOLS: str = "BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT"

    # Bybit (linear USDT perpetuals via CCXT Pro). Empty = feed disabled.
    BYBIT_API_KEY: str = ""
    BYBIT_API_SECRET: str = ""
    BYBIT_TESTNET: bool = True
    BYBIT_PERP_SYMBOLS: str = ""   # e.g. "BTC/USDT:USDT,ETH/USDT:USDT"

    # OKX (linear USDT perpetuals via CCXT Pro). Empty = feed disabled.
    OKX_API_KEY: str = ""
    OKX_API_SECRET: str = ""
    OKX_API_PASSWORD: str = ""
    OKX_TESTNET: bool = True
    OKX_PERP_SYMBOLS: str = ""     # e.g. "BTC/USDT:USDT,ETH/USDT:USDT"

    # Kraken (spot via CCXT Pro). Empty = feed disabled.
    KRAKEN_API_KEY: str = ""
    KRAKEN_API_SECRET: str = ""
    KRAKEN_SYMBOLS: str = ""       # e.g. "BTC/USD,ETH/USD"

    # Oanda
    OANDA_API_KEY: str = ""
    OANDA_ACCOUNT_ID: str = ""
    OANDA_ENVIRONMENT: str = "practice"
    OANDA_INSTRUMENTS: str = "EUR_USD,GBP_USD,USD_JPY"

    # Tick cache config
    TICK_CACHE_MAX_SIZE: int = 500  # Max ticks to keep per symbol in Redis

    # ── Live OHLCV bar writer ─────────────────────────────────────────────────
    # Resamples the tick cache into completed candles and persists them to
    # ohlcv_bars (see app/bar_writer.py). ON by default — builds the history that
    # backtests and the directional bar-mode strategies need. Idempotent + best-
    # effort: a DB blip is logged and retried next cycle, never breaks the feeds.
    BAR_WRITER_ENABLED: bool = True
    BAR_WRITER_INTERVAL_SECONDS: int = 60  # how often to flush completed bars
    BAR_WRITER_BAR_SECONDS: int = 60       # candle width (60s = "1m" bars)

    @property
    def binance_spot_list(self) -> list[str]:
        return [s.strip() for s in self.BINANCE_SPOT_SYMBOLS.split(",") if s.strip()]

    @property
    def binance_perp_list(self) -> list[str]:
        return [s.strip() for s in self.BINANCE_PERP_SYMBOLS.split(",") if s.strip()]

    @property
    def bybit_perp_list(self) -> list[str]:
        return [s.strip() for s in self.BYBIT_PERP_SYMBOLS.split(",") if s.strip()]

    @property
    def okx_perp_list(self) -> list[str]:
        return [s.strip() for s in self.OKX_PERP_SYMBOLS.split(",") if s.strip()]

    @property
    def kraken_symbol_list(self) -> list[str]:
        return [s.strip() for s in self.KRAKEN_SYMBOLS.split(",") if s.strip()]

    @property
    def oanda_instrument_list(self) -> list[str]:
        return [i.strip() for i in self.OANDA_INSTRUMENTS.split(",") if i.strip()]

    @property
    def bar_writer_targets(self) -> list[tuple[str, str]]:
        """
        (venue, symbol) pairs the bar writer persists — every configured feed
        symbol, keyed by the same venue the feed publishes under. Disabled feeds
        contribute nothing (their symbol lists are empty).
        """
        targets: list[tuple[str, str]] = []
        for s in self.binance_spot_list + self.binance_perp_list:
            targets.append(("binance", s))
        for s in self.bybit_perp_list:
            targets.append(("bybit", s))
        for s in self.okx_perp_list:
            targets.append(("okx", s))
        for s in self.kraken_symbol_list:
            targets.append(("kraken", s))
        for s in self.oanda_instrument_list:
            targets.append(("oanda", s))
        return targets

    @property
    def oanda_base_url(self) -> str:
        if self.OANDA_ENVIRONMENT == "live":
            return "https://api-fxtrade.oanda.com"
        return "https://api-fxpractice.oanda.com"

    @property
    def oanda_stream_url(self) -> str:
        if self.OANDA_ENVIRONMENT == "live":
            return "https://stream-fxtrade.oanda.com"
        return "https://stream-fxpractice.oanda.com"


settings = Settings()
