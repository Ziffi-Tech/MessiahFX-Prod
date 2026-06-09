"""
Market-data service — normalised tick ingestion.

Responsibilities (Phase 1):
- Binance WebSocket feed via CCXT Pro (spot + USDM perpetuals)
- Oanda v20 persistent HTTP streaming (FX instruments)
- Normalise all quotes to NormalisedTick format
- Publish to Redis:
    tick:latest:{venue}:{symbol}  — always the most recent bid/ask (hash)
    ticks:{venue}:{symbol}        — ring buffer of recent ticks (list)
    feed:heartbeat:{venue}        — liveness signal with 30 s TTL
- Health endpoints expose feed liveness so the gateway can surface it

Feed design:
- Each feed runs as a long-lived asyncio.Task.
- Failures are caught and retried inside the feed (exponential backoff).
- The service stays up even if one feed is down — the other keeps running.
- On shutdown, tasks are cancelled and awaited cleanly.
"""

import asyncio
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

from mezna_shared.logging_config import setup_logging
from mezna_shared.observability import init_sentry
from mezna_shared.db import get_engine, check_db_connection, dispose_engine
from mezna_shared.redis_client import get_redis, close_redis

from .config import settings
from .routes import health, backfill, ticks
from .feeds import binance_feed, oanda_feed, bybit_feed, okx_feed, kraken_feed, orderbook_feed
from . import bar_writer

setup_logging(
    service_name=settings.SERVICE_NAME,
    log_level=settings.LOG_LEVEL,
    debug=settings.DEBUG,
)
log = structlog.get_logger()
init_sentry(service_name=settings.SERVICE_NAME)

# Module-level task list so the health route can inspect task state
_feed_tasks: list[asyncio.Task] = []


def _on_feed_task_done(task: asyncio.Task) -> None:
    """
    Callback attached to each feed task.
    Logs if a task exits unexpectedly (i.e., not due to cancellation).
    This should not normally happen — feeds catch their own exceptions.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(
            "feed_task.unexpected_exit",
            task=task.get_name(),
            error=str(exc),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect infrastructure, launch feeds. Shutdown: cancel feeds, close connections."""
    log.info("service.starting", service=settings.SERVICE_NAME, version=settings.VERSION)

    # ── Infrastructure ─────────────────────────────────────────────────────────
    app.state.db_engine = get_engine(settings.DATABASE_URL)
    app.state.redis = await get_redis(settings.REDIS_URL)

    db_ok = await check_db_connection(app.state.db_engine)
    if not db_ok:
        raise RuntimeError("Database unreachable at startup")

    try:
        await app.state.redis.ping()
    except Exception as exc:
        raise RuntimeError("Redis unreachable at startup") from exc

    # ── Launch feeds ───────────────────────────────────────────────────────────
    # Each feed runs as a background task. They start immediately but the service
    # is ready before they establish WebSocket/stream connections.

    binance_task = asyncio.create_task(
        binance_feed.run(settings, app.state.redis),
        name="binance_feed",
    )
    binance_task.add_done_callback(_on_feed_task_done)
    _feed_tasks.append(binance_task)

    oanda_task = asyncio.create_task(
        oanda_feed.run(settings, app.state.redis),
        name="oanda_feed",
    )
    oanda_task.add_done_callback(_on_feed_task_done)
    _feed_tasks.append(oanda_task)

    bybit_task = asyncio.create_task(
        bybit_feed.run(settings, app.state.redis),
        name="bybit_feed",
    )
    bybit_task.add_done_callback(_on_feed_task_done)
    _feed_tasks.append(bybit_task)

    okx_task = asyncio.create_task(
        okx_feed.run(settings, app.state.redis),
        name="okx_feed",
    )
    okx_task.add_done_callback(_on_feed_task_done)
    _feed_tasks.append(okx_task)

    kraken_task = asyncio.create_task(
        kraken_feed.run(settings, app.state.redis),
        name="kraken_feed",
    )
    kraken_task.add_done_callback(_on_feed_task_done)
    _feed_tasks.append(kraken_task)

    # Live bar writer — resamples the tick cache into persisted OHLCV candles.
    # Tracked alongside the feeds so it is cancelled + awaited on shutdown.
    bar_writer_task = asyncio.create_task(
        bar_writer.run(settings, app.state.redis, app.state.db_engine),
        name="bar_writer",
    )
    bar_writer_task.add_done_callback(_on_feed_task_done)
    _feed_tasks.append(bar_writer_task)

    # L2 order-book feed — depth ladder for the terminal DOM panel (default off).
    orderbook_task = asyncio.create_task(
        orderbook_feed.run(settings, app.state.redis),
        name="orderbook_feed",
    )
    orderbook_task.add_done_callback(_on_feed_task_done)
    _feed_tasks.append(orderbook_task)

    log.info(
        "service.ready",
        service=settings.SERVICE_NAME,
        binance_spot=settings.binance_spot_list,
        binance_perp=settings.binance_perp_list,
        bybit_perp=settings.bybit_perp_list,
        okx_perp=settings.okx_perp_list,
        kraken_symbols=settings.kraken_symbol_list,
        oanda_instruments=settings.oanda_instrument_list,
        testnet=settings.BINANCE_TESTNET,
    )

    yield  # ── Service is running ────────────────────────────────────────────

    # ── Graceful shutdown ──────────────────────────────────────────────────────
    log.info("service.stopping", service=settings.SERVICE_NAME)

    for task in _feed_tasks:
        task.cancel()

    # Wait for all feed tasks to finish (they catch CancelledError and clean up)
    results = await asyncio.gather(*_feed_tasks, return_exceptions=True)
    for task, result in zip(_feed_tasks, results):
        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
            log.warning("feed_task.shutdown_error", task=task.get_name(), error=str(result))

    _feed_tasks.clear()

    await close_redis()
    await dispose_engine()
    log.info("service.stopped", service=settings.SERVICE_NAME)


app = FastAPI(
    title="MeznaQuantFX — Market Data",
    description="Binance WebSocket + Oanda v20 streaming feed normaliser.",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
)

app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(backfill.router, tags=["backfill"])
app.include_router(ticks.router, tags=["ticks"])

from mezna_shared.metrics import setup_metrics
setup_metrics(app, service_name=settings.SERVICE_NAME)
