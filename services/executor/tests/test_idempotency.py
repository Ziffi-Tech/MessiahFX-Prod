"""Tests for executor idempotent-replay helpers (ccxt-free)."""

import asyncio

from app.adapters import OrderResult
from app.idempotency import serialize_result, deserialize_result, recover_result, record_result


class FakeRedis:
    def __init__(self):
        self.kv: dict[str, str] = {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None):
        self.kv[k] = v


def _result(status="filled"):
    return OrderResult(
        client_order_id="mx-abc", exchange_order_id="X1", status=status,
        filled_qty=1.0, average_fill_price=100.0, fee=0.1, fee_currency="USDT",
        slippage_bps=1.0, rejection_reason=None, raw_response={"exchange": "object"},
    )


def test_serialize_round_trip_drops_raw_response():
    blob = serialize_result(_result())
    back = deserialize_result(blob)
    assert back is not None
    assert back.client_order_id == "mx-abc"
    assert back.status == "filled"
    assert back.filled_qty == 1.0
    # raw_response is NOT serialised (may hold non-JSON exchange objects).
    assert back.raw_response == {"replayed": True}


def test_deserialize_garbage_is_none():
    assert deserialize_result("not json") is None


def test_recover_then_record_round_trip():
    redis = FakeRedis()
    key = "execution:result:mx-abc"
    # nothing recorded yet
    assert asyncio.run(recover_result(redis, key)) is None
    # record, then recover
    asyncio.run(record_result(redis, key, _result(), ttl=60))
    recovered = asyncio.run(recover_result(redis, key))
    assert recovered is not None and recovered.status == "filled"


def test_record_failure_is_swallowed():
    class BoomRedis:
        async def set(self, *a, **k):
            raise RuntimeError("redis down")

    # Must not raise — recording is best-effort.
    asyncio.run(record_result(BoomRedis(), "k", _result(), ttl=60))


def test_recover_failure_is_none():
    class BoomRedis:
        async def get(self, *a, **k):
            raise RuntimeError("redis down")

    assert asyncio.run(recover_result(BoomRedis(), "k")) is None
