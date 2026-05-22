"""
Reconciliation background task.

Runs every RECONCILIATION_INTERVAL_SECONDS (default 60).

Responsibilities:
  1. Stale-trade cleanup
       Find trades stuck in 'pending' or 'open' for longer than
       RECONCILIATION_STALE_MINUTES (default 5). Mark them 'error'
       with rejection_reason='reconciliation_timeout'. This handles
       edge cases where the executor crashed after submitting but
       before receiving a fill confirmation.

  2. Open position count correction
       Recompute the true open position count from the database
       (count of distinct opportunity_ids still linked to non-terminal
       trades) and write the corrected value to Redis risk:state.
       This acts as a safety net against any drift caused by
       crashes, bugs, or missed decrements.

Why a polling reconciler rather than event-driven?
  Trading systems always need a reconciliation sweep as the final
  safety net. Event-driven approaches fail silently when events are
  missed (crashes, network partitions). This polling approach is the
  authoritative correction mechanism.

CRITICAL: This task only corrects state — it never submits orders.
"""

import asyncio

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from mezna_shared.redis_client import RedisKeys
from . import queries
from .config import Settings

log = structlog.get_logger()


async def _run_cycle(db_engine: AsyncEngine, redis: Redis, settings: Settings) -> None:
    """Execute one reconciliation cycle."""

    # ── 1. Stale trade cleanup ────────────────────────────────────────────────
    stale = await queries.find_stale_trades(db_engine, settings.RECONCILIATION_STALE_MINUTES)

    if stale:
        log.warning(
            "reconciler.stale_trades_found",
            count=len(stale),
            stale_threshold_minutes=settings.RECONCILIATION_STALE_MINUTES,
        )
        for trade in stale:
            try:
                await queries.mark_trade_error(
                    db_engine,
                    trade["client_order_id"],
                    reason="reconciliation_timeout",
                )
                log.warning(
                    "reconciler.stale_trade_closed",
                    client_order_id=trade["client_order_id"],
                    symbol=trade.get("symbol"),
                    venue=trade.get("venue"),
                    opened_at=trade.get("opened_at"),
                )
            except Exception as exc:
                log.error(
                    "reconciler.mark_error_failed",
                    client_order_id=trade["client_order_id"],
                    error=str(exc),
                )

    # ── 2. Open position count correction ─────────────────────────────────────
    try:
        true_open = await queries.count_open_positions(db_engine)
        await redis.hset(RedisKeys.RISK_STATE, "open_position_count", str(true_open))
        log.info(
            "reconciler.positions_corrected",
            open_position_count=true_open,
            stale_closed=len(stale),
        )
    except Exception as exc:
        log.error("reconciler.position_count_failed", error=str(exc))


async def run(
    settings: Settings,
    db_engine: AsyncEngine,
    redis: Redis,
) -> None:
    """
    Reconciliation loop — runs forever until cancelled.

    Sleeps for RECONCILIATION_INTERVAL_SECONDS between cycles.
    Errors in a single cycle are logged but do NOT stop the loop.
    """
    log.info(
        "reconciler.started",
        interval_seconds=settings.RECONCILIATION_INTERVAL_SECONDS,
        stale_minutes=settings.RECONCILIATION_STALE_MINUTES,
    )

    while True:
        try:
            await asyncio.sleep(settings.RECONCILIATION_INTERVAL_SECONDS)
            await _run_cycle(db_engine, redis, settings)
        except asyncio.CancelledError:
            log.info("reconciler.cancelled")
            break
        except Exception as exc:
            log.error("reconciler.cycle_error", error=str(exc))
            # Continue — reconciler must be resilient to transient errors
