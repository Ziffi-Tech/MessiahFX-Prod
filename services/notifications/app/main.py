"""
Notifications service — Telegram + Discord alert dispatcher.

Reads from the notifications:queue Redis list (BLPOP).
Producers push with RPUSH:
  executor  → trade.fill alerts after every order
  risk      → risk.halt, risk.cooldown, risk.rejected alerts

The consumer loop dispatches to all configured channels concurrently.
Channel failures are logged but never crash the loop.

If no channels are configured (TELEGRAM_ENABLED=false, DISCORD_ENABLED=false),
the service still runs — it logs alerts locally. This lets you develop without
configuring real credentials, then add channels when ready.
"""

import asyncio

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

from mezna_shared.logging_config import setup_logging
from mezna_shared.observability import init_sentry
from mezna_shared.redis_client import get_redis, close_redis

from .config import settings
from .routes import health
from . import consumer

setup_logging(
    service_name=settings.SERVICE_NAME,
    log_level=settings.LOG_LEVEL,
    debug=settings.DEBUG,
)
log = structlog.get_logger()
init_sentry(service_name=settings.SERVICE_NAME)


def _task_done_callback(task: asyncio.Task) -> None:
    if task.cancelled():
        log.info("notifications.task_cancelled", task=task.get_name())
        return
    exc = task.exception()
    if exc:
        log.error(
            "notifications.task_crashed",
            task=task.get_name(),
            error=str(exc),
            hint="consumer loop is down — alerts will queue in Redis but not be sent",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "service.starting",
        service=settings.SERVICE_NAME,
        version=settings.VERSION,
        telegram=settings.TELEGRAM_ENABLED,
        discord=settings.DISCORD_ENABLED,
    )

    app.state.redis = await get_redis(settings.REDIS_URL)
    await app.state.redis.ping()

    if not settings.any_channel_configured:
        log.warning(
            "notifications.no_channels_configured",
            hint="Set TELEGRAM_ENABLED=true or DISCORD_ENABLED=true to send real alerts",
        )

    # Launch consumer task
    consumer_task = asyncio.create_task(
        consumer.run(settings=settings, redis=app.state.redis),
        name="notifications-consumer",
    )
    consumer_task.add_done_callback(_task_done_callback)
    app.state.consumer_task = consumer_task

    log.info("service.ready", service=settings.SERVICE_NAME)
    yield

    log.info("service.stopping", service=settings.SERVICE_NAME)
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    await close_redis()
    log.info("service.stopped", service=settings.SERVICE_NAME)


app = FastAPI(
    title="MeznaQuantFX — Notifications",
    description="Telegram + Discord alert dispatcher. Reads from Redis notifications:queue.",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
)

app.include_router(health.router, prefix="/health", tags=["health"])

from mezna_shared.metrics import setup_metrics
setup_metrics(app, service_name=settings.SERVICE_NAME)
