"""
Bybit market data feed — CCXT Pro WebSocket (linear USDT perpetuals).

Mirrors binance_feed.py: one CCXT Pro exchange instance (defaultType=swap) with
one watcher coroutine per symbol, all multiplexed over a single WebSocket, plus a
heartbeat coroutine. Publishes NormalisedTick objects to Redis under venue
"bybit", so a strategy scanning "bybit:BTC/USDT:USDT" reads ticks:bybit:BTC/USDT:USDT.

Disabled by default: BYBIT_PERP_SYMBOLS is empty unless configured, so this feed
is a no-op until an operator opts in.

Retry policy matches binance_feed (exponential backoff 1s → 60s; BadSymbol exits;
CancelledError propagates for clean shutdown).
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

VENUE = "bybit"
HEARTBEAT_INTERVAL: int = 10
ORDER_BOOK_DEPTH: int = 5
INITIAL_RETRY: float = 1.0
MAX_RETRY: float = 60.0


def _make_exchange(settings: Settings) -> "ccxtpro.bybit":
    """Instantiate a CCXT Pro Bybit exchange for linear (swap) perpetuals."""
    exchange = ccxtpro.bybit(
        {
            "apiKey": settings.BYBIT_API_KEY or None,
            "secret": settings.BYBIT_API_SECRET or None,
            "options": {
                "defaultType": "swap",   # linear USDT-margined perpetuals
                "newUpdates": True,
            },
            "enableRateLimit": True,
        }
    )
    if settings.BYBIT_TESTNET:
        exchange.set_sandbox_mode(True)
        log.info("bybit_feed.sandbox_mode")
    return exchange


async def _watch_symbol(
    exchange: "ccxtpro.bybit",
    symbol: str,
    redis: Redis,
    cache_max: int,
) -> None:
    """Watch one symbol's order book in a tight loop, publishing each top-of-book."""
    retry_delay = INITIAL_RETRY
    symbol_log = log.bind(symbol=symbol, market_type=MARKET_TYPE_PERP, venue=VENUE)
    symbol_log.info("bybit_feed.symbol_starting")

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
            symbol_log.error("bybit_feed.bad_symbol", hint="Check BYBIT_PERP_SYMBOLS (use ccxt linear form, e.g. BTC/USDT:USDT)")
            return

        except ccxtpro.NetworkError as exc:
            symbol_log.warning("bybit_feed.network_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)

        except ccxtpro.ExchangeError as exc:
            symbol_log.error("bybit_feed.exchange_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)

        except asyncio.CancelledError:
            symbol_log.info("bybit_feed.symbol_cancelled")
            raise

        except Exception as exc:
            symbol_log.error("bybit_feed.unexpected_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)


async def _heartbeat_loop(redis: Redis) -> None:
    """Refresh the Bybit feed heartbeat key every HEARTBEAT_INTERVAL seconds."""
    while True:
        try:
            await update_heartbeat(redis, VENUE)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("bybit_feed.heartbeat_error", error=str(exc))
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def run(settings: Settings, redis: Redis) -> None:
    """
    Start all symbol watchers + heartbeat. No-op when BYBIT_PERP_SYMBOLS is empty.

    Called from main.py lifespan as an asyncio.Task; runs until cancelled.
    """
    perp_symbols = settings.bybit_perp_list
    if not perp_symbols:
        log.info("bybit_feed.disabled", hint="Set BYBIT_PERP_SYMBOLS to enable the Bybit feed")
        return

    exchange = _make_exchange(settings)
    tasks: list[asyncio.Task] = []

    try:
        for symbol in perp_symbols:
            tasks.append(
                asyncio.create_task(
                    _watch_symbol(exchange, symbol, redis, settings.TICK_CACHE_MAX_SIZE),
                    name=f"bybit:perp:{symbol}",
                )
            )
        tasks.append(asyncio.create_task(_heartbeat_loop(redis), name="bybit:heartbeat"))

        log.info(
            "bybit_feed.started",
            perp_symbols=perp_symbols,
            testnet=settings.BYBIT_TESTNET,
            total_subscriptions=len(perp_symbols),
        )

        await asyncio.gather(*tasks)

    except asyncio.CancelledError:
        log.info("bybit_feed.shutting_down")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    finally:
        await exchange.close()
        log.info("bybit_feed.exchange_closed")
