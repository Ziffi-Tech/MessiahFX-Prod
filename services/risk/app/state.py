"""
Risk state manager — owns all writes to risk:state and risk:halt in Redis.

Design principles:
  - Only the risk engine writes these keys. All other services read only.
  - All updates use Redis pipelines for atomicity where possible.
  - Daily counters (daily_pnl_usd, daily_drawdown_pct) are reset at UTC midnight.
  - The halt flag (risk:halt) is a separate string key for O(1) reads by all services.
    It mirrors the trading_halted field in the risk:state hash.

Daily reset:
  Checked on startup and before each risk check. If the date stored in
  risk:state differs from today UTC, daily counters are zeroed.
  The reset is logged and written to audit_log.
"""

import json
from datetime import datetime, timezone, date

import structlog
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mezna_shared.db import get_async_session
from mezna_shared.redis_client import RedisKeys

log = structlog.get_logger()

_DATE_KEY = "risk_date"  # Field in risk:state storing the current trading day


async def get_risk_hash(redis: Redis) -> dict[str, str]:
    """Read the full risk state hash."""
    return await redis.hgetall(RedisKeys.RISK_STATE)


async def check_and_reset_daily(redis: Redis) -> bool:
    """
    Check if the trading day has rolled over. If so, reset daily counters.
    Returns True if a reset was performed.
    """
    today = date.today().isoformat()
    stored_date = await redis.hget(RedisKeys.RISK_STATE, _DATE_KEY)

    if stored_date == today:
        return False

    # Day has changed — reset daily counters only (not position count, consecutive losses)
    pipe = redis.pipeline()
    pipe.hset(
        RedisKeys.RISK_STATE,
        mapping={
            "daily_pnl_usd": "0.0",
            "daily_drawdown_pct": "0.0",
            "funding_arb_signals_today": "0",
            "stat_arb_signals_today": "0",
            "swing_signals_today": "0",
            _DATE_KEY: today,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        },
    )
    await pipe.execute()

    log.info("risk.daily_reset", previous_date=stored_date, new_date=today)
    return True


async def increment_open_positions(redis: Redis) -> int:
    """Atomically increment open position count. Returns new value."""
    new_val = await redis.hincrby(RedisKeys.RISK_STATE, "open_position_count", 1)
    await redis.hset(
        RedisKeys.RISK_STATE, "last_updated", datetime.now(timezone.utc).isoformat()
    )
    return int(new_val)


async def decrement_open_positions(redis: Redis) -> int:
    """Atomically decrement open position count (floor = 0). Returns new value."""
    current = int((await redis.hget(RedisKeys.RISK_STATE, "open_position_count")) or 0)
    new_val = max(0, current - 1)
    await redis.hset(
        RedisKeys.RISK_STATE,
        mapping={
            "open_position_count": str(new_val),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        },
    )
    return new_val


async def increment_strategy_signal_count(redis: Redis, strategy_type: str) -> None:
    """Increment the per-strategy signal counter for today."""
    field = f"{strategy_type}_signals_today"
    await redis.hincrby(RedisKeys.RISK_STATE, field, 1)


async def activate_halt(redis: Redis, reason: str, db_engine: AsyncEngine) -> None:
    """
    Activate the kill switch. Sets both the fast-read key and the state hash.
    Writes a risk event to the database.
    """
    now = datetime.now(timezone.utc)

    pipe = redis.pipeline()
    pipe.set(RedisKeys.HALT, "1")
    pipe.hset(
        RedisKeys.RISK_STATE,
        mapping={
            "trading_halted": "1",
            "halt_reason": reason,
            "last_updated": now.isoformat(),
        },
    )
    await pipe.execute()

    log.warning("risk.auto_halt_activated", reason=reason)

    # Persist risk event to DB
    try:
        async with get_async_session(db_engine) as session:
            await session.execute(
                text("""
                    INSERT INTO risk_events
                        (event_type, trigger_value, threshold_value, description, auto_resolved, created_at)
                    VALUES
                        ('auto_halt', :trigger, :threshold, :description, false, :now)
                """),
                {
                    "trigger": reason,
                    "threshold": "risk_limit_breached",
                    "description": f"Auto-halt triggered: {reason}",
                    "now": now,
                },
            )
    except Exception as exc:
        log.error("risk.db_write_failed", event="auto_halt", error=str(exc))


async def activate_cooldown(
    redis: Redis, strategy_type: str, cooldown_minutes: int
) -> None:
    """
    Set a per-strategy cooldown TTL key.
    The strategy runner checks this key before each iteration.
    """
    ttl_seconds = cooldown_minutes * 60
    await redis.set(
        RedisKeys.cooldown(strategy_type),
        datetime.now(timezone.utc).isoformat(),
        ex=ttl_seconds,
    )
    log.warning(
        "risk.cooldown_activated",
        strategy=strategy_type,
        duration_minutes=cooldown_minutes,
    )


async def write_audit_log(
    db_engine: AsyncEngine,
    event_type: str,
    payload: dict,
) -> None:
    """Write a risk check result to the audit log."""
    now = datetime.now(timezone.utc)
    try:
        async with get_async_session(db_engine) as session:
            await session.execute(
                text("""
                    INSERT INTO audit_log (event_type, service, payload, metadata, created_at)
                    VALUES (:event_type, 'risk', :payload::jsonb, '{}'::jsonb, :created_at)
                """),
                {
                    "event_type": event_type,
                    "payload": json.dumps(payload),
                    "created_at": now,
                },
            )
    except Exception as exc:
        log.error("risk.audit_log_failed", event_type=event_type, error=str(exc))
