"""
OKX market data feed — CCXT Pro WebSocket (linear USDT perpetuals / swap).

Mirrors bybit_feed.py. Publishes NormalisedTick under venue "okx", so a strategy
scanning "okx:BTC/USDT:USDT" reads ticks:okx:BTC/USDT:USDT. Disabled by default
(OKX_PERP_SYMBOLS empty). Public order-book data needs no API credentials.
"""

import asyncio
from datetime import datetime, timezone

import ccxt.pro as ccxtpro
import structlog
from redis.asyncio import Redis

from ..config import Settings
from .normaliser import NormalisedTick, MARKET_TYPE_PERP
from .publisher import publish_tick, update_heartbeat

log = structlog.get_logger()

VENUE = "okx"
HEARTBEAT_INTERVAL: int = 10
ORDER_BOOK_DEPTH: int = 5
INITIAL_RETRY: float = 1.0
MAX_RETRY: float = 60.0


def _make_exchange(settings: Settings) -> "ccxtpro.okx":
    """Instantiate a CCXT Pro OKX exchange for linear (swap) perpetuals."""
    exchange = ccxtpro.okx(
        {
            "apiKey": settings.OKX_API_KEY or None,
            "secret": settings.OKX_API_SECRET or None,
            "password": settings.OKX_API_PASSWORD or None,
            "options": {"defaultType": "swap", "newUpdates": True},
            "enableRateLimit": True,
        }
    )
    if settings.OKX_TESTNET:
        exchange.set_sandbox_mode(True)
        log.info("okx_feed.sandbox_mode")
    return exchange


async def _watch_symbol(
    exchange: "ccxtpro.okx",
    symbol: str,
    redis: Redis,
    cache_max: int,
) -> None:
    retry_delay = INITIAL_RETRY
    symbol_log = log.bind(symbol=symbol, market_type=MARKET_TYPE_PERP, venue=VENUE)
    symbol_log.info("okx_feed.symbol_starting")

    while True:
        try:
            ob = await exchange.watch_order_book(symbol, limit=ORDER_BOOK_DEPTH)
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            if not bids or not asks:
                continue
            bid = float(bids[0][0])
            ask = float(asks[0][0])
            if bid <= 0 or ask <= 0 or bid >= ask:
                continue
            tick = NormalisedTick(
                timestamp=datetime.now(timezone.utc),
                venue=VENUE,
                symbol=symbol,
                market_type=MARKET_TYPE_PERP,
                bid=bid,
                ask=ask,
            )
            await publish_tick(redis, tick, cache_max=cache_max)
            retry_delay = INITIAL_RETRY

        except ccxtpro.BadSymbol:
            symbol_log.error("okx_feed.bad_symbol", hint="Use ccxt linear form, e.g. BTC/USDT:USDT")
            return
        except ccxtpro.NetworkError as exc:
            symbol_log.warning("okx_feed.network_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)
        except ccxtpro.ExchangeError as exc:
            symbol_log.error("okx_feed.exchange_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)
        except asyncio.CancelledError:
            symbol_log.info("okx_feed.symbol_cancelled")
            raise
        except Exception as exc:
            symbol_log.error("okx_feed.unexpected_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)


async def _heartbeat_loop(redis: Redis) -> None:
    while True:
        try:
            await update_heartbeat(redis, VENUE)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("okx_feed.heartbeat_error", error=str(exc))
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def run(settings: Settings, redis: Redis) -> None:
    """Start OKX symbol watchers + heartbeat. No-op when OKX_PERP_SYMBOLS is empty."""
    perp_symbols = settings.okx_perp_list
    if not perp_symbols:
        log.info("okx_feed.disabled", hint="Set OKX_PERP_SYMBOLS to enable the OKX feed")
        return

    exchange = _make_exchange(settings)
    tasks: list[asyncio.Task] = []
    try:
        for symbol in perp_symbols:
            tasks.append(
                asyncio.create_task(
                    _watch_symbol(exchange, symbol, redis, settings.TICK_CACHE_MAX_SIZE),
                    name=f"okx:perp:{symbol}",
                )
            )
        tasks.append(asyncio.create_task(_heartbeat_loop(redis), name="okx:heartbeat"))
        log.info("okx_feed.started", perp_symbols=perp_symbols, testnet=settings.OKX_TESTNET,
                 total_subscriptions=len(perp_symbols))
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        log.info("okx_feed.shutting_down")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        await exchange.close()
        log.info("okx_feed.exchange_closed")
