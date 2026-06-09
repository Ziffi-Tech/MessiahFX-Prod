"""
L2 order-book feed — CCXT Pro watch_order_book → Redis depth snapshots.

Publishes the top-N levels per side to orderbook:{venue}:{symbol} as a JSON
string with a short TTL, so the terminal's depth-ladder (DOM) panel can render a
real book and detect staleness when a feed dies.

Distinct from the tick feeds: those publish top-of-book as a NormalisedTick for
strategies; this publishes the *full ladder* for display. It uses PUBLIC data
(no API keys) on mainnet so the book is liquid and meaningful even while the
trading feeds run on testnet.

Disabled by default: ORDERBOOK_SYMBOLS is empty unless an operator opts in.
One CCXT Pro client per venue; one watcher coroutine per symbol. Publishes are
throttled (ORDERBOOK_THROTTLE_MS) so a fast book can't hammer Redis.
"""

import asyncio
import json
import time
from datetime import datetime, timezone

import ccxt.pro as ccxtpro
import structlog
from redis.asyncio import Redis

from mezna_shared.redis_client import RedisKeys
from ..config import Settings

log = structlog.get_logger()

INITIAL_RETRY: float = 1.0
MAX_RETRY: float = 60.0


def _make_exchange(venue: str, symbols: list[str]) -> "ccxtpro.Exchange | None":
    """Public (keyless) CCXT Pro client for a venue; swap type if symbols are perps."""
    klass = getattr(ccxtpro, venue, None)
    if klass is None:
        log.error("orderbook_feed.unknown_venue", venue=venue)
        return None
    options: dict = {"newUpdates": True}
    if any(":" in s for s in symbols):
        options["defaultType"] = "swap"
    return klass({"enableRateLimit": True, "options": options})


async def _watch(
    exchange: "ccxtpro.Exchange",
    venue: str,
    symbol: str,
    redis: Redis,
    depth: int,
    throttle_ms: int,
    ttl: int,
) -> None:
    """Stream one symbol's order book, publishing throttled depth snapshots."""
    retry_delay = INITIAL_RETRY
    last_publish = 0.0
    slog = log.bind(venue=venue, symbol=symbol)
    slog.info("orderbook_feed.symbol_starting")

    while True:
        try:
            ob = await exchange.watch_order_book(symbol, limit=depth)

            now_ms = time.monotonic() * 1000
            if now_ms - last_publish < throttle_ms:
                continue
            last_publish = now_ms

            bids = [[float(p), float(a)] for p, a, *_ in ob.get("bids", [])[:depth]]
            asks = [[float(p), float(a)] for p, a, *_ in ob.get("asks", [])[:depth]]
            if not bids or not asks:
                continue

            payload = json.dumps({
                "venue": venue,
                "symbol": symbol,
                "ts": datetime.now(timezone.utc).isoformat(),
                "bids": bids,   # [price, amount] descending
                "asks": asks,   # [price, amount] ascending
            })
            await redis.set(RedisKeys.order_book(venue, symbol), payload, ex=ttl)
            retry_delay = INITIAL_RETRY

        except ccxtpro.BadSymbol:
            slog.error("orderbook_feed.bad_symbol", hint="Check ORDERBOOK_SYMBOLS uses ccxt unified symbols")
            return
        except (ccxtpro.NetworkError, ccxtpro.ExchangeError) as exc:
            slog.warning("orderbook_feed.error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)
        except asyncio.CancelledError:
            slog.info("orderbook_feed.symbol_cancelled")
            raise
        except Exception as exc:
            slog.error("orderbook_feed.unexpected_error", error=str(exc), retry_in=retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY)


async def run(settings: Settings, redis: Redis) -> None:
    """Start order-book watchers. No-op when ORDERBOOK_SYMBOLS is empty."""
    by_venue = settings.orderbook_by_venue
    if not by_venue:
        log.info("orderbook_feed.disabled", hint="Set ORDERBOOK_SYMBOLS to enable the L2 depth feed")
        return

    exchanges: dict[str, "ccxtpro.Exchange"] = {}
    tasks: list[asyncio.Task] = []
    try:
        for venue, symbols in by_venue.items():
            exchange = _make_exchange(venue, symbols)
            if exchange is None:
                continue
            exchanges[venue] = exchange
            for symbol in symbols:
                tasks.append(asyncio.create_task(
                    _watch(
                        exchange, venue, symbol, redis,
                        settings.ORDERBOOK_DEPTH,
                        settings.ORDERBOOK_THROTTLE_MS,
                        settings.ORDERBOOK_TTL_SECONDS,
                    ),
                    name=f"orderbook:{venue}:{symbol}",
                ))

        if not tasks:
            log.warning("orderbook_feed.no_valid_targets")
            return

        log.info("orderbook_feed.started", targets=settings.orderbook_targets, depth=settings.ORDERBOOK_DEPTH)
        await asyncio.gather(*tasks)

    except asyncio.CancelledError:
        log.info("orderbook_feed.shutting_down")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        for exchange in exchanges.values():
            try:
                await exchange.close()
            except Exception:
                pass
        log.info("orderbook_feed.closed")
