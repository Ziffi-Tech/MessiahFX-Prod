"""
Health endpoints for the risk engine.

  GET /health/live   — Liveness: always 200 while the process is up.
  GET /health/ready  — Readiness: DB + Redis + halt flag.
  GET /health/state  — Full live risk state snapshot (dashboard reads this).
"""

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from mezna_shared.db import check_db_connection
from mezna_shared.redis_client import RedisKeys
from mezna_shared.schemas.risk import RiskState
from ..config import settings

log = structlog.get_logger()
router = APIRouter()

_ALL_STRATEGIES = ("funding_arb", "stat_arb", "swing")


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
    halt = None

    try:
        await request.app.state.redis.ping()
        halt = await request.app.state.redis.get(RedisKeys.HALT)
    except Exception:
        redis_ok = False

    all_ok = db_ok and redis_ok
    return JSONResponse(
        status_code=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ok" if all_ok else "degraded",
            "service": settings.SERVICE_NAME,
            "version": settings.VERSION,
            "trading_halted": halt == "1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dependencies": {
                "database": "ok" if db_ok else "unreachable",
                "redis": "ok" if redis_ok else "unreachable",
            },
        },
    )


@router.get("/state")
async def risk_state(request: Request) -> dict:
    """
    Full live risk state snapshot.
    Includes per-strategy cooldown status and stream queue depths.
    Used by the dashboard Overview tab.
    """
    redis = request.app.state.redis

    raw = await redis.hgetall(RedisKeys.RISK_STATE)
    halt = await redis.get(RedisKeys.HALT) or "0"

    # Per-strategy cooldown status
    cooldowns: dict[str, bool] = {}
    for name in _ALL_STRATEGIES:
        cooldowns[name] = bool(await redis.exists(RedisKeys.cooldown(name)))

    # Stream depths
    try:
        approved_len = await redis.xlen(RedisKeys.SIGNALS_APPROVED)
        execution_len = await redis.xlen(RedisKeys.SIGNALS_EXECUTION_QUEUE)
        rejected_len = await redis.xlen(RedisKeys.SIGNALS_REJECTED)
    except Exception:
        approved_len = execution_len = rejected_len = None

    # Limits snapshot (from settings — for dashboard display)
    limits = {
        "max_per_trade_pct": settings.RISK_MAX_PER_TRADE_PCT,
        "max_daily_drawdown_pct": settings.RISK_MAX_DAILY_DRAWDOWN_PCT,
        "max_open_positions": settings.RISK_MAX_OPEN_POSITIONS,
        "max_consecutive_losses": settings.RISK_MAX_CONSECUTIVE_LOSSES,
        "cooldown_minutes": settings.RISK_COOLDOWN_MINUTES,
        "paper_capital_usd": settings.PAPER_CAPITAL_USD,
    }

    return {
        "trading_halted": halt == "1",
        "halt_reason": raw.get("halt_reason") or None,
        "risk_state": {
            "daily_pnl_usd": float(raw.get("daily_pnl_usd", 0)),
            "daily_drawdown_pct": float(raw.get("daily_drawdown_pct", 0)),
            "open_position_count": int(raw.get("open_position_count", 0)),
            "consecutive_losses": int(raw.get("consecutive_losses", 0)),
            "funding_arb_signals_today": int(raw.get("funding_arb_signals_today", 0)),
            "stat_arb_signals_today": int(raw.get("stat_arb_signals_today", 0)),
            "swing_signals_today": int(raw.get("swing_signals_today", 0)),
            "last_updated": raw.get("last_updated"),
        },
        "cooldowns": cooldowns,
        "limits": limits,
        "streams": {
            "approved_pending": approved_len,
            "execution_queue": execution_len,
            "rejected_today": rejected_len,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
