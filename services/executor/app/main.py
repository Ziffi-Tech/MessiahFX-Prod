"""
Executor service — order router.

Routes normalised orders to:
- Binance (CCXT async, spot + perp)
- Oanda (v20 REST API, forex/CFD)

In paper mode (TRADING_MODE=paper): orders are simulated at current Redis
bid/ask prices. No exchange connection is opened.

In live mode (TRADING_MODE=live): real orders are submitted to exchange APIs.
Live mode requires explicit env var activation — never automatic.

Consumer loop:
  Reads signals:execution_queue → builds order plan → routes to adapters
  → persists fills to DB → ACKs stream message.
  Single consumer (executor-1) serialises all order submission.

CRITICAL: This service runs with a SINGLE Uvicorn worker.
  Concurrent workers would submit duplicate orders and corrupt the DB.
  Never scale this service horizontally without first implementing
  distributed locking on client_order_id.
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
from .routes import health
from .adapters.registry import AdapterRegistry
from . import consumer

setup_logging(
    service_name=settings.SERVICE_NAME,
    log_level=settings.LOG_LEVEL,
    debug=settings.DEBUG,
)
log = structlog.get_logger()
init_sentry(service_name=settings.SERVICE_NAME)


def _task_done_callback(task: asyncio.Task) -> None:
    """Log unexpected task exits — consumer should run forever."""
    if task.cancelled():
        log.info("executor.task_cancelled", task=task.get_name())
        return
    exc = task.exception()
    if exc:
        log.error(
            "executor.task_crashed",
            task=task.get_name(),
            error=str(exc),
            hint="consumer loop died — executor is NOT processing signals",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "service.starting",
        service=settings.SERVICE_NAME,
        version=settings.VERSION,
        trading_mode=settings.TRADING_MODE,
        is_paper=settings.is_paper,
        position_usd=settings.position_usd,
    )

    if not settings.is_paper:
        log.warning(
            "executor.live_mode_active",
            message="LIVE TRADING MODE — real orders will be submitted to exchanges",
            binance_testnet=settings.BINANCE_TESTNET,
            oanda_environment=settings.OANDA_ENVIRONMENT,
        )

    # ── Database + Redis ──────────────────────────────────────────────────────
    app.state.db_engine = get_engine(settings.DATABASE_URL)
    app.state.redis = await get_redis(settings.REDIS_URL)

    db_ok = await check_db_connection(app.state.db_engine)
    if not db_ok:
        raise RuntimeError("Database unreachable at startup")

    await app.state.redis.ping()

    # ── Adapter registry — owns every venue's client + lifecycle ──────────────
    # Creating the registry opens the live exchange clients (per settings; none in
    # paper mode except the MT5 bridge client). Adding a venue is a registry-only
    # change — main.py no longer threads per-venue clients to the consumer.
    registry = await AdapterRegistry(settings, app.state.redis).build()
    app.state.adapter_registry = registry

    # ── Consumer task ─────────────────────────────────────────────────────────
    consumer_task = asyncio.create_task(
        consumer.run(
            settings=settings,
            redis=app.state.redis,
            db_engine=app.state.db_engine,
            registry=registry,
        ),
        name="executor-consumer",
    )
    consumer_task.add_done_callback(_task_done_callback)
    app.state.consumer_task = consumer_task

    log.info("service.ready", service=settings.SERVICE_NAME)

    yield

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    log.info("service.stopping", service=settings.SERVICE_NAME)

    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    await registry.aclose()

    await close_redis()
    await dispose_engine()
    log.info("service.stopped", service=settings.SERVICE_NAME)


app = FastAPI(
    title="MeznaQuantFX — Executor",
    description="Order router: Binance (CCXT async) + Oanda (v20 API). Paper/live mode.",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
)

app.include_router(health.router, prefix="/health", tags=["health"])

from mezna_shared.metrics import setup_metrics
setup_metrics(app, service_name=settings.SERVICE_NAME)
