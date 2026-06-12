"""
Redis Stream consumer — reads opportunities, scores them, forwards to risk engine.

Stream topology:
  signals:opportunities  →  [ai-filter consumer group]  →  signals:approved

Consumer group design:
  - Group "ai-filter" is created with id="$" on first start.
    id="$" means: only process NEW signals from this point forward.
    Rationale: trading signals are time-sensitive. A signal from 5 minutes ago
    is stale and should never be executed — it's safer to drop it than to act on
    outdated market conditions after a service restart.

  - Messages are acknowledged AFTER publishing to signals:approved (not before).
    If the service crashes between read and ack, the message stays pending and
    will be visible to XAUTOCLAIM on the next restart. We intentionally skip
    PEL recovery for Phase 3 — stale signals should not be executed.

  - Always ACK in the finally block — even if scoring or publishing fails.
    A permanent stuck message in the PEL is worse than occasionally skipping one.

Advisory-only guarantee:
  The signal is ALWAYS forwarded to signals:approved, regardless of AI score.
  Even a score of 0 does not block the trade — that is the risk engine's job.
  ai_timeout=True means the risk engine acts without AI context.
"""

import asyncio
import json
import socket
from datetime import datetime, timezone
from typing import Optional

import anthropic
import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from mezna_shared.redis_client import RedisKeys, StreamNames
from .config import Settings
from .scorer import score_opportunity

log = structlog.get_logger()

_GROUP = "ai-filter"
# Hostname-unique: scoring is stateless per message, so ai-filter CAN run as
# multiple replicas — the consumer group splits the stream between them. A fixed
# name would make replicas share one consumer identity (and one PEL).
_CONSUMER = f"ai-filter-{socket.gethostname()}"
_BLOCK_MS = 100          # Wait up to 100 ms for new messages before looping
_STREAM_MAXLEN = 1000    # Rolling window for signals:approved


async def _ensure_group(redis: Redis) -> None:
    """Create the consumer group if it doesn't already exist."""
    try:
        await redis.xgroup_create(
            RedisKeys.SIGNALS_OPPORTUNITIES,
            _GROUP,
            id="$",        # New signals only — never replay stale signals
            mkstream=True, # Create stream if market-data hasn't started yet
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
    anthropic_client: Optional[anthropic.AsyncAnthropic],
    settings: Settings,
) -> None:
    """
    Score one opportunity and forward it to signals:approved.
    Never raises — errors are caught and logged; the signal is always forwarded.
    """
    # ── Deserialise ────────────────────────────────────────────────────────────
    raw_payload = fields.get(StreamNames.PAYLOAD, "{}")
    try:
        payload = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError):
        log.warning("consumer.bad_payload", msg_id=msg_id)
        payload = {}

    # ── Score ──────────────────────────────────────────────────────────────────
    if anthropic_client is not None:
        result = await score_opportunity(anthropic_client, settings, payload, redis=redis)
    else:
        # API key not configured — advisory layer skipped, risk engine runs alone
        result = {"score": None, "reason": "ai_not_configured", "timeout": True}

    # ── Enrich payload ─────────────────────────────────────────────────────────
    scored_at = datetime.now(timezone.utc).isoformat()
    enriched_payload = {
        **payload,
        "ai_score": result["score"],
        "ai_reason": result["reason"],
        "ai_timeout": result["timeout"],
        "ai_scored_at": scored_at,
    }

    # ── Forward to signals:approved ────────────────────────────────────────────
    # Merge original stream fields with updated AI fields.
    # The risk engine reads the PAYLOAD field for the full opportunity + AI data.
    approved_entry = {
        **fields,
        StreamNames.AI_SCORE: str(result["score"]) if result["score"] is not None else "",
        StreamNames.AI_TIMEOUT: "true" if result["timeout"] else "false",
        StreamNames.PAYLOAD: json.dumps(enriched_payload),
    }

    await redis.xadd(
        RedisKeys.SIGNALS_APPROVED,
        approved_entry,
        maxlen=_STREAM_MAXLEN,
        approximate=True,
    )

    log.info(
        "consumer.forwarded",
        msg_id=msg_id,
        strategy=fields.get(StreamNames.STRATEGY_TYPE),
        symbol=fields.get(StreamNames.SYMBOL_PRIMARY),
        net_edge_bps=fields.get(StreamNames.NET_EDGE_BPS),
        ai_score=result["score"],
        ai_timeout=result["timeout"],
    )


async def run(settings: Settings, redis: Redis) -> None:
    """
    Main consumer loop. Runs until cancelled (service shutdown).

    Establishes consumer group, builds the Anthropic client if configured,
    then reads and processes messages indefinitely.
    """
    await _ensure_group(redis)

    # Build Anthropic client only if key is set
    anthropic_client: Optional[anthropic.AsyncAnthropic] = None
    if settings.ai_configured:
        anthropic_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        log.info(
            "consumer.ai_ready",
            model=settings.AI_SCORING_MODEL,
            timeout_ms=settings.AI_TIMEOUT_MS,
        )
    else:
        log.warning(
            "consumer.ai_disabled",
            reason="ANTHROPIC_API_KEY not set — signals forwarded without scoring",
        )

    log.info("consumer.started", group=_GROUP, consumer=_CONSUMER)

    try:
        while True:
            try:
                response = await redis.xreadgroup(
                    groupname=_GROUP,
                    consumername=_CONSUMER,
                    streams={RedisKeys.SIGNALS_OPPORTUNITIES: ">"},
                    count=1,
                    block=_BLOCK_MS,
                )

                if not response:
                    continue  # Timeout — no new messages, loop again

                for _stream, entries in response:
                    for msg_id, fields in entries:
                        try:
                            await _process(msg_id, fields, redis, anthropic_client, settings)
                        except Exception as exc:
                            log.error("consumer.process_error", msg_id=msg_id, error=str(exc))
                        finally:
                            # ACK in finally: even on error, don't leave messages pending forever
                            await redis.xack(
                                RedisKeys.SIGNALS_OPPORTUNITIES, _GROUP, msg_id
                            )

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                log.error("consumer.loop_error", error=str(exc))
                await asyncio.sleep(1.0)

    except asyncio.CancelledError:
        log.info("consumer.cancelled")
        if anthropic_client is not None:
            await anthropic_client.close()
        raise
