"""
Shared utilities for all strategy modules.

Strategies call these helpers to read tick data from Redis and check
system state (halt, toggle, cooldown) before doing any computation.
All Redis reads are fast (HGETALL / GET / EXISTS on local Redis).
"""

import json

import structlog
from redis.asyncio import Redis

from mezna_shared.redis_client import RedisKeys

log = structlog.get_logger()

# Seconds to sleep per iteration, keyed by latency profile.
# Strategies do NOT sleep themselves — the runner applies the delay.
LATENCY_DELAYS: dict[str, float] = {
    "fast": 0.1,
    "standard": 0.5,
    "relaxed": 2.0,
}


async def read_latest_tick(redis: Redis, venue: str, symbol: str) -> dict | None:
    """
    Read the most recent bid/ask tick for a symbol.
    Returns None if the market-data service hasn't published yet.
    """
    key = RedisKeys.latest_tick(venue, symbol)
    data = await redis.hgetall(key)
    return data if data else None


async def read_tick_cache(redis: Redis, venue: str, symbol: str, n: int) -> list[dict]:
    """
    Read the last N ticks from the ring buffer (most recent first).
    Returns fewer than N entries if the cache hasn't filled yet.
    """
    key = RedisKeys.tick_cache(venue, symbol)
    raw_list = await redis.lrange(key, 0, n - 1)
    ticks: list[dict] = []
    for raw in raw_list:
        try:
            ticks.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return ticks


async def is_halted(redis: Redis) -> bool:
    """True if the kill switch is active. Checked on every strategy iteration."""
    return await redis.get(RedisKeys.HALT) == "1"


async def get_strategy_state(redis: Redis, strategy_name: str) -> dict:
    """Return the current toggle state hash for a strategy."""
    return await redis.hgetall(RedisKeys.strategy_state(strategy_name))


async def is_on_cooldown(redis: Redis, strategy_name: str) -> bool:
    """
    True if this strategy is in a risk-engine cooldown.
    The cooldown key has a TTL set by the risk service — it disappears automatically.
    """
    return bool(await redis.exists(RedisKeys.cooldown(strategy_name)))
