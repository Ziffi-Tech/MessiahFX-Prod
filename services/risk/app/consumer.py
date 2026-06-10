"""
Risk engine stream consumer.

Reads from signals:approved (written by ai-filter), runs hard risk checks,
then routes each signal to one of two destinations:

  APPROVED  →  signals:execution_queue  (executor reads this)
  REJECTED  →  signals:rejected         (audit trail only)

Consumer group: "risk" on signals:approved stream.
Consumer name:  "risk-1" (single worker — risk checks must be serialised).

Serialisation matters:
  Risk checks read and write shared state (open_position_count, etc.).
  Running two checks in parallel against the same state could approve
  more positions than the limit allows. Single consumer = no race conditions.

Daily reset:
  Before processing each message, the consumer checks if the trading day
  has rolled over (UTC midnight). If so, it resets daily counters first.
  This is the simplest approach — no separate cron job needed.
"""

import asyncio
import json
from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy.ext.asyncio import AsyncEngine

from mezna_shared.redis_client import RedisKeys, StreamNames
from mezna_shared.opportunities import upsert_opportunity


async def _notify(redis: Redis, event: str, **kwargs) -> None:
    """Push a risk notification to the notifications queue (fire-and-forget)."""
    try:
        payload = json.dumps({
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        })
        await redis.rpush(RedisKeys.NOTIFICATION_QUEUE, payload)
    except Exception:
        pass

from .checker import run_checks
from .config import Settings
from . import state

log = structlog.get_logger()

_GROUP = "risk"
_CONSUMER = "risk-1"
_BLOCK_MS = 100
_STREAM_MAXLEN = 1000


async def _ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(
            RedisKeys.SIGNALS_APPROVED,
            _GROUP,
            id="$",        # Only new signals — stale ones must not be executed
            mkstream=True,
        )
        log.info("consumer.group_created", group=_GROUP)
    except ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            log.debug("consumer.group_exists", group=_GROUP)
        else:
            raise


async def _process(
    msg_id: str,
    fields: dict,
    redis: Redis,
    db_engine: AsyncEngine,
    settings: Settings,
) -> None:
    """
    Run risk checks on one opportunity and route it accordingly.
    Never raises — all errors are caught and logged.
    """
    # ── Deserialise ────────────────────────────────────────────────────────────
    raw_payload = fields.get(StreamNames.PAYLOAD, "{}")
    try:
        opportunity = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError):
        log.warning("consumer.bad_payload", msg_id=msg_id)
        opportunity = {}

    strategy_type = opportunity.get("strategy_type", fields.get(StreamNames.STRATEGY_TYPE, ""))
    symbol = opportunity.get("symbol_primary", fields.get(StreamNames.SYMBOL_PRIMARY, ""))
    opportunity_id = fields.get(StreamNames.OPPORTUNITY_ID)

    # ── Daily reset check ──────────────────────────────────────────────────────
    await state.check_and_reset_daily(redis)

    # ── Read current state ─────────────────────────────────────────────────────
    risk_hash = await state.get_risk_hash(redis)
    halt_flag = await redis.get(RedisKeys.HALT) or "0"
    strategy_state = await redis.hgetall(RedisKeys.strategy_state(strategy_type))
    on_cooldown = bool(await redis.exists(RedisKeys.cooldown(strategy_type)))

    # ── Capital-control exposure (Phase 4) — query only when caps are configured ─
    gross_exposure = 0.0
    strategy_exposure = 0.0
    if settings.exposure_caps_enabled:
        gross_exposure, by_strategy = await state.get_open_exposure(db_engine, settings.is_paper)
        strategy_exposure = by_strategy.get(strategy_type, 0.0)

    # ── Run checks ─────────────────────────────────────────────────────────────
    result = run_checks(
        risk_hash=risk_hash,
        opportunity=opportunity,
        halt_flag=halt_flag,
        strategy_state=strategy_state,
        on_cooldown=on_cooldown,
        settings=settings,
        gross_exposure_usd=gross_exposure,
        strategy_exposure_usd=strategy_exposure,
        new_notional_usd=settings.position_usd,
    )

    checked_at = datetime.now(timezone.utc).isoformat()

    if result.approved:
        # ── Approved path ──────────────────────────────────────────────────────
        new_position_count = await state.increment_open_positions(redis)
        await state.increment_strategy_signal_count(redis, strategy_type)

        enriched = {
            **opportunity,
            "risk_approved": True,
            "risk_checks_passed": result.checks_passed,
            "risk_checked_at": checked_at,
            "open_position_count_after": new_position_count,
        }

        await redis.xadd(
            RedisKeys.SIGNALS_EXECUTION_QUEUE,
            {
                **fields,
                StreamNames.PAYLOAD: json.dumps(enriched),
                "risk_approved": "true",
                "risk_checked_at": checked_at,
            },
            maxlen=_STREAM_MAXLEN,
            approximate=True,
        )

        log.info(
            "risk.approved",
            strategy=strategy_type,
            symbol=symbol,
            net_edge_bps=opportunity.get("net_edge_bps"),
            checks_passed=result.checks_passed,
            open_positions=new_position_count,
        )

        await state.write_audit_log(
            db_engine,
            event_type="risk.approved",
            payload={
                "strategy_type": strategy_type,
                "symbol_primary": symbol,
                "net_edge_bps": opportunity.get("net_edge_bps"),
                "checks_passed": result.checks_passed,
                "ai_score": opportunity.get("ai_score"),
            },
        )

    else:
        # ── Rejected path ──────────────────────────────────────────────────────
        enriched = {
            **opportunity,
            "risk_approved": False,
            "risk_rejection_reason": result.rejection_reason,
            "risk_checks_passed": result.checks_passed,
            "risk_checks_failed": result.checks_failed,
            "risk_checked_at": checked_at,
        }

        await redis.xadd(
            RedisKeys.SIGNALS_REJECTED,
            {
                **fields,
                StreamNames.PAYLOAD: json.dumps(enriched),
                "risk_approved": "false",
                "risk_rejection_reason": result.rejection_reason or "",
                "risk_checked_at": checked_at,
            },
            maxlen=_STREAM_MAXLEN,
            approximate=True,
        )

        log.info(
            "risk.rejected",
            strategy=strategy_type,
            symbol=symbol,
            reason=result.rejection_reason,
            checks_failed=result.checks_failed,
        )

        await state.write_audit_log(
            db_engine,
            event_type="risk.rejected",
            payload={
                "strategy_type": strategy_type,
                "symbol_primary": symbol,
                "rejection_reason": result.rejection_reason,
                "checks_failed": result.checks_failed,
            },
        )

        # ── Side effects ───────────────────────────────────────────────────────
        if result.auto_halt:
            await state.activate_halt(
                redis,
                reason=result.rejection_reason or "auto_halt",
                db_engine=db_engine,
            )
            await _notify(
                redis, "risk.halt",
                reason=result.rejection_reason,
                strategy_type=strategy_type,
            )

        if result.trigger_cooldown and strategy_type:
            await state.activate_cooldown(
                redis,
                strategy_type=strategy_type,
                cooldown_minutes=settings.RISK_COOLDOWN_MINUTES,
            )
            await _notify(
                redis, "risk.cooldown",
                strategy_type=strategy_type,
                reason=result.rejection_reason,
            )

    # Best-effort persist the opportunity (detected + AI + risk) so the journal
    # funnel/history is populated. Both branches above set `enriched`.
    await upsert_opportunity(db_engine, opportunity_id, enriched)


async def run(settings: Settings, redis: Redis, db_engine: AsyncEngine) -> None:
    """
    Main consumer loop. Runs until cancelled.
    Single-consumer design: risk checks must not run in parallel.
    """
    await _ensure_group(redis)
    log.info("consumer.started", group=_GROUP, consumer=_CONSUMER)

    while True:
        try:
            response = await redis.xreadgroup(
                groupname=_GROUP,
                consumername=_CONSUMER,
                streams={RedisKeys.SIGNALS_APPROVED: ">"},
                count=1,
                block=_BLOCK_MS,
            )

            if not response:
                continue

            for _stream, entries in response:
                for msg_id, fields in entries:
                    try:
                        await _process(msg_id, fields, redis, db_engine, settings)
                    except Exception as exc:
                        log.error("consumer.process_error", msg_id=msg_id, error=str(exc))
                    finally:
                        await redis.xack(RedisKeys.SIGNALS_APPROVED, _GROUP, msg_id)

        except asyncio.CancelledError:
            log.info("consumer.cancelled")
            raise

        except Exception as exc:
            log.error("consumer.loop_error", error=str(exc))
            await asyncio.sleep(1.0)
