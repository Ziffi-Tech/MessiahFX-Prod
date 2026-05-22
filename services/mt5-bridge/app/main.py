"""
MT5 Bridge Service — Windows-native REST API over MetaTrader 5.

This service runs NATIVELY on Windows (NOT in a Podman/Docker container).
It connects to a running MetaTrader 5 terminal via the MetaTrader5 Python
package and exposes a simple HTTP API on port 8010.

The containerised executor service calls this bridge to:
  - Get live ticks from MT5 (for position sizing reference)
  - Place market orders (after lot size calculation)
  - Close open positions
  - Query account info and open positions

Startup:
  1. Read config from .env (MT5_ACCOUNT, MT5_PASSWORD, MT5_SERVER, BRIDGE_API_KEY)
  2. Connect to running MT5 terminal
  3. Start FastAPI on port 8010

Run with:
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
  OR: double-click run.bat on Windows

IMPORTANT:
  MT5 terminal MUST be open and logged in before starting this service.
  The service does NOT auto-login if the terminal is closed.
"""

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

from .config import settings
from . import mt5_client
from .routes.health import router as health_router
from .routes.market import router as market_router
from .routes.orders import router as orders_router
from .routes.account import router as account_router

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    log.info(
        "mt5_bridge.starting",
        service=settings.SERVICE_NAME,
        version=settings.VERSION,
        mt5_package=mt5_client.MT5_AVAILABLE,
        api_key_set=bool(settings.BRIDGE_API_KEY),
    )

    if not settings.BRIDGE_API_KEY:
        log.warning(
            "mt5_bridge.no_api_key",
            hint="BRIDGE_API_KEY is not set — all requests are unauthenticated. "
                 "Set it in .env and in MT5_BRIDGE_API_KEY on the executor side.",
        )

    if not mt5_client.MT5_AVAILABLE:
        log.error(
            "mt5_bridge.no_package",
            hint="MetaTrader5 Python package is not installed. "
                 "Run: pip install MetaTrader5 (Windows only)",
        )
    else:
        # Connect to MT5 terminal
        connected = await mt5_client.connect(
            account=settings.MT5_ACCOUNT,
            password=settings.MT5_PASSWORD,
            server=settings.MT5_SERVER,
            path=settings.MT5_PATH,
        )
        if connected:
            log.info(
                "mt5_bridge.mt5_connected",
                account=settings.MT5_ACCOUNT,
                server=settings.MT5_SERVER,
            )
        else:
            log.warning(
                "mt5_bridge.mt5_not_connected",
                hint="MT5 terminal may not be running. "
                     "Open MT5 terminal and ensure you are logged in, then restart this service.",
            )

    app.state.settings = settings
    log.info("mt5_bridge.ready", port=settings.PORT)

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if mt5_client.MT5_AVAILABLE:
        await mt5_client.shutdown()
    log.info("mt5_bridge.stopped")


app = FastAPI(
    title="MeznaQuantFX — MT5 Bridge",
    description=(
        "Windows-native bridge service connecting MeznaQuantFX containers "
        "to MetaTrader 5 terminal for Forex, CFD, and Index execution."
    ),
    version=settings.VERSION,
    docs_url="/docs",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(market_router)
app.include_router(orders_router)
app.include_router(account_router)
