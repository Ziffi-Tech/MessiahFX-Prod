"""Health endpoints for journal service."""

from datetime import datetime, timezone

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from mezna_shared.db import check_db_connection
from ..config import settings

router = APIRouter()


@router.get("/live")
async def liveness() -> dict:
    return {
        "status": "ok",
        "service": settings.SERVICE_NAME,
        "version": settings.VERSION,
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

    reconciler_task = getattr(request.app.state, "reconciler_task", None)
    reconciler_ok = reconciler_task is not None and not reconciler_task.done()

    all_ok = db_ok and redis_ok
    http_status = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=http_status,
        content={
            "status": "ok" if all_ok else "degraded",
            "service": settings.SERVICE_NAME,
            "version": settings.VERSION,
            "trading_mode": settings.TRADING_MODE,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dependencies": {
                "database": "ok" if db_ok else "unreachable",
                "redis": "ok" if redis_ok else "unreachable",
                "reconciler": "running" if reconciler_ok else "stopped",
            },
            "config": {
                "reconciliation_interval_seconds": settings.RECONCILIATION_INTERVAL_SECONDS,
                "reconciliation_stale_minutes": settings.RECONCILIATION_STALE_MINUTES,
            },
        },
    )
