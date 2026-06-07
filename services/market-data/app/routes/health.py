"""
Health endpoints for the market-data service.

  GET /health/live   — Liveness: is the process running? (always 200 if up)
  GET /health/ready  — Readiness: are DB and Redis reachable?
  GET /health/feeds  — Feed liveness: did each feed push a heartbeat recently?
"""

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from mezna_shared.db import check_db_connection
from mezna_shared.redis_client import RedisKeys
from ..config import settings

log = structlog.get_logger()
router = APIRouter()

# Feed venues we expect to be running (subset may be skipped if not configured)
_EXPECTED_VENUES = ["binance", "bybit", "oanda"]


@router.get("/live")
async def liveness() -> dict:
    """Always 200 while the process is alive."""
    return {
        "status": "ok",
        "service": settings.SERVICE_NAME,
        "version": settings.VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
async def readiness(request: Request) -> JSONResponse:
    """200 if DB and Redis are reachable; 503 otherwise."""
    db_ok = await check_db_connection(request.app.state.db_engine)
    redis_ok = True
    try:
        await request.app.state.redis.ping()
    except Exception:
        redis_ok = False

    all_ok = db_ok and redis_ok
    return JSONResponse(
        status_code=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ok" if all_ok else "degraded",
            "service": settings.SERVICE_NAME,
            "version": settings.VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dependencies": {
                "database": "ok" if db_ok else "unreachable",
                "redis": "ok" if redis_ok else "unreachable",
            },
        },
    )


@router.get("/feeds")
async def feed_status(request: Request) -> JSONResponse:
    """
    Per-feed liveness based on Redis heartbeat keys.

    The heartbeat key (feed:heartbeat:{venue}) has a 30 s TTL.
    If it exists → feed is alive, value is ISO timestamp of last update.
    If it's missing → feed is dead or hasn't started yet.

    HTTP 200 if all configured feeds are alive; 503 if any are dead.
    """
    redis = request.app.state.redis
    feeds: dict[str, dict] = {}
    all_alive = True

    for venue in _EXPECTED_VENUES:
        heartbeat = await redis.get(RedisKeys.feed_heartbeat(venue))

        # Determine if this feed is expected to be running
        if venue == "binance":
            configured = bool(settings.binance_spot_list or settings.binance_perp_list)
        elif venue == "bybit":
            configured = bool(settings.bybit_perp_list)
        elif venue == "oanda":
            configured = bool(settings.OANDA_API_KEY and settings.OANDA_ACCOUNT_ID and settings.oanda_instrument_list)
        else:
            configured = False

        if configured:
            alive = heartbeat is not None
            if not alive:
                all_alive = False
            feeds[venue] = {
                "configured": True,
                "alive": alive,
                "last_heartbeat": heartbeat,
            }
        else:
            feeds[venue] = {
                "configured": False,
                "alive": None,  # Not expected — don't fail health check
                "last_heartbeat": None,
            }

    return JSONResponse(
        status_code=status.HTTP_200_OK if all_alive else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ok" if all_alive else "degraded",
            "service": settings.SERVICE_NAME,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "feeds": feeds,
        },
    )
