"""
Risk engine service — the synchronous gatekeeper.

ALL hard risk rules are enforced here. No order reaches the executor without
passing through this service. The AI layer is advisory only and cannot override
or bypass any risk rule.

Phase 4 responsibilities:
  - Consume signals:approved (from ai-filter)
  - Run 7 sequential hard checks (kill switch → edge → position limits)
  - Route to signals:execution_queue (approved) or signals:rejected (denied)
  - Auto-halt on daily drawdown breach
  - Per-strategy cooldown on consecutive loss limit
  - Write all decisions to audit_log

Redis keys exclusively owned (written) by this service:
  risk:halt                   — kill switch (string "0" or "1")
  risk:state                  — risk metrics hash
  risk:cooldown:{strategy}    — cooldown TTL keys
"""

import asyncio
import structlog
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI

from mezna_shared.logging_config import setup_logging
from mezna_shared.observability import init_sentry
from mezna_shared.db import get_engine, check_db_connection, dispose_engine
from mezna_shared.redis_client import get_redis, close_redis, RedisKeys
from mezna_shared.schemas.risk import RiskState

from .config import settings
from .routes import health
from . import consumer
from . import metrics_exporter
from .state import check_and_reset_daily

setup_logging(
    service_name=settings.SERVICE_NAME,
    log_level=settings.LOG_LEVEL,
    debug=settings.DEBUG,
)
log = structlog.get_logger()
init_sentry(service_name=settings.SERVICE_NAME)

_consumer_task: asyncio.Task | None = None


async def _initialise_risk_state(redis) -> None:
    """
    Seed risk state in Redis on first startup only.
    Existing state is ALWAYS preserved — a restart must never reset counters.
    """
    existing = await redis.hgetall(RedisKeys.RISK_STATE)
    if not existing:
        initial = RiskState()
        await redis.hset(RedisKeys.RISK_STATE, mapping=initial.to_redis_hash())
        await redis.set(RedisKeys.HALT, "0")
        log.info("risk.state_initialised", note="First startup — defaults seeded")
    else:
        halt = await redis.get(RedisKeys.HALT)
        log.info(
            "risk.state_restored",
            trading_halted=existing.get("trading_halted", "0") == "1",
            daily_drawdown_pct=existing.get("daily_drawdown_pct", "0"),
            consecutive_losses=existing.get("consecutive_losses", "0"),
            halt_flag=halt,
        )
        # Resync halt flag if there was a crash between state write and key write
        if existing.get("trading_halted", "0") == "1" and halt != "1":
            await redis.set(RedisKeys.HALT, "1")
            log.warning("risk.halt_flag_resynced", reason="State mismatch after restart")


def _on_consumer_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("consumer.exited_unexpectedly", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _consumer_task

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

    await _initialise_risk_state(app.state.redis)

    # Check if the trading day has rolled since last run
    reset = await check_and_reset_daily(app.state.redis)
    if reset:
        log.info("risk.daily_reset_on_startup")

    # ── Launch consumer ────────────────────────────────────────────────────────
    _consumer_task = asyncio.create_task(
        consumer.run(settings, app.state.redis, app.state.db_engine),
        name="risk_consumer",
    )
    _consumer_task.add_done_callback(_on_consumer_done)

    # ── Risk-state metrics exporter (gauges + drawdown warning) ────────────────
    _metrics_task = asyncio.create_task(
        metrics_exporter.run(settings, app.state.redis),
        name="risk_metrics_exporter",
    )

    log.info(
        "service.ready",
        service=settings.SERVICE_NAME,
        max_per_trade_pct=settings.RISK_MAX_PER_TRADE_PCT,
        max_daily_drawdown_pct=settings.RISK_MAX_DAILY_DRAWDOWN_PCT,
        max_open_positions=settings.RISK_MAX_OPEN_POSITIONS,
        max_consecutive_losses=settings.RISK_MAX_CONSECUTIVE_LOSSES,
        cooldown_minutes=settings.RISK_COOLDOWN_MINUTES,
        trading_mode=settings.TRADING_MODE,
    )

    yield  # ── Service is running ─────────────────────────────────────────────

    log.info("service.stopping", service=settings.SERVICE_NAME)

    for _task in (_consumer_task, _metrics_task):
        if _task and not _task.done():
            _task.cancel()
            try:
                await _task
            except asyncio.CancelledError:
                pass

    await close_redis()
    await dispose_engine()
    log.info("service.stopped", service=settings.SERVICE_NAME)


app = FastAPI(
    title="MeznaQuantFX — Risk Engine",
    description="Hard pre-trade gatekeeper. No order bypasses this service.",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
)

app.include_router(health.router, prefix="/health", tags=["health"])

from mezna_shared.metrics import setup_metrics
setup_metrics(app, service_name=settings.SERVICE_NAME)
