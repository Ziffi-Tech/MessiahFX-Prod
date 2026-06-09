"""
Kraken market data feed — CCXT Pro WebSocket (spot).

Mirrors bybit_feed.py but for Kraken SPOT (no perp suffix; ccxt maps XBT→BTC).
Publishes NormalisedTick under venue "kraken", so a strategy scanning
"kraken:BTC/USD" reads ticks:kraken:BTC/USD. Disabled by default (KRAKEN_SYMBOLS
empty). Public order-book data needs no credentials; Kraken spot has no sandbox.
"""

import asyncio
from datetime import datetime, timezone

import ccxt.pro as ccxtpro
import structlog
from redis.asyncio import Redis

from ..config import Settings
from .normaliser import NormalisedTick, MARKET_TYPE_SPOT
from .publisher import publish_tick, update_heartbeat

log = structlog.get_logger()

VENUE = "kraken"
HEARTBEAT_INTERVAL: int = 10
ORDER_BOOK_DEPTH: int = 5
INITIAL_RETRY: float = 1.0
MAX_RETRY: float = 60.0


def _make_exchange(settings: Settings) -> "ccxtpro.kraken":
    """Instantiate a CCXT Pro Kraken spot exchange."""
    return ccxtpro.kraken(
        {
            "apiKey": settings.KRAKEN_API_KEY or None,
            "secret": settings.KRAKEN_API_SECRET or None,
            "options": {"newUpdates": True},
            "enableRateLimit": True,
        }
    )


async def _watch_symbol(
    exchange: "ccxtpro.kraken",
    symbol: str,
    redis: Redis,
    cache_max: int,
) -> None:
    retry_delay = INITIAL_RETRY
    symbol_log = log.bind(symbol=symbol, market_type=MARKET_TYPE_SPOT, venue=VENUE)
    symbol_log.info("kraken_feed.symbol_starting")

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
                market_type=MARKET_TYPE_SPOT,
                bid=bid,
                ask=ask,
            )
            await publish_tick(redis, tick, cache_max=cache_max)
            retry_delay = INITIAL_RETRY

        except ccxtpro.BadSymbol:
            symbol_log.error("kraken_feed.bad_symbol", hint="Use ccxt spot form, e.g. BTC/USD")
            return
        except ccxtpro.NetworkError as exc:
            symbol_log.warning("kraken_feed.network_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)
        except ccxtpro.ExchangeError as exc:
            symbol_log.error("kraken_feed.exchange_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)
        except asyncio.CancelledError:
            symbol_log.info("kraken_feed.symbol_cancelled")
            raise
        except Exception as exc:
            symbol_log.error("kraken_feed.unexpected_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)


async def _heartbeat_loop(redis: Redis) -> None:
    while True:
        try:
            await update_heartbeat(redis, VENUE)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("kraken_feed.heartbeat_error", error=str(exc))
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def run(settings: Settings, redis: Redis) -> None:
    """Start Kraken symbol watchers + heartbeat. No-op when KRAKEN_SYMBOLS is empty."""
    symbols = settings.kraken_symbol_list
    if not symbols:
        log.info("kraken_feed.disabled", hint="Set KRAKEN_SYMBOLS to enable the Kraken feed")
        return

    exchange = _make_exchange(settings)
    tasks: list[asyncio.Task] = []
    try:
        for symbol in symbols:
            tasks.append(
                asyncio.create_task(
                    _watch_symbol(exchange, symbol, redis, settings.TICK_CACHE_MAX_SIZE),
                    name=f"kraken:spot:{symbol}",
                )
            )
        tasks.append(asyncio.create_task(_heartbeat_loop(redis), name="kraken:heartbeat"))
        log.info("kraken_feed.started", symbols=symbols, total_subscriptions=len(symbols))
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        log.info("kraken_feed.shutting_down")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        await exchange.close()
        log.info("kraken_feed.exchange_closed")
