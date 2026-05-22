"""
Opportunity signal publisher — writes to the Redis Stream.

The Redis Stream (signals:opportunities) is consumed by:
  1. ai-filter service  — scores the opportunity with Claude Haiku
  2. risk service       — checks position limits, drawdown, halt state
  3. executor service   — places the order (paper or live)

Stream entry format:
  All values are strings (Redis Stream requirement).
  The full OpportunityCreate payload is JSON-encoded in the PAYLOAD field
  so downstream services can deserialise without re-reading every individual field.
"""

import uuid
from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis

from mezna_shared.redis_client import RedisKeys, StreamNames
from mezna_shared.schemas.opportunity import OpportunityCreate

log = structlog.get_logger()

STREAM_MAXLEN = 1000  # Rolling window — old signals auto-pruned


async def publish_opportunity(redis: Redis, opp: OpportunityCreate) -> str:
    """
    Publish an opportunity to the Redis Stream.

    Generates a UUID as the opportunity_id (used as idempotency key downstream).
    Returns the Redis stream message ID (e.g., "1234567890123-0").
    """
    opportunity_id = str(uuid.uuid4())

    stream_entry = {
        StreamNames.OPPORTUNITY_ID: opportunity_id,
        StreamNames.STRATEGY_TYPE: opp.strategy_type,
        StreamNames.VENUE: opp.venue,
        StreamNames.SYMBOL_PRIMARY: opp.symbol_primary,
        StreamNames.SYMBOL_SECONDARY: opp.symbol_secondary or "",
        StreamNames.NET_EDGE_BPS: str(opp.net_edge_bps),
        StreamNames.AI_SCORE: "",          # populated by ai-filter service
        StreamNames.AI_TIMEOUT: "false",   # updated by ai-filter if it times out
        StreamNames.PAPER_MODE: "true" if opp.paper_mode else "false",
        StreamNames.DETECTED_AT: opp.detected_at.isoformat(),
        StreamNames.PAYLOAD: opp.model_dump_json(),
    }

    msg_id = await redis.xadd(
        RedisKeys.SIGNALS_OPPORTUNITIES,
        stream_entry,
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )

    log.info(
        "opportunity.published",
        opportunity_id=opportunity_id,
        strategy=opp.strategy_type,
        symbol_primary=opp.symbol_primary,
        symbol_secondary=opp.symbol_secondary,
        net_edge_bps=opp.net_edge_bps,
        paper_mode=opp.paper_mode,
        msg_id=msg_id,
    )

    return str(msg_id)
