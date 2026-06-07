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

import structlog
from redis.asyncio import Redis

from ..config import Settings
from . import OrderRequest, OrderResult
from . import paper as paper_adapter
from . import binance as binance_adapter
from . import bybit as bybit_adapter
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


def build_registry(
    *,
    settings: Settings,
    redis: Redis,
    spot_exchange: Any,
    perp_exchange: Any,
    oanda_client: Any,
    mt5_client: Any,
    bybit_exchange: Any = None,
) -> dict[str, BaseExchangeAdapter]:
    """
    Build the venue -> adapter registry from the executor's live dependencies.

    Always includes the paper adapter. Live-venue adapters are registered with
    their clients (which may be None in paper mode — they raise only if actually
    used in live mode). To support a new venue, construct its adapter here.
    """
    registry: dict[str, BaseExchangeAdapter] = {
        PAPER: PaperAdapter(redis, settings),
        "binance": BinanceAdapter(spot_exchange, perp_exchange, settings),
        "bybit": BybitAdapter(bybit_exchange, settings),
        "oanda": OandaAdapter(oanda_client, settings),
        "mt5": Mt5Adapter(mt5_client, settings),
    }
    return registry


def resolve_adapter(
    registry: dict[str, BaseExchangeAdapter],
    venue: str,
    *,
    is_paper: bool,
) -> BaseExchangeAdapter | None:
    """
    Select the adapter for a leg. In paper mode all venues route to the paper
    adapter (simulated fills). Returns None for an unknown venue in live mode.
    """
    key = PAPER if is_paper else venue
    return registry.get(key)
