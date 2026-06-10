"""
Journal service — trade recording, reconciliation, P&L, and audit log.

System of record for all trade activity in MeznaQuantFX AI.
Every state transition, AI score, risk decision, and order fill is
queryable here. The dashboard Journal and Risk Log tabs read from this
service.

Background task:
  Reconciliation loop (every RECONCILIATION_INTERVAL_SECONDS):
    - Marks stale pending/open trades as 'error'
    - Corrects open_position_count in Redis based on DB ground truth

Routes:
  GET /trades                    — paginated trade list
  GET /trades/summary            — aggregate stats (fill rate, fees, notional)
  GET /trades/{client_order_id}  — single trade
  GET /opportunities             — opportunity list with funnel metadata
  GET /opportunities/funnel      — conversion rates (detected → executed)
  GET /opportunities/{id}        — single opportunity + linked trades
  GET /pnl/daily                 — daily activity rows (N days)
  GET /pnl/summary               — rolled-up totals
  GET /pnl/positions             — net positions + realized P&L per key
  GET /audit                     — audit log entries
  GET /audit/risk-events         — risk events (halts, cooldowns)
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
from .routes import health, trades, opportunities, pnl, audit, readiness
from . import reconciler

setup_logging(
    service_name=settings.SERVICE_NAME,
    log_level=settings.LOG_LEVEL,
    debug=settings.DEBUG,
)
log = structlog.get_logger()
init_sentry(service_name=settings.SERVICE_NAME)


def _task_done_callback(task: asyncio.Task) -> None:
    if task.cancelled():
        log.info("journal.task_cancelled", task=task.get_name())
        return
    exc = task.exception()
    if exc:
        log.error(
            "journal.task_crashed",
            task=task.get_name(),
            error=str(exc),
            hint="reconciliation is not running — open position counts may drift",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "service.starting",
        service=settings.SERVICE_NAME,
        version=settings.VERSION,
        trading_mode=settings.TRADING_MODE,
        reconciliation_interval=settings.RECONCILIATION_INTERVAL_SECONDS,
    )

    app.state.db_engine = get_engine(settings.DATABASE_URL)
    app.state.redis = await get_redis(settings.REDIS_URL)

    db_ok = await check_db_connection(app.state.db_engine)
    if not db_ok:
        raise RuntimeError("Database unreachable at startup")
    await app.state.redis.ping()

    # Start reconciliation background loop
    reconciler_task = asyncio.create_task(
        reconciler.run(
            settings=settings,
            db_engine=app.state.db_engine,
            redis=app.state.redis,
        ),
        name="journal-reconciler",
    )
    reconciler_task.add_done_callback(_task_done_callback)
    app.state.reconciler_task = reconciler_task

    log.info("service.ready", service=settings.SERVICE_NAME)
    yield

    log.info("service.stopping", service=settings.SERVICE_NAME)

    reconciler_task.cancel()
    try:
        await reconciler_task
    except asyncio.CancelledError:
        pass

    await close_redis()
    await dispose_engine()
    log.info("service.stopped", service=settings.SERVICE_NAME)


app = FastAPI(
    title="MeznaQuantFX — Journal",
    description="Trade recording, reconciliation, P&L, and audit log.",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
)

app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(trades.router, prefix="/trades", tags=["trades"])
app.include_router(opportunities.router, prefix="/opportunities", tags=["opportunities"])
app.include_router(pnl.router, prefix="/pnl", tags=["pnl"])
app.include_router(audit.router, prefix="/audit", tags=["audit"])
app.include_router(readiness.router, tags=["readiness"])

from mezna_shared.metrics import setup_metrics
setup_metrics(app, service_name=settings.SERVICE_NAME)
