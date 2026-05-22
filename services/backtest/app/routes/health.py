"""Health endpoints for backtest service."""

from datetime import datetime, timezone
from fastapi import APIRouter
from ..config import settings

router = APIRouter()


@router.get("/live")
async def liveness() -> dict:
    return {
        "status": "ok",
        "service": settings.SERVICE_NAME,
        "version": settings.VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_source": "Binance public API (no auth required)",
    }


@router.get("/ready")
async def readiness() -> dict:
    """Backtest service has no external dependencies at startup."""
    return {
        "status": "ok",
        "service": settings.SERVICE_NAME,
        "version": settings.VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "limits": {
            "max_candles": settings.MAX_TOTAL_CANDLES,
            "max_days": 365,
        },
    }
