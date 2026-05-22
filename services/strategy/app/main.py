"""
Strategy engine service.

Phase 2 responsibilities:
- Funding rate arbitrage (Binance spot + USDM perp, delta-neutral)
- Statistical arbitrage (spot vs perp z-score mean reversion)
- Swing strategy placeholder (Phase 3)

Each strategy runs as an independent asyncio.Task.
Signals are published to the Redis Stream for AI filter → risk → executor.
"""

import asyncio
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

from mezna_shared.logging_config import setup_logging
from mezna_shared.db import get_engine, check_db_connection, dispose_engine
from mezna_shared.redis_client import get_redis, close_redis

from .config import settings
from .routes import health
from . import runner

setup_logging(
    service_name=settings.SERVICE_NAME,
    log_level=settings.LOG_LEVEL,
    debug=settings.DEBUG,
)
log = structlog.get_logger()

_runner_task: asyncio.Task | None = None


def _on_runner_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("runner.exited_unexpectedly", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _runner_task

    log.info("service.starting", service=settings.SERVICE_NAME, version=settings.VERSION)

    app.state.db_engine = get_engine(settings.DATABASE_URL)
    app.state.redis = await get_redis(settings.REDIS_URL)

    db_ok = await check_db_connection(app.state.db_engine)
    if not db_ok:
        raise RuntimeError("Database unreachable at startup")

    try:
        await app.state.redis.ping()
    except Exception as exc:
        raise RuntimeError("Redis unreachable at startup") from exc

    # ── Launch strategy runner ─────────────────────────────────────────────────
    _runner_task = asyncio.create_task(
        runner.run(settings, app.state.redis),
        name="strategy_runner",
    )
    _runner_task.add_done_callback(_on_runner_done)

    log.info(
        "service.ready",
        service=settings.SERVICE_NAME,
        funding_arb_symbols=settings.funding_arb_symbol_list,
        stat_arb_symbols=settings.stat_arb_symbol_list,
        trading_mode=settings.TRADING_MODE,
    )

    yield  # ── Service is running ─────────────────────────────────────────────

    log.info("service.stopping", service=settings.SERVICE_NAME)

    if _runner_task and not _runner_task.done():
        _runner_task.cancel()
        try:
            await _runner_task
        except asyncio.CancelledError:
            pass

    await close_redis()
    await dispose_engine()
    log.info("service.stopped", service=settings.SERVICE_NAME)


app = FastAPI(
    title="MeznaQuantFX — Strategy Engine",
    description="Funding arb + stat arb signal generation.",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
)

app.include_router(health.router, prefix="/health", tags=["health"])

from mezna_shared.metrics import setup_metrics
setup_metrics(app, service_name=settings.SERVICE_NAME)
