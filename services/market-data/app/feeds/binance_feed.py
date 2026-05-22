"""
Binance market data feed — CCXT Pro WebSocket.

Subscribes to order books for all configured spot and USDM perpetual symbols.
Publishes NormalisedTick objects to Redis on every update.

Architecture:
  - Two exchange instances: one for spot (defaultType=spot),
    one for USDM perps (defaultType=future).
  - One asyncio coroutine per symbol. All coroutines share the same underlying
    WebSocket connection per exchange instance (CCXT Pro multiplexes internally).
  - A separate heartbeat coroutine refreshes the Redis heartbeat key every
    HEARTBEAT_INTERVAL seconds.

Retry policy (per symbol):
  - NetworkError / ExchangeError → exponential backoff, 1 s → 60 s.
  - CCXT Pro also retries internally for WebSocket reconnection.
  - Unexpected errors → same backoff + error log.
  - CancelledError → propagated immediately (clean shutdown).

Note on testnet vs mainnet:
  BINANCE_TESTNET=true puts the exchange in sandbox mode (testnet.binance.vision
  for spot; testnet.binancefuture.com for futures). Testnet order books are
  sparse and synthetic — real spread data requires mainnet. For paper trading
  with realistic data, set BINANCE_TESTNET=false in .env and set
  BINANCE_API_KEY/SECRET to empty strings (public order book data needs no auth).
"""

import asyncio
from datetime import datetime, timezone

import ccxt.pro as ccxtpro
import structlog
from redis.asyncio import Redis

from ..config import Settings
from .normaliser import NormalisedTick, MARKET_TYPE_SPOT, MARKET_TYPE_PERP
from .publisher import publish_tick, update_heartbeat

log = structlog.get_logger()

VENUE = "binance"
HEARTBEAT_INTERVAL: int = 10   # seconds between heartbeat refreshes
ORDER_BOOK_DEPTH: int = 5      # levels to request (min for most exchanges)
INITIAL_RETRY: float = 1.0     # seconds
MAX_RETRY: float = 60.0        # seconds


def _make_exchange(settings: Settings, market_type: str) -> ccxtpro.binance:
    """
    Instantiate a CCXT Pro Binance exchange for the given market type.

    Args:
        market_type: "spot" for spot markets, "future" for USDM perpetuals.
    """
    exchange = ccxtpro.binance(
        {
            "apiKey": settings.BINANCE_API_KEY or None,
            "secret": settings.BINANCE_API_SECRET or None,
            "options": {
                "defaultType": market_type,
                # CCXT Pro: only emit the delta (changed levels) — more efficient
                "newUpdates": True,
            },
            "enableRateLimit": True,
        }
    )
    if settings.BINANCE_TESTNET:
        exchange.set_sandbox_mode(True)
        log.info("binance_feed.sandbox_mode", market_type=market_type)
    return exchange


async def _watch_symbol(
    exchange: ccxtpro.binance,
    symbol: str,
    market_type: str,
    redis: Redis,
    cache_max: int,
) -> None:
    """
    Watch a single symbol's order book in a tight loop.

    CCXT Pro's watch_order_book() is a long-poll coroutine — it awaits until
    the exchange emits a new update, then returns immediately. This means the
    loop runs at the exchange's natural update rate with no artificial sleep.

    A symbol is skipped silently if it's not listed on the exchange (e.g., a
    testnet symbol that doesn't exist) — logged once, then stops retrying.
    """
    retry_delay = INITIAL_RETRY
    symbol_log = log.bind(symbol=symbol, market_type=market_type, venue=VENUE)
    symbol_log.info("binance_feed.symbol_starting")

    while True:
        try:
            ob = await exchange.watch_order_book(symbol, limit=ORDER_BOOK_DEPTH)

            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            if not bids or not asks:
                continue

            bid = float(bids[0][0])
            ask = float(asks[0][0])

            # Guard against synthetic/corrupt testnet data
            if bid <= 0 or ask <= 0 or bid >= ask:
                continue

            tick = NormalisedTick(
                timestamp=datetime.now(timezone.utc),
                venue=VENUE,
                symbol=symbol,
                market_type=market_type,
                bid=bid,
                ask=ask,
            )
            await publish_tick(redis, tick, cache_max=cache_max)
            retry_delay = INITIAL_RETRY  # reset on success

        except ccxtpro.BadSymbol:
            # Symbol doesn't exist on this exchange/environment — log and exit
            symbol_log.error("binance_feed.bad_symbol", hint="Check BINANCE_SPOT_SYMBOLS / BINANCE_PERP_SYMBOLS")
            return

        except ccxtpro.NetworkError as exc:
            symbol_log.warning("binance_feed.network_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)

        except ccxtpro.ExchangeError as exc:
            symbol_log.error("binance_feed.exchange_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)

        except asyncio.CancelledError:
            symbol_log.info("binance_feed.symbol_cancelled")
            raise

        except Exception as exc:
            symbol_log.error("binance_feed.unexpected_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)


async def _heartbeat_loop(redis: Redis) -> None:
    """
    Refresh the Binance feed heartbeat key every HEARTBEAT_INTERVAL seconds.
    The heartbeat TTL is 30 s (see publisher.py), so this runs every 10 s
    giving 3× safety margin before the key expires.
    """
    while True:
        try:
            await update_heartbeat(redis, VENUE)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("binance_feed.heartbeat_error", error=str(exc))
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def run(settings: Settings, redis: Redis) -> None:
    """
    Main entry point — start all symbol watchers and the heartbeat loop.

    Called from main.py lifespan as an asyncio.Task. Runs until the task is
    cancelled (service shutdown).

    Two exchange instances are created (spot + futures). CCXT Pro multiplexes
    all symbol subscriptions per instance over a single WebSocket connection.
    Both exchanges are closed cleanly in the finally block.
    """
    spot_symbols = settings.binance_spot_list
    perp_symbols = settings.binance_perp_list

    if not spot_symbols and not perp_symbols:
        log.warning("binance_feed.no_symbols_configured", hint="Set BINANCE_SPOT_SYMBOLS and/or BINANCE_PERP_SYMBOLS")
        return

    spot_exchange = _make_exchange(settings, "spot")
    perp_exchange = _make_exchange(settings, "future")

    tasks: list[asyncio.Task] = []

    try:
        for symbol in spot_symbols:
            tasks.append(
                asyncio.create_task(
                    _watch_symbol(spot_exchange, symbol, MARKET_TYPE_SPOT, redis, settings.TICK_CACHE_MAX_SIZE),
                    name=f"binance:spot:{symbol}",
                )
            )

        for symbol in perp_symbols:
            tasks.append(
                asyncio.create_task(
                    _watch_symbol(perp_exchange, symbol, MARKET_TYPE_PERP, redis, settings.TICK_CACHE_MAX_SIZE),
                    name=f"binance:perp:{symbol}",
                )
            )

        tasks.append(
            asyncio.create_task(_heartbeat_loop(redis), name="binance:heartbeat")
        )

        log.info(
            "binance_feed.started",
            spot_symbols=spot_symbols,
            perp_symbols=perp_symbols,
            testnet=settings.BINANCE_TESTNET,
            total_subscriptions=len(spot_symbols) + len(perp_symbols),
        )

        # gather() propagates the first exception that is not CancelledError.
        # Individual symbol tasks handle their own retries internally, so this
        # only raises if something truly unrecoverable happens at the task level.
        await asyncio.gather(*tasks)

    except asyncio.CancelledError:
        log.info("binance_feed.shutting_down")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    finally:
        await spot_exchange.close()
        await perp_exchange.close()
        log.info("binance_feed.exchanges_closed")
