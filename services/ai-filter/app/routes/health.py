"""
Health endpoints for the AI filter service.

  GET /health/live   — Liveness: always 200 while the process is up.
  GET /health/ready  — Readiness: DB + Redis reachable.
  GET /health/ai     — AI configuration status and stream queue depth.
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from mezna_shared.db import check_db_connection
from mezna_shared.redis_client import RedisKeys
from ..config import settings
from ..news_sentinel import _HEALTH_KEY as _SENTINEL_HEALTH_KEY

router = APIRouter()

_GROUP = "ai-filter"


@router.get("/live")
async def liveness() -> dict:
    return {
        "status": "ok",
        "service": settings.SERVICE_NAME,
        "version": settings.VERSION,
        "ai_configured": settings.ai_configured,
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
            "ai_configured": settings.ai_configured,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dependencies": {
                "database": "ok" if db_ok else "unreachable",
                "redis": "ok" if redis_ok else "unreachable",
            },
        },
    )


@router.get("/ai")
async def ai_status(request: Request) -> dict:
    """
    AI configuration and stream queue depth.

    pending_unacked: messages read but not yet ACKed — should be 0 in normal operation.
    """
    redis = request.app.state.redis

    try:
        opportunities_len = await redis.xlen(RedisKeys.SIGNALS_OPPORTUNITIES)
    except Exception:
        opportunities_len = None

    try:
        approved_len = await redis.xlen(RedisKeys.SIGNALS_APPROVED)
    except Exception:
        approved_len = None

    try:
        pending_info = await redis.xpending(RedisKeys.SIGNALS_OPPORTUNITIES, _GROUP)
        pending_count = pending_info.get("pending", 0) if isinstance(pending_info, dict) else 0
    except Exception:
        pending_count = None

    # ── News sentinel health ──────────────────────────────────────────────────
    sentinel_health = None
    sentiment_ages: dict[str, str | None] = {}
    try:
        raw = await redis.get(_SENTINEL_HEALTH_KEY)
        if raw:
            sentinel_health = json.loads(raw)

        for asset_class in ("crypto", "fx"):
            last_ok = await redis.get(f"ai:sentinel:last_ok:{asset_class}")
            if last_ok:
                last_ok_str = last_ok if isinstance(last_ok, str) else last_ok.decode()
                last_dt = datetime.fromisoformat(last_ok_str.replace("Z", "+00:00"))
                age_s = int((datetime.now(timezone.utc) - last_dt).total_seconds())
                sentiment_ages[asset_class] = f"{age_s}s ago"
            else:
                sentiment_ages[asset_class] = None
    except Exception:
        pass

    return {
        "ai_configured": settings.ai_configured,
        "scoring_model": settings.AI_SCORING_MODEL if settings.ai_configured else None,
        "timeout_ms": settings.AI_TIMEOUT_MS,
        "streams": {
            "opportunities_length": opportunities_len,
            "approved_length": approved_len,
            "pending_unacked": pending_count,
        },
        "news_sentinel": {
            "enabled": settings.NEWS_FETCH_ENABLED,
            "interval_seconds": settings.NEWS_FETCH_INTERVAL_SECONDS,
            "last_cycle": sentinel_health,
            "sentiment_last_ok": sentiment_ages,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
