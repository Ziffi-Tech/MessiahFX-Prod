"""
Backtest service — historical strategy simulation.

Runs offline simulations of funding_arb and stat_arb strategies
against Binance public OHLCV + funding rate data.
No auth, no database, no Redis required at runtime.

Endpoints:
  POST /backtest/funding-arb  — funding rate arbitrage simulation
  POST /backtest/stat-arb     — statistical arbitrage simulation
  GET  /backtest/symbols      — available symbol pairs

Data source:
  Binance public REST API — no API key required.
  1-minute candles + 8-hour funding rates.
  Downloads happen inline per request (5–30s for large date ranges).

This service is stateless — results are returned in the response.
For large backtests (365d of 1m data), expect ~10–20 seconds.
"""

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

from mezna_shared.logging_config import setup_logging
from mezna_shared.observability import init_sentry
from mezna_shared.metrics import setup_metrics

from .config import settings
from .routes import health, backtest
from .routes import compare
from .routes import regime_backtest
from .routes import ohlcv
from .routes import walk_forward
from .routes import volatility

setup_logging(
    service_name=settings.SERVICE_NAME,
    log_level=settings.LOG_LEVEL,
    debug=settings.DEBUG,
)
log = structlog.get_logger()
init_sentry(service_name=settings.SERVICE_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "service.starting",
        service=settings.SERVICE_NAME,
        version=settings.VERSION,
        max_candles=settings.MAX_TOTAL_CANDLES,
    )

    # Connect to DB if DATABASE_URL is configured (enables /compare endpoints)
    app.state.db_engine = None
    if settings.DATABASE_URL:
        try:
            from mezna_shared.db import get_engine, check_db_connection
            app.state.db_engine = get_engine(settings.DATABASE_URL)
            db_ok = await check_db_connection(app.state.db_engine)
            if db_ok:
                log.info("backtest.db_connected", note="Walk-forward compare endpoints enabled")
            else:
                log.warning("backtest.db_unavailable", hint="Compare endpoints will return 503")
                app.state.db_engine = None
        except Exception as exc:
            log.warning("backtest.db_connect_failed", error=str(exc))
            app.state.db_engine = None
    else:
        log.info("backtest.db_skipped", note="DATABASE_URL not set — compare endpoints disabled")

    log.info("service.ready", service=settings.SERVICE_NAME)
    yield

    if app.state.db_engine is not None:
        from mezna_shared.db import dispose_engine
        await dispose_engine()
    log.info("service.stopped", service=settings.SERVICE_NAME)


app = FastAPI(
    title="MeznaQuantFX — Backtest",
    description=(
        "Historical simulation of funding_arb and stat_arb strategies. "
        "Uses Binance public API for historical OHLCV + funding rate data. "
        "No API key required."
    ),
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs",   # Always show docs — backtest is a dev/research tool
    redoc_url=None,
)

app.include_router(health.router, prefix="/health", tags=["health"])
# Backtest routes at root: /funding-arb, /stat-arb, /symbols, etc.
# Gateway proxy strips the "backtest" prefix before forwarding.
app.include_router(backtest.router,         tags=["backtest"])
app.include_router(compare.router,          tags=["compare"])
app.include_router(regime_backtest.router,  tags=["regime-backtest"])
app.include_router(ohlcv.router,            tags=["ohlcv"])
app.include_router(walk_forward.router,     tags=["walk-forward"])
app.include_router(volatility.router,       tags=["volatility"])

setup_metrics(app, service_name=settings.SERVICE_NAME)
