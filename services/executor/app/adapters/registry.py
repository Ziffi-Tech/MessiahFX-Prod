"""
Exchange adapter abstraction.

Every venue is reached through one uniform interface — BaseExchangeAdapter.execute
(order) -> OrderResult. Concrete adapters are constructed with their own
dependencies (exchange clients, Redis, settings), so the consumer routes purely by
venue and never needs to know how any venue is wired.

Adding a venue (e.g. Bybit, OKX, Kraken):
  1. Implement an adapter module with an async execute(...) like the existing ones.
  2. Add a thin Adapter class here that wraps it.
  3. Register it in build_registry().
Nothing in the executor's order path (consumer._execute_leg) changes.

Paper mode override: when TRADING_MODE=paper every venue routes to the paper
adapter (simulated fills) via resolve_adapter(), regardless of the leg's venue.
"""

from typing import Any, Protocol, runtime_checkable

import httpx
import structlog
from redis.asyncio import Redis

from ..config import Settings
from . import OrderRequest, OrderResult
from . import paper as paper_adapter
from . import binance as binance_adapter
from . import bybit as bybit_adapter
from . import okx as okx_adapter
from . import kraken as kraken_adapter
from . import oanda as oanda_adapter
from . import mt5_adapter

log = structlog.get_logger()

# Registry key for the simulated paper adapter (overrides the real venue in paper mode).
PAPER = "paper"


@runtime_checkable
class BaseExchangeAdapter(Protocol):
    """Uniform execution interface implemented by every venue adapter."""

    venue: str

    async def execute(self, order: OrderRequest) -> OrderResult:
        """Submit one order and return a normalised OrderResult. Never raises."""
        ...


class PaperAdapter:
    """Simulated fills against the Redis tick cache — used for every venue in paper mode."""

    venue = PAPER

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    async def execute(self, order: OrderRequest) -> OrderResult:
        fee_bps = (
            self._settings.OANDA_SPREAD_BPS
            if order.venue == "oanda"
            else self._settings.BINANCE_TAKER_FEE_BPS
        )
        return await paper_adapter.execute(order=order, redis=self._redis, fee_bps=fee_bps)


class BinanceAdapter:
    """Binance spot + USDM perp via CCXT async."""

    venue = "binance"

    def __init__(self, spot_exchange: Any, perp_exchange: Any, settings: Settings) -> None:
        self._spot = spot_exchange
        self._perp = perp_exchange
        self._settings = settings

    async def execute(self, order: OrderRequest) -> OrderResult:
        if self._spot is None or self._perp is None:
            raise RuntimeError("Binance exchange instances not initialised")
        return await binance_adapter.execute(
            order=order,
            spot_exchange=self._spot,
            perp_exchange=self._perp,
            fee_bps=self._settings.BINANCE_TAKER_FEE_BPS,
        )


class BybitAdapter:
    """Bybit linear USDT perpetuals via CCXT async."""

    venue = "bybit"

    def __init__(self, exchange: Any, settings: Settings) -> None:
        self._exchange = exchange
        self._settings = settings

    async def execute(self, order: OrderRequest) -> OrderResult:
        if self._exchange is None:
            raise RuntimeError("Bybit exchange instance not initialised")
        return await bybit_adapter.execute(
            order=order,
            exchange=self._exchange,
            fee_bps=self._settings.BYBIT_TAKER_FEE_BPS,
        )


class OkxAdapter:
    """OKX linear USDT perpetuals via CCXT async."""

    venue = "okx"

    def __init__(self, exchange: Any, settings: Settings) -> None:
        self._exchange = exchange
        self._settings = settings

    async def execute(self, order: OrderRequest) -> OrderResult:
        if self._exchange is None:
            raise RuntimeError("OKX exchange instance not initialised")
        return await okx_adapter.execute(
            order=order,
            exchange=self._exchange,
            fee_bps=self._settings.OKX_TAKER_FEE_BPS,
        )


class KrakenAdapter:
    """Kraken spot via CCXT async."""

    venue = "kraken"

    def __init__(self, exchange: Any, settings: Settings) -> None:
        self._exchange = exchange
        self._settings = settings

    async def execute(self, order: OrderRequest) -> OrderResult:
        if self._exchange is None:
            raise RuntimeError("Kraken exchange instance not initialised")
        return await kraken_adapter.execute(
            order=order,
            exchange=self._exchange,
            fee_bps=self._settings.KRAKEN_TAKER_FEE_BPS,
        )


class OandaAdapter:
    """Oanda forex/CFD via the v20 REST API."""

    venue = "oanda"

    def __init__(self, client: Any, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def execute(self, order: OrderRequest) -> OrderResult:
        if self._client is None:
            raise RuntimeError("Oanda HTTP client not initialised")
        return await oanda_adapter.execute(
            order=order,
            client=self._client,
            api_key=self._settings.OANDA_API_KEY,
            account_id=self._settings.OANDA_ACCOUNT_ID,
            base_url=self._settings.oanda_rest_url,
        )


class Mt5Adapter:
    """MetaTrader 5 via the Windows-native bridge service."""

    venue = "mt5"

    def __init__(self, client: Any, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def execute(self, order: OrderRequest) -> OrderResult:
        if self._client is None:
            raise RuntimeError("MT5 HTTP client not initialised — check MT5_BRIDGE_URL")
        return await mt5_adapter.execute(
            order=order,
            client=self._client,
            bridge_url=self._settings.MT5_BRIDGE_URL,
            api_key=self._settings.MT5_BRIDGE_API_KEY,
            spread_bps=self._settings.MT5_SPREAD_BPS,
        )


class AdapterRegistry:
    """
    Owns the venue adapters AND the lifecycle of the exchange clients they wrap.

    build() creates each venue's live client from settings (paper mode opens none,
    except the MT5 bridge client which itself honours per-order paper_mode), wires
    one adapter per venue, and resolve() routes by venue (paper-mode override sends
    every venue to the paper adapter). aclose() closes every client created here.

    Adding a venue is now self-contained: add its make_*/client creation in build()
    and one adapter entry — main.py and consumer.run do not change.
    """

    def __init__(self, settings: Settings, redis: Redis) -> None:
        self._settings = settings
        self._redis = redis
        self._adapters: dict[str, BaseExchangeAdapter] = {}
        self._clients: dict[str, Any] = {}  # name -> client, for status() + aclose()

    async def build(self) -> "AdapterRegistry":
        s, r = self._settings, self._redis
        self._adapters[PAPER] = PaperAdapter(r, s)

        # MT5 bridge client — always created if configured (bridge handles paper/live
        # per-order). Containers reach the Windows-native bridge via host.containers.internal.
        mt5_client = None
        if s.mt5_configured:
            mt5_client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=5.0),
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )
            self._clients["mt5"] = mt5_client
            log.info(
                "executor.mt5_client_ready",
                bridge_url=s.MT5_BRIDGE_URL,
                api_key_set=bool(s.MT5_BRIDGE_API_KEY),
            )
        else:
            log.warning("executor.mt5_not_configured", hint="MT5_BRIDGE_URL empty — MT5 orders will error")
        self._adapters["mt5"] = Mt5Adapter(mt5_client, s)

        # Live venue clients are opened only in live mode.
        spot = perp = oanda = bybit = okx = kraken = None
        if not s.is_paper:
            if s.BINANCE_API_KEY and s.BINANCE_API_SECRET:
                spot = binance_adapter.make_spot_exchange(s.BINANCE_API_KEY, s.BINANCE_API_SECRET, s.BINANCE_TESTNET)
                perp = binance_adapter.make_perp_exchange(s.BINANCE_API_KEY, s.BINANCE_API_SECRET, s.BINANCE_TESTNET)
                self._clients["binance_spot"] = spot
                self._clients["binance_perp"] = perp
                log.info("executor.binance_ready", testnet=s.BINANCE_TESTNET)
            else:
                log.warning("executor.binance_not_configured", hint="BINANCE_API_KEY/SECRET not set")

            if s.OANDA_API_KEY and s.OANDA_ACCOUNT_ID:
                oanda = httpx.AsyncClient(
                    timeout=httpx.Timeout(10.0),
                    limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
                )
                self._clients["oanda"] = oanda
                log.info("executor.oanda_ready", environment=s.OANDA_ENVIRONMENT, base_url=s.oanda_rest_url)
            else:
                log.warning("executor.oanda_not_configured", hint="OANDA_API_KEY/ACCOUNT_ID not set")

            if s.BYBIT_API_KEY and s.BYBIT_API_SECRET:
                bybit = bybit_adapter.make_exchange(s.BYBIT_API_KEY, s.BYBIT_API_SECRET, s.BYBIT_TESTNET)
                self._clients["bybit"] = bybit
                log.info("executor.bybit_ready", testnet=s.BYBIT_TESTNET)
            else:
                log.warning("executor.bybit_not_configured", hint="BYBIT_API_KEY/SECRET not set")

            if s.OKX_API_KEY and s.OKX_API_SECRET:
                okx = okx_adapter.make_exchange(s.OKX_API_KEY, s.OKX_API_SECRET, s.OKX_TESTNET, s.OKX_API_PASSWORD)
                self._clients["okx"] = okx
                log.info("executor.okx_ready", testnet=s.OKX_TESTNET)
            else:
                log.warning("executor.okx_not_configured", hint="OKX_API_KEY/SECRET not set")

            if s.KRAKEN_API_KEY and s.KRAKEN_API_SECRET:
                kraken = kraken_adapter.make_exchange(s.KRAKEN_API_KEY, s.KRAKEN_API_SECRET, s.KRAKEN_TESTNET)
                self._clients["kraken"] = kraken
                log.info("executor.kraken_ready")
            else:
                log.warning("executor.kraken_not_configured", hint="KRAKEN_API_KEY/SECRET not set")
        else:
            log.info("executor.paper_mode", note="No exchange connections opened — all fills simulated")

        self._adapters["binance"] = BinanceAdapter(spot, perp, s)
        self._adapters["bybit"] = BybitAdapter(bybit, s)
        self._adapters["okx"] = OkxAdapter(okx, s)
        self._adapters["kraken"] = KrakenAdapter(kraken, s)
        self._adapters["oanda"] = OandaAdapter(oanda, s)
        return self

    @property
    def venues(self) -> list[str]:
        return sorted(self._adapters)

    def resolve(self, venue: str) -> BaseExchangeAdapter | None:
        """Adapter for a leg; paper mode routes every venue to the paper adapter."""
        key = PAPER if self._settings.is_paper else venue
        return self._adapters.get(key)

    def status(self) -> dict[str, dict]:
        """Per-venue config/initialised status for the /health/execution endpoint."""
        s = self._settings
        c = self._clients
        return {
            "paper": {"active": s.is_paper},
            "binance": {
                "configured": bool(s.BINANCE_API_KEY and s.BINANCE_API_SECRET),
                "initialised": "binance_spot" in c and "binance_perp" in c,
                "testnet": s.BINANCE_TESTNET, "taker_fee_bps": s.BINANCE_TAKER_FEE_BPS,
            },
            "bybit": {
                "configured": bool(s.BYBIT_API_KEY and s.BYBIT_API_SECRET),
                "initialised": "bybit" in c, "testnet": s.BYBIT_TESTNET,
                "taker_fee_bps": s.BYBIT_TAKER_FEE_BPS,
            },
            "okx": {
                "configured": bool(s.OKX_API_KEY and s.OKX_API_SECRET),
                "initialised": "okx" in c, "testnet": s.OKX_TESTNET,
                "taker_fee_bps": s.OKX_TAKER_FEE_BPS,
            },
            "kraken": {
                "configured": bool(s.KRAKEN_API_KEY and s.KRAKEN_API_SECRET),
                "initialised": "kraken" in c, "taker_fee_bps": s.KRAKEN_TAKER_FEE_BPS,
            },
            "oanda": {
                "configured": bool(s.OANDA_API_KEY and s.OANDA_ACCOUNT_ID),
                "initialised": "oanda" in c, "environment": s.OANDA_ENVIRONMENT,
                "spread_bps": s.OANDA_SPREAD_BPS,
            },
            "mt5": {
                "configured": s.mt5_configured, "initialised": "mt5" in c,
                "bridge_url": s.MT5_BRIDGE_URL, "api_key_set": bool(s.MT5_BRIDGE_API_KEY),
                "spread_bps": s.MT5_SPREAD_BPS,
            },
        }

    async def aclose(self) -> None:
        """Close every client created in build(). ccxt uses .close(); httpx .aclose()."""
        for name, client in self._clients.items():
            try:
                closer = getattr(client, "aclose", None) or getattr(client, "close", None)
                if closer is not None:
                    await closer()
                log.info("executor.client_closed", client=name)
            except Exception as exc:
                log.warning("executor.client_close_failed", client=name, error=str(exc))
