"""
MT5 Bridge Service configuration.

This service runs natively on Windows (NOT in a container).
It connects to a running MetaTrader 5 terminal via the MetaTrader5 Python package
and exposes a simple HTTP REST API for the containerised executor service.

Environment variables (set in .env or Windows system env):
  MT5_ACCOUNT       — MT5 account number (integer)
  MT5_PASSWORD      — MT5 account password
  MT5_SERVER        — MT5 broker server name (e.g. "ICMarkets-Live")
  MT5_PATH          — Optional: full path to terminal64.exe if auto-detect fails
  BRIDGE_API_KEY    — Bearer token for request authentication (REQUIRED in live mode)
  MAGIC_NUMBER      — EA magic number to identify our orders in MT5 (default 234000)
  SYMBOL_MAP        — Comma-separated internal:mt5 pairs (see default below)
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SERVICE_NAME: str = "mt5-bridge"
    VERSION: str = "0.1.0"
    HOST: str = "0.0.0.0"
    PORT: int = 8010
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── MT5 terminal connection ───────────────────────────────────────────────
    MT5_ACCOUNT: int = 0            # Demo or live account number
    MT5_PASSWORD: str = ""          # Account password
    MT5_SERVER: str = ""            # Broker server (e.g. "ICMarkets-Demo")
    MT5_PATH: str = ""              # Optional: path to terminal64.exe

    # ── Security ──────────────────────────────────────────────────────────────
    # REQUIRED for live mode. Executor must set the same key in MT5_BRIDGE_API_KEY.
    # Leave empty only for local development (bridge warns loudly if empty).
    BRIDGE_API_KEY: str = ""

    # ── Order settings ────────────────────────────────────────────────────────
    MAGIC_NUMBER: int = 234000      # Identifies MeznaQuantFX orders in MT5
    DEFAULT_DEVIATION: int = 20     # Max slippage in points for market orders
    MAX_LOT_SIZE: float = 10.0      # Hard cap — prevents grossly oversized orders
    MIN_LOT_SIZE: float = 0.01      # Minimum; enforced even if calc gives less

    # ── Symbol mapping ────────────────────────────────────────────────────────
    # Format: "internal_symbol:mt5_symbol" comma-separated.
    # internal_symbol = how our system refers to it (BTC/USDT, EUR/USD)
    # mt5_symbol      = exact broker symbol name in MetaTrader 5
    #
    # IMPORTANT: MT5 symbol names vary by broker.
    #   ICMarkets uses:  EURUSD, XAUUSD, US30, NAS100
    #   Pepperstone:     EURUSD, XAUUSD, US30.cash, NAS100
    #   Check your broker's symbol list and update accordingly.
    SYMBOL_MAP: str = (
        "EUR/USD:EURUSD,"
        "GBP/USD:GBPUSD,"
        "USD/JPY:USDJPY,"
        "GBP/JPY:GBPJPY,"
        "EUR/JPY:EURJPY,"
        "AUD/USD:AUDUSD,"
        "USD/CAD:USDCAD,"
        "USD/CHF:USDCHF,"
        "NZD/USD:NZDUSD,"
        "EUR/GBP:EURGBP,"
        "XAU/USD:XAUUSD,"       # Gold
        "XAG/USD:XAGUSD,"       # Silver
        "US30:US30,"            # Dow Jones — check your broker name
        "NAS100:NAS100,"        # Nasdaq 100
        "SPX500:SP500,"         # S&P 500
        "WTI:USOIL,"            # Crude Oil WTI
        "EURUSD:EURUSD,"        # Pass-through if TV sends condensed format
        "GBPUSD:GBPUSD,"
        "USDJPY:USDJPY,"
        "XAUUSD:XAUUSD"
    )

    @property
    def symbol_map_dict(self) -> dict[str, str]:
        """Parse SYMBOL_MAP into {internal: mt5} dict."""
        result = {}
        for pair in self.SYMBOL_MAP.split(","):
            pair = pair.strip()
            if ":" in pair:
                internal, mt5_sym = pair.split(":", 1)
                result[internal.strip()] = mt5_sym.strip()
        return result

    def to_mt5_symbol(self, internal: str) -> str:
        """
        Convert internal symbol format to MT5 broker symbol.

        Falls back to stripping '/' and '_' if no explicit mapping.
        Example: EUR/USD → EURUSD (fallback)
        """
        mapping = self.symbol_map_dict
        if internal in mapping:
            return mapping[internal]
        # Auto-strip: EUR/USD → EURUSD, EUR_USD → EURUSD
        return internal.replace("/", "").replace("_", "").upper()


settings = Settings()
