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

import httpx
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

from mezna_shared.logging_config import setup_logging
from mezna_shared.db import get_engine, check_db_connection, dispose_engine
from mezna_shared.redis_client import get_redis, close_redis

from .config import settings
from .routes import health
from .adapters import binance as binance_adapter
from . import consumer

setup_logging(
    service_name=settings.SERVICE_NAME,
    log_level=settings.LOG_LEVEL,
    debug=settings.DEBUG,
)
log = structlog.get_logger()


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

    # ── MT5 bridge client (always created — bridge handles paper/live internally) ─
    # The bridge runs natively on Windows; containers reach it via host.containers.internal.
    # We always open the client regardless of TRADING_MODE because the bridge's
    # /order/place endpoint respects the paper_mode flag we send per-order.
    mt5_client: httpx.AsyncClient | None = None
    if settings.mt5_configured:
        mt5_client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
        log.info(
            "executor.mt5_client_ready",
            bridge_url=settings.MT5_BRIDGE_URL,
            api_key_set=bool(settings.MT5_BRIDGE_API_KEY),
            note="MT5 bridge client initialised — bridge handles lot sizing",
        )
    else:
        log.warning(
            "executor.mt5_not_configured",
            hint="MT5_BRIDGE_URL is empty — MT5 orders will error",
        )

    # ── Exchange clients (live mode only) ─────────────────────────────────────
    spot_exchange = None
    perp_exchange = None
    oanda_client = None

    if not settings.is_paper:
        if settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET:
            spot_exchange = binance_adapter.make_spot_exchange(
                settings.BINANCE_API_KEY,
                settings.BINANCE_API_SECRET,
                settings.BINANCE_TESTNET,
            )
            perp_exchange = binance_adapter.make_perp_exchange(
                settings.BINANCE_API_KEY,
                settings.BINANCE_API_SECRET,
                settings.BINANCE_TESTNET,
            )
            log.info(
                "executor.binance_ready",
                testnet=settings.BINANCE_TESTNET,
                note="spot + perp exchange instances created",
            )
        else:
            log.warning(
                "executor.binance_not_configured",
                hint="BINANCE_API_KEY/SECRET not set — Binance orders will error",
            )

        if settings.OANDA_API_KEY and settings.OANDA_ACCOUNT_ID:
            oanda_client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0),
                limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            )
            log.info(
                "executor.oanda_ready",
                environment=settings.OANDA_ENVIRONMENT,
                base_url=settings.oanda_rest_url,
            )
        else:
            log.warning(
                "executor.oanda_not_configured",
                hint="OANDA_API_KEY/ACCOUNT_ID not set — Oanda orders will error",
            )
    else:
        log.info(
            "executor.paper_mode",
            note="No exchange connections opened — all fills are simulated",
        )

    # Expose adapters on app.state for health checks
    app.state.spot_exchange = spot_exchange
    app.state.perp_exchange = perp_exchange
    app.state.oanda_client = oanda_client
    app.state.mt5_client = mt5_client

    # ── Consumer task ─────────────────────────────────────────────────────────
    consumer_task = asyncio.create_task(
        consumer.run(
            settings=settings,
            redis=app.state.redis,
            db_engine=app.state.db_engine,
            spot_exchange=spot_exchange,
            perp_exchange=perp_exchange,
            oanda_client=oanda_client,
            mt5_client=mt5_client,
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

    if spot_exchange is not None:
        await spot_exchange.close()
        log.info("executor.binance_spot_closed")
    if perp_exchange is not None:
        await perp_exchange.close()
        log.info("executor.binance_perp_closed")
    if oanda_client is not None:
        await oanda_client.aclose()
        log.info("executor.oanda_client_closed")
    if mt5_client is not None:
        await mt5_client.aclose()
        log.info("executor.mt5_client_closed")

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
