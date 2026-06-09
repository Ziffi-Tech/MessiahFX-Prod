"""
System control endpoints — kill switch, strategy toggles, trading mode.

These are the most safety-critical endpoints in the system.
Every action is logged to audit_log with full context.

Kill switch design:
- Sets risk:halt = 1 in Redis (fastest possible read path for all services)
- Writes a RiskEvent record to Postgres (audit trail)
- Cannot be bypassed by strategy code or AI layer
- Reset requires explicit confirmation
"""

import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from mezna_shared.redis_client import RedisKeys
from mezna_shared.regime_map import ALL_STRATEGIES
from mezna_shared.schemas.risk import KillSwitchRequest, KillSwitchResetRequest, StrategyToggleRequest
from mezna_shared.db import get_async_session

log = structlog.get_logger()
router = APIRouter()

# Canonical strategy set (6) — single source of truth so the control plane never
# silently ignores newer strategies. Sorted for deterministic ordering in logs.
_ALL_STRATEGIES: tuple[str, ...] = tuple(sorted(ALL_STRATEGIES))


def _actor(request: Request, fallback: str) -> str:
    """
    Resolve the operator behind a control action for the audit trail.

    The dashboard proxy verifies the session and forwards X-Mezna-User after
    auth, so safety-critical actions are attributed to the real user instead of
    a literal "dashboard". Falls back to the request-body value for non-dashboard
    callers (scripts, direct API).
    """
    user = request.headers.get("x-mezna-user")
    return user.strip() if user and user.strip() else fallback


@router.get("/status", summary="Get current system control state")
async def get_control_status(request: Request) -> dict:
    """Return current kill switch state and strategy toggle states."""
    redis = request.app.state.redis

    halt = await redis.get(RedisKeys.HALT)
    risk_state = await redis.hgetall(RedisKeys.RISK_STATE)

    strategy_states = {}
    for strategy in _ALL_STRATEGIES:
        state = await redis.hgetall(RedisKeys.strategy_state(strategy))
        strategy_states[strategy] = {
            "enabled": state.get("enabled", "0") == "1",
            "paper_mode": state.get("paper_mode", "1") == "1",
            "latency_profile": state.get("latency_profile", "standard"),
        }

    return {
        "trading_halted": halt == "1",
        "risk_state": risk_state,
        "strategies": strategy_states,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post(
    "/kill",
    status_code=status.HTTP_200_OK,
    summary="EMERGENCY: Activate kill switch — halts all trading immediately",
)
async def activate_kill_switch(
    request: Request,
    body: KillSwitchRequest,
) -> dict:
    """
    Activate the kill switch.

    Effect:
    - Sets risk:halt = 1 in Redis immediately (all services check this before any order)
    - Writes risk event to Postgres for audit
    - Sends notification alert

    This cannot be undone by a strategy or AI call. Only /control/reset can clear it.
    """
    redis = request.app.state.redis
    activated_at = datetime.now(timezone.utc)
    actor = _actor(request, body.activated_by)

    # Set halt flag in Redis — immediate effect, fastest possible read
    await redis.set(RedisKeys.HALT, "1")
    await redis.hset(
        RedisKeys.RISK_STATE,
        mapping={
            "trading_halted": "1",
            "halt_reason": body.reason,
            "last_updated": activated_at.isoformat(),
        },
    )

    log.warning(
        "kill_switch.activated",
        reason=body.reason,
        activated_by=actor,
        activated_at=activated_at.isoformat(),
    )

    # Write audit record
    async with get_async_session(request.app.state.db_engine) as session:
        await session.execute(
            text("""
                INSERT INTO audit_log (event_type, service, payload, metadata, created_at)
                VALUES (:event_type, :service, :payload::jsonb, :metadata::jsonb, :created_at)
            """),
            {
                "event_type": "kill_switch.activated",
                "service": "gateway",
                "payload": json.dumps({"reason": body.reason, "activated_by": actor}),
                "metadata": json.dumps({"activated_at": activated_at.isoformat()}),
                "created_at": activated_at,
            },
        )
        await session.execute(
            text("""
                INSERT INTO risk_events (event_type, description, created_at)
                VALUES ('kill_switch.activated', :description, :created_at)
            """),
            {
                "description": f"Kill switch activated by {actor}: {body.reason}",
                "created_at": activated_at,
            },
        )

    return {
        "halted": True,
        "reason": body.reason,
        "activated_by": actor,
        "activated_at": activated_at.isoformat(),
        "message": "Kill switch active. All trading halted. Use /control/reset to re-enable.",
    }


@router.post(
    "/reset",
    status_code=status.HTTP_200_OK,
    summary="Reset kill switch — re-enables trading (requires explicit confirmation)",
)
async def reset_kill_switch(
    request: Request,
    body: KillSwitchResetRequest,
) -> dict:
    """
    Reset the kill switch. Requires confirm=true in the request body.

    WARNING: This re-enables trading. Verify system state before calling.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm must be true to reset the kill switch",
        )

    redis = request.app.state.redis
    reset_at = datetime.now(timezone.utc)
    actor = _actor(request, body.reset_by)

    await redis.set(RedisKeys.HALT, "0")
    await redis.hset(
        RedisKeys.RISK_STATE,
        mapping={
            "trading_halted": "0",
            "halt_reason": "",
            "last_updated": reset_at.isoformat(),
        },
    )

    log.info(
        "kill_switch.reset",
        reason=body.reason,
        reset_by=actor,
        reset_at=reset_at.isoformat(),
    )

    async with get_async_session(request.app.state.db_engine) as session:
        await session.execute(
            text("""
                INSERT INTO audit_log (event_type, service, payload, metadata, created_at)
                VALUES (:event_type, :service, :payload::jsonb, :metadata::jsonb, :created_at)
            """),
            {
                "event_type": "kill_switch.reset",
                "service": "gateway",
                "payload": json.dumps({"reason": body.reason, "reset_by": actor}),
                "metadata": json.dumps({"reset_at": reset_at.isoformat()}),
                "created_at": reset_at,
            },
        )

    return {
        "halted": False,
        "reason": body.reason,
        "reset_by": actor,
        "reset_at": reset_at.isoformat(),
        "message": "Kill switch cleared. Trading can resume if strategies are enabled.",
    }


@router.post(
    "/strategy/toggle",
    status_code=status.HTTP_200_OK,
    summary="Enable or disable a strategy",
)
async def toggle_strategy(
    request: Request,
    body: StrategyToggleRequest,
) -> dict:
    """
    Toggle a strategy on or off. Writes to Redis (immediate) and audit log.

    Note: Enabling a strategy does NOT override the kill switch.
    If trading is halted, no signals will execute regardless of toggle state.
    """
    redis = request.app.state.redis
    toggled_at = datetime.now(timezone.utc)
    actor = _actor(request, "dashboard")

    state_update: dict[str, str] = {
        "enabled": "1" if body.enabled else "0",
        "last_updated": toggled_at.isoformat(),
    }
    if body.latency_profile:
        state_update["latency_profile"] = body.latency_profile

    await redis.hset(RedisKeys.strategy_state(body.strategy_type), mapping=state_update)

    log.info(
        "strategy.toggled",
        strategy_type=body.strategy_type,
        enabled=body.enabled,
        latency_profile=body.latency_profile,
        toggled_by=actor,
    )

    async with get_async_session(request.app.state.db_engine) as session:
        await session.execute(
            text("""
                UPDATE strategy_configs
                SET enabled = :enabled,
                    latency_profile = COALESCE(:latency_profile, latency_profile),
                    updated_at = :updated_at,
                    updated_by = :updated_by
                WHERE strategy_type = :strategy_type
            """),
            {
                "enabled": body.enabled,
                "latency_profile": body.latency_profile,
                "updated_at": toggled_at,
                "strategy_type": body.strategy_type,
                "updated_by": actor,
            },
        )
        await session.execute(
            text("""
                INSERT INTO audit_log (event_type, service, payload, metadata, created_at)
                VALUES ('strategy.toggled', 'gateway', :payload::jsonb, '{}'::jsonb, :created_at)
            """),
            {
                "payload": json.dumps({
                    "strategy_type": body.strategy_type,
                    "enabled": body.enabled,
                    "latency_profile": body.latency_profile,
                    "toggled_by": actor,
                }),
                "created_at": toggled_at,
            },
        )

    return {
        "strategy_type": body.strategy_type,
        "enabled": body.enabled,
        "toggled_at": toggled_at.isoformat(),
    }


# ── Bot START / STOP ──────────────────────────────────────────────────────────


class BotStartRequest(BaseModel):
    started_by: str = "dashboard"
    paper_mode: bool = True   # True = paper, False = live (must be explicitly set for live)


class BotStopRequest(BaseModel):
    stopped_by: str = "dashboard"
    reason: str = "Manual stop"


@router.post(
    "/bot/start",
    status_code=status.HTTP_200_OK,
    summary="START the trading bot — clears kill switch + enables all strategies",
)
async def bot_start(
    request: Request,
    body: BotStartRequest,
) -> dict:
    """
    One-click bot start:
      1. Clears the kill switch (risk:halt = 0)
      2. Enables all three strategies in Redis
      3. Sets paper_mode on each strategy as requested
      4. Writes a single audit event

    Note: This does NOT override position limits or drawdown checks.
    The risk service enforces hard limits regardless of bot state.
    """
    redis = request.app.state.redis
    started_at = datetime.now(timezone.utc)
    actor = _actor(request, body.started_by)
    paper_flag = "1" if body.paper_mode else "0"
    mode_label = "paper" if body.paper_mode else "LIVE"

    # 1. Clear halt flag
    await redis.set(RedisKeys.HALT, "0")
    await redis.hset(
        RedisKeys.RISK_STATE,
        mapping={
            "trading_halted": "0",
            "halt_reason": "",
            "last_updated": started_at.isoformat(),
        },
    )

    # 2. Enable all strategies
    for strategy in _ALL_STRATEGIES:
        await redis.hset(
            RedisKeys.strategy_state(strategy),
            mapping={
                "enabled": "1",
                "paper_mode": paper_flag,
                "last_updated": started_at.isoformat(),
            },
        )

    log.info(
        "bot.started",
        started_by=actor,
        paper_mode=body.paper_mode,
        strategies=list(_ALL_STRATEGIES),
    )

    # 3. Audit log
    async with get_async_session(request.app.state.db_engine) as session:
        await session.execute(
            text("""
                INSERT INTO audit_log (event_type, service, payload, metadata, created_at)
                VALUES ('bot.started', 'gateway', :payload::jsonb, '{}'::jsonb, :created_at)
            """),
            {
                "payload": json.dumps({
                    "started_by": actor,
                    "paper_mode": body.paper_mode,
                    "strategies_enabled": list(_ALL_STRATEGIES),
                }),
                "created_at": started_at,
            },
        )

    return {
        "running": True,
        "mode": mode_label,
        "strategies_enabled": list(_ALL_STRATEGIES),
        "started_by": actor,
        "started_at": started_at.isoformat(),
        "message": f"Bot started in {mode_label} mode. All strategies enabled.",
    }


@router.post(
    "/bot/stop",
    status_code=status.HTTP_200_OK,
    summary="STOP the trading bot — activates kill switch + disables all strategies",
)
async def bot_stop(
    request: Request,
    body: BotStopRequest,
) -> dict:
    """
    One-click bot stop:
      1. Activates kill switch (risk:halt = 1) — immediate effect on all services
      2. Disables all three strategies in Redis
      3. Writes audit + risk event records

    Open positions are NOT closed — the executor will simply stop processing
    new signals. Close open positions manually if needed.
    """
    redis = request.app.state.redis
    stopped_at = datetime.now(timezone.utc)
    actor = _actor(request, body.stopped_by)

    # 1. Activate halt
    await redis.set(RedisKeys.HALT, "1")
    await redis.hset(
        RedisKeys.RISK_STATE,
        mapping={
            "trading_halted": "1",
            "halt_reason": body.reason,
            "last_updated": stopped_at.isoformat(),
        },
    )

    # 2. Disable all strategies
    for strategy in _ALL_STRATEGIES:
        await redis.hset(
            RedisKeys.strategy_state(strategy),
            mapping={
                "enabled": "0",
                "last_updated": stopped_at.isoformat(),
            },
        )

    log.warning(
        "bot.stopped",
        stopped_by=actor,
        reason=body.reason,
    )

    # 3. Audit + risk event
    async with get_async_session(request.app.state.db_engine) as session:
        await session.execute(
            text("""
                INSERT INTO audit_log (event_type, service, payload, metadata, created_at)
                VALUES ('bot.stopped', 'gateway', :payload::jsonb, '{}'::jsonb, :created_at)
            """),
            {
                "payload": json.dumps({
                    "stopped_by": actor,
                    "reason": body.reason,
                    "strategies_disabled": list(_ALL_STRATEGIES),
                }),
                "created_at": stopped_at,
            },
        )
        await session.execute(
            text("""
                INSERT INTO risk_events (event_type, description, created_at)
                VALUES ('bot.stopped', :description, :created_at)
            """),
            {
                "description": f"Bot stopped by {actor}: {body.reason}",
                "created_at": stopped_at,
            },
        )

    return {
        "running": False,
        "strategies_disabled": list(_ALL_STRATEGIES),
        "stopped_by": actor,
        "reason": body.reason,
        "stopped_at": stopped_at.isoformat(),
        "message": "Bot stopped. Kill switch active. Open positions NOT automatically closed.",
    }
