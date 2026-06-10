"""
Idempotent-replay helpers for order execution.

Records an OrderResult under its (deterministic) client_order_id so a redelivered
message reuses it instead of resubmitting the order. Kept ccxt-free (imports only
the OrderResult dataclass) so the logic is unit-testable without the exchange stack.
"""

from __future__ import annotations

import json

import structlog
from redis.asyncio import Redis

from .adapters import OrderResult

log = structlog.get_logger()

# raw_response may hold non-JSON exchange objects — persist only these fields.
_RESULT_FIELDS = (
    "client_order_id", "exchange_order_id", "status", "filled_qty",
    "average_fill_price", "fee", "fee_currency", "slippage_bps", "rejection_reason",
)


def serialize_result(result: OrderResult) -> str:
    return json.dumps({f: getattr(result, f) for f in _RESULT_FIELDS})


def deserialize_result(blob: str) -> OrderResult | None:
    try:
        data = json.loads(blob)
        return OrderResult(raw_response={"replayed": True}, **{f: data.get(f) for f in _RESULT_FIELDS})
    except Exception:
        return None


async def recover_result(redis: Redis, result_key: str) -> OrderResult | None:
    """Return a previously recorded result for this key, or None. Best-effort."""
    try:
        prior = await redis.get(result_key)
    except Exception:
        return None
    return deserialize_result(prior) if prior else None


async def record_result(redis: Redis, result_key: str, result: OrderResult, ttl: int) -> None:
    """Record a result for idempotent replay. Best-effort — never blocks the fill."""
    try:
        await redis.set(result_key, serialize_result(result), ex=ttl)
    except Exception as exc:
        log.warning("executor.result_record_failed", result_key=result_key, error=str(exc))
