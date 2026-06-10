"""
Redis client factory and key schema definitions.

All services use this module to obtain a Redis connection.
Key schema is defined here to maintain a single source of truth.

Usage:
    from mezna_shared.redis_client import get_redis, RedisKeys

    redis = await get_redis(settings.REDIS_URL)
    halt = await redis.get(RedisKeys.HALT)
"""

import structlog
from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool

log = structlog.get_logger()

# Module-level pool — one pool per process
_pool: ConnectionPool | None = None


async def get_redis(url: str, max_connections: int = 10) -> Redis:
    """
    Return a Redis client backed by a shared connection pool.
    Call once at service startup; reuse the returned client throughout.
    """
    global _pool
    if _pool is None:
        _pool = ConnectionPool.from_url(
            url,
            max_connections=max_connections,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        log.info("redis.pool_created", url=url, max_connections=max_connections)
    return Redis(connection_pool=_pool)


async def close_redis() -> None:
    """Close the shared connection pool. Call at service shutdown."""
    global _pool
    if _pool is not None:
        await _pool.disconnect()
        _pool = None
        log.info("redis.pool_closed")


class RedisKeys:
    """
    Centralised Redis key schema.
    All keys used across services are defined here.

    NEVER use raw strings for Redis keys in service code.
    Always reference RedisKeys.* to maintain consistency.
    """

    # ── Risk State ────────────────────────────────────────────────────────────
    # Hash: risk engine writes, all services read pre-trade
    RISK_STATE = "risk:state"

    # String: 0 or 1 — fastest possible read in critical path
    HALT = "risk:halt"

    # String per strategy (with TTL = cooldown duration)
    # Use: RedisKeys.cooldown("funding_arb")
    @staticmethod
    def cooldown(strategy_type: str) -> str:
        return f"risk:cooldown:{strategy_type}"

    # ── Strategy State ────────────────────────────────────────────────────────
    # Hash per strategy
    @staticmethod
    def strategy_state(strategy_type: str) -> str:
        return f"strategy:state:{strategy_type}"

    # ── Signal Streams ────────────────────────────────────────────────────────
    # Redis Stream: strategy → (ai-filter + risk) → executor
    SIGNALS_OPPORTUNITIES = "signals:opportunities"
    SIGNALS_APPROVED = "signals:approved"
    SIGNALS_REJECTED = "signals:rejected"

    # Dedicated TradingView inbound stream.
    # Gateway writes here; strategy service signal_consumer reads here.
    # Keeps TV signals separate from internal strategy opportunities.
    SIGNALS_TV = "signals:tradingview"

    # ── Market Data ───────────────────────────────────────────────────────────
    # Capped list per symbol (most recent N ticks)
    @staticmethod
    def tick_cache(venue: str, symbol: str) -> str:
        clean_symbol = symbol.replace("/", "_").replace(":", "_")
        return f"ticks:{venue}:{clean_symbol}"

    # Hash: latest tick per symbol (single-key fast read)
    @staticmethod
    def latest_tick(venue: str, symbol: str) -> str:
        clean_symbol = symbol.replace("/", "_").replace(":", "_")
        return f"tick:latest:{venue}:{clean_symbol}"

    # String (JSON): latest L2 order-book snapshot per symbol (top-N levels).
    # Written with a short TTL so a dead order-book feed's book disappears.
    @staticmethod
    def order_book(venue: str, symbol: str) -> str:
        clean_symbol = symbol.replace("/", "_").replace(":", "_")
        return f"orderbook:{venue}:{clean_symbol}"

    # Last heartbeat timestamp per feed
    @staticmethod
    def feed_heartbeat(venue: str) -> str:
        return f"feed:heartbeat:{venue}"

    # ── Paper Trading ─────────────────────────────────────────────────────────
    # Hash: virtual balance per venue
    @staticmethod
    def paper_balance(venue: str) -> str:
        return f"paper:balance:{venue}"

    # ── Execution queue (risk-approved signals ready for executor) ────────────
    SIGNALS_EXECUTION_QUEUE = "signals:execution_queue"

    # String (JSON): recorded OrderResult per client_order_id, for idempotent
    # replay — a redelivered leg reuses the stored result instead of resubmitting.
    @staticmethod
    def execution_result(client_order_id: str) -> str:
        return f"execution:result:{client_order_id}"

    # ── Notification Queue ────────────────────────────────────────────────────
    NOTIFICATION_QUEUE = "notifications:queue"

    # ── Session revocation (dashboard auth) ───────────────────────────────────
    # Epoch (seconds): any session token with iat < this value is revoked.
    SESSION_REVOKE_ALL = "session:revoke:all"

    @staticmethod
    def session_revoke_user(sub: str) -> str:
        return f"session:revoke:user:{sub}"


class StreamNames:
    """Redis Stream field names for opportunity signals."""

    # Opportunity object fields in stream
    OPPORTUNITY_ID = "opportunity_id"
    STRATEGY_TYPE = "strategy_type"
    VENUE = "venue"
    SYMBOL_PRIMARY = "symbol_primary"
    SYMBOL_SECONDARY = "symbol_secondary"
    NET_EDGE_BPS = "net_edge_bps"
    AI_SCORE = "ai_score"
    AI_TIMEOUT = "ai_timeout"
    PAPER_MODE = "paper_mode"
    DETECTED_AT = "detected_at"
    PAYLOAD = "payload"  # Full JSON blob
