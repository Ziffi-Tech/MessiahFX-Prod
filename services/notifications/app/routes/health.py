"""Health endpoints for notifications service."""

from datetime import datetime, timezone

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from mezna_shared.redis_client import RedisKeys
from ..config import settings

router = APIRouter()


@router.get("/live")
async def liveness() -> dict:
    return {
        "status": "ok",
        "service": settings.SERVICE_NAME,
        "version": settings.VERSION,
        "channels": {
            "telegram": settings.TELEGRAM_ENABLED,
            "discord": settings.DISCORD_ENABLED,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
async def readiness(request: Request) -> JSONResponse:
    redis_ok = True
    try:
        await request.app.state.redis.ping()
    except Exception:
        redis_ok = False

    consumer_task = getattr(request.app.state, "consumer_task", None)
    consumer_ok = consumer_task is not None and not consumer_task.done()

    try:
        queue_depth = await request.app.state.redis.llen(RedisKeys.NOTIFICATION_QUEUE)
    except Exception:
        queue_depth = -1

    all_ok = redis_ok
    return JSONResponse(
        status_code=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ok" if all_ok else "degraded",
            "service": settings.SERVICE_NAME,
            "version": settings.VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channels": {
                "telegram": {
                    "enabled": settings.TELEGRAM_ENABLED,
                    "configured": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID),
                },
                "discord": {
                    "enabled": settings.DISCORD_ENABLED,
                    "configured": bool(settings.DISCORD_WEBHOOK_URL),
                },
            },
            "queue": {
                "depth": queue_depth,
                "max_len": settings.NOTIFICATION_QUEUE_MAX_LEN,
            },
            "dependencies": {
                "redis": "ok" if redis_ok else "unreachable",
                "consumer_loop": "running" if consumer_ok else "stopped",
            },
        },
    )
