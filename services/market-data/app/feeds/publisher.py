"""
Redis publisher for normalised ticks.

Every tick is written to two Redis structures atomically via pipeline:

  tick:latest:{venue}:{symbol}  (Hash)
    — Always the most recent bid/ask. Strategy reads this for instant price lookups.
    — Overwritten on every tick. No TTL — stale if feed dies but always readable.

  ticks:{venue}:{symbol}  (List, ring buffer)
    — Up to TICK_CACHE_MAX_SIZE most recent ticks (LPUSH + LTRIM).
    — Stat arb calculations read a window from this list to compute z-scores.
    — Each entry is a JSON string of the tick's redis hash.

Feed heartbeat:
  feed:heartbeat:{venue}  (String, with TTL)
    — Written periodically while the feed is alive.
    — If this key expires, health checks report the feed as dead.
    — TTL is HEARTBEAT_TTL seconds. Feeds must refresh before expiry.
"""

import json
from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis

from mezna_shared.redis_client import RedisKeys
from .normaliser import NormalisedTick

log = structlog.get_logger()

# Feed considered dead if no heartbeat update within this window.
# Individual feed implementations must refresh more frequently than this.
HEARTBEAT_TTL: int = 30  # seconds


async def publish_tick(redis: Redis, tick: NormalisedTick, cache_max: int = 500) -> None:
    """
    Write a tick to Redis atomically.

    Uses a pipeline to batch three commands into one round-trip:
      1. HSET  tick:latest:{venue}:{symbol}  — update latest prices
      2. LPUSH ticks:{venue}:{symbol}        — prepend to ring buffer
      3. LTRIM ticks:{venue}:{symbol}        — keep only last cache_max entries
    """
    cache_key = RedisKeys.tick_cache(tick.venue, tick.symbol)
    latest_key = RedisKeys.latest_tick(tick.venue, tick.symbol)
    serialized = json.dumps(tick.to_redis_hash())

    pipe = redis.pipeline()
    pipe.hset(latest_key, mapping=tick.to_redis_hash())
    pipe.lpush(cache_key, serialized)
    pipe.ltrim(cache_key, 0, cache_max - 1)
    await pipe.execute()


async def update_heartbeat(redis: Redis, venue: str) -> None:
    """
    Refresh the feed heartbeat key with a new TTL.
    Must be called at least once per HEARTBEAT_TTL seconds or the health
    check will report this feed as dead.
    """
    await redis.set(
        RedisKeys.feed_heartbeat(venue),
        datetime.now(timezone.utc).isoformat(),
        ex=HEARTBEAT_TTL,
    )
