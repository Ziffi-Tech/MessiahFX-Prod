"""Health endpoints — liveness and readiness probes."""

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mezna_shared.db import check_db_connection
from ..config import settings

log = structlog.get_logger()
router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    trading_mode: str
    timestamp: str
    dependencies: dict[str, str] | None = None


@router.get("/live", response_model=HealthResponse, summary="Liveness probe")
async def liveness() -> HealthResponse:
    """
    Always returns 200 if the service process is running.
    Used by Podman/Coolify to decide whether to restart the container.
    Does NOT check dependencies — use /health/ready for that.
    """
    return HealthResponse(
        status="ok",
        service=settings.SERVICE_NAME,
        version=settings.VERSION,
        trading_mode=settings.TRADING_MODE,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/ready",
    response_model=HealthResponse,
    summary="Readiness probe — checks DB and Redis connectivity",
)
async def readiness(request: Request) -> JSONResponse:
    """
    Returns 200 if all dependencies are reachable.
    Returns 503 if any dependency is unhealthy.
    Used by load balancers and Coolify to decide whether to route traffic.
    """
    db_status = "ok"
    redis_status = "ok"

    # Database check
    db_ok = await check_db_connection(request.app.state.db_engine)
    if not db_ok:
        db_status = "unreachable"
        log.warning("health.dependency_unhealthy", dependency="database")

    # Redis check
    try:
        await request.app.state.redis.ping()
    except Exception as exc:
        redis_status = f"unreachable: {str(exc)[:50]}"
        log.warning("health.dependency_unhealthy", dependency="redis", error=str(exc))

    all_ok = db_status == "ok" and redis_status == "ok"
    http_status = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE

    response = HealthResponse(
        status="ok" if all_ok else "degraded",
        service=settings.SERVICE_NAME,
        version=settings.VERSION,
        trading_mode=settings.TRADING_MODE,
        timestamp=datetime.now(timezone.utc).isoformat(),
        dependencies={"database": db_status, "redis": redis_status},
    )

    return JSONResponse(content=response.model_dump(), status_code=http_status)
