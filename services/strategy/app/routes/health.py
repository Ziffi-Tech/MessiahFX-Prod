"""
Health endpoints for the strategy service.

  GET /health/live       — Liveness: always 200 while the process is up.
  GET /health/ready      — Readiness: DB + Redis reachable.
  GET /health/strategies — Per-strategy toggle state (enabled / latency / cooldown).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from mezna_shared.db import check_db_connection
from mezna_shared.redis_client import RedisKeys
from ..config import settings

router = APIRouter()

_ALL_STRATEGIES = (
    "funding_arb", "stat_arb", "swing",
    "breakout", "mean_reversion_scalp", "momentum",
)


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


@router.get("/strategies")
async def strategy_states(request: Request) -> dict:
    """
    Return the current toggle state for every strategy.
    Reads directly from Redis so it reflects live state (dashboard changes
    take effect immediately — no service restart needed).
    """
    redis = request.app.state.redis
    halt = await redis.get(RedisKeys.HALT) == "1"
    strategies = {}

    for name in _ALL_STRATEGIES:
        state = await redis.hgetall(RedisKeys.strategy_state(name))
        on_cooldown = bool(await redis.exists(RedisKeys.cooldown(name)))
        strategies[name] = {
            "enabled": state.get("enabled", "0") == "1",
            "paper_mode": state.get("paper_mode", "1") == "1",
            "latency_profile": state.get("latency_profile", "standard"),
            "on_cooldown": on_cooldown,
            "effectively_running": (
                state.get("enabled", "0") == "1"
                and not on_cooldown
                and not halt
            ),
        }

    return {
        "system_halted": halt,
        "strategies": strategies,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
