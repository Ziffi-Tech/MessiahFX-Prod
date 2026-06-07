"""
Gateway service — entry point.

Responsibilities:
- API gateway and WebSocket hub for the dashboard
- TradingView webhook receiver (validates, logs, enqueues signals)
- Strategy toggle and kill switch control endpoints
- Routes internal traffic between services
- Auth middleware (API key, Phase 2)

All inbound traffic flows through the gateway.
No service accepts external connections directly.
"""

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mezna_shared.logging_config import setup_logging
from mezna_shared.observability import init_sentry
from mezna_shared.db import get_engine, check_db_connection, dispose_engine
from mezna_shared.redis_client import get_redis, close_redis
from mezna_shared.credential_store import CredentialStore
from mezna_shared.metrics import setup_metrics

from .config import settings
from .routes import health, signals, control, credentials, proxy

setup_logging(
    service_name=settings.SERVICE_NAME,
    log_level=settings.LOG_LEVEL,
    debug=settings.DEBUG,
)
log = structlog.get_logger()
init_sentry(service_name=settings.SERVICE_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    log.info(
        "service.starting",
        service=settings.SERVICE_NAME,
        version=settings.VERSION,
        trading_mode=settings.TRADING_MODE,
    )

    # Connect to infrastructure
    app.state.db_engine = get_engine(settings.DATABASE_URL)
    app.state.redis = await get_redis(settings.REDIS_URL)

    # Verify connections at startup — fail fast, don't start with broken infra
    db_ok = await check_db_connection(app.state.db_engine)
    if not db_ok:
        log.error("service.startup_failed", reason="database unreachable")
        raise RuntimeError("Cannot connect to database at startup")

    try:
        await app.state.redis.ping()
    except Exception as exc:
        log.error("service.startup_failed", reason="redis unreachable", error=str(exc))
        raise RuntimeError("Cannot connect to Redis at startup") from exc

    # Credential store — only initialise if encryption key is set
    if settings.credentials_enabled:
        app.state.credential_store = CredentialStore(
            engine=app.state.db_engine,
            encryption_key=settings.CREDENTIAL_ENCRYPTION_KEY,
        )
        await app.state.credential_store.load_all()
        log.info("credentials.store_ready")
    else:
        app.state.credential_store = None
        log.warning(
            "credentials.store_disabled",
            reason="CREDENTIAL_ENCRYPTION_KEY not set — credential management via dashboard unavailable",
        )

    log.info(
        "service.ready",
        service=settings.SERVICE_NAME,
        db="connected",
        redis="connected",
        trading_mode=settings.TRADING_MODE,
        credentials_enabled=settings.credentials_enabled,
    )

    yield  # Service is running

    # Graceful shutdown
    log.info("service.stopping", service=settings.SERVICE_NAME)
    await close_redis()
    await dispose_engine()
    log.info("service.stopped", service=settings.SERVICE_NAME)


app = FastAPI(
    title="MeznaQuantFX — Gateway",
    description=(
        "API gateway, WebSocket hub, TradingView webhook receiver, "
        "and system control plane."
    ),
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,  # Disable Swagger in production
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    # Must cover every method the proxied APIs serve. Strategy config updates
    # use PATCH (/strategy/configs/{name}); credential management uses DELETE.
    # Omitting them caused browser preflight (OPTIONS) to reject those calls.
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(health.router,       prefix="/health",            tags=["health"])
app.include_router(signals.router,      prefix="/api/v1/signals",    tags=["signals"])
app.include_router(control.router,      prefix="/api/v1/control",    tags=["control"])
app.include_router(credentials.router,  prefix="/api/v1/credentials",tags=["credentials"])
# Service reverse proxy — must be last (catch-all path)
app.include_router(proxy.router,        tags=["proxy"])

setup_metrics(app, service_name=settings.SERVICE_NAME)
