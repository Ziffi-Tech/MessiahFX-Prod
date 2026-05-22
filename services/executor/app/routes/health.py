"""Health endpoints for executor service."""

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from mezna_shared.db import check_db_connection
from mezna_shared.redis_client import RedisKeys
from ..config import settings

log = structlog.get_logger()
router = APIRouter()


@router.get("/live")
async def liveness() -> dict:
    return {
        "status": "ok",
        "service": settings.SERVICE_NAME,
        "version": settings.VERSION,
        "trading_mode": settings.TRADING_MODE,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
async def readiness(request: Request) -> JSONResponse:
    db_ok = await check_db_connection(request.app.state.db_engine)
    redis_ok = True
    try:
        await request.app.state.redis.ping()
    except Exception:
        redis_ok = False

    # Consumer task must be alive for the service to be ready
    consumer_task = getattr(request.app.state, "consumer_task", None)
    consumer_ok = consumer_task is not None and not consumer_task.done()

    all_ok = db_ok and redis_ok and consumer_ok
    http_status = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=http_status,
        content={
            "status": "ok" if all_ok else "degraded",
            "service": settings.SERVICE_NAME,
            "version": settings.VERSION,
            "trading_mode": settings.TRADING_MODE,
            "is_paper": settings.is_paper,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dependencies": {
                "database": "ok" if db_ok else "unreachable",
                "redis": "ok" if redis_ok else "unreachable",
                "consumer_loop": "running" if consumer_ok else "stopped",
            },
        },
    )


@router.get("/execution")
async def execution_status(request: Request) -> JSONResponse:
    """
    Execution pipeline health: queue depth, consumer lag, adapter status.
    Used by the dashboard and monitoring systems.
    """
    redis = request.app.state.redis

    # ── Execution queue metrics ───────────────────────────────────────────────
    queue_depth = -1
    consumer_pending = -1
    consumer_lag = -1

    try:
        queue_depth = await redis.xlen(RedisKeys.SIGNALS_EXECUTION_QUEUE)
    except Exception as exc:
        log.warning("health.xlen_failed", error=str(exc))

    try:
        groups = await redis.xinfo_groups(RedisKeys.SIGNALS_EXECUTION_QUEUE)
        for group in groups:
            if group.get("name") == "executor":
                consumer_pending = group.get("pending", 0)
                consumer_lag = group.get("lag", 0)
                break
    except Exception as exc:
        log.warning("health.xinfo_groups_failed", error=str(exc))

    # ── Consumer task liveness ────────────────────────────────────────────────
    consumer_task = getattr(request.app.state, "consumer_task", None)
    consumer_running = consumer_task is not None and not consumer_task.done()

    # ── Adapter status ────────────────────────────────────────────────────────
    spot_exchange = getattr(request.app.state, "spot_exchange", None)
    perp_exchange = getattr(request.app.state, "perp_exchange", None)
    oanda_client = getattr(request.app.state, "oanda_client", None)
    mt5_client = getattr(request.app.state, "mt5_client", None)

    adapters = {
        "paper": {
            "active": settings.is_paper,
        },
        "binance": {
            "configured": bool(settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET),
            "initialised": spot_exchange is not None and perp_exchange is not None,
            "testnet": settings.BINANCE_TESTNET,
            "taker_fee_bps": settings.BINANCE_TAKER_FEE_BPS,
        },
        "oanda": {
            "configured": bool(settings.OANDA_API_KEY and settings.OANDA_ACCOUNT_ID),
            "initialised": oanda_client is not None,
            "environment": settings.OANDA_ENVIRONMENT,
            "spread_bps": settings.OANDA_SPREAD_BPS,
        },
        "mt5": {
            "configured": settings.mt5_configured,
            "initialised": mt5_client is not None,
            "bridge_url": settings.MT5_BRIDGE_URL,
            "api_key_set": bool(settings.MT5_BRIDGE_API_KEY),
            "spread_bps": settings.MT5_SPREAD_BPS,
        },
    }

    return JSONResponse(
        content={
            "trading_mode": settings.TRADING_MODE,
            "is_paper": settings.is_paper,
            "position_usd": settings.position_usd,
            "consumer": {
                "running": consumer_running,
                "group": "executor",
                "consumer_name": "executor-1",
            },
            "execution_queue": {
                "depth": queue_depth,
                "pending_unacked": consumer_pending,
                "lag": consumer_lag,
            },
            "adapters": adapters,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
