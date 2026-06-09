"""
Server-Sent Events stream — the real-time spine for the trading terminal.

  GET /stream  — text/event-stream pushing three event types:

      event: ticks    → { ticks: [...], ts }      latest bid/ask per symbol
      event: risk     → { halted, risk_state }     kill-switch + live risk gauges
      event: signals  → { signals: [...] }         new opportunities since last poll

The gateway is the natural aggregation point: every service writes to the one
shared Redis, and the gateway already holds a connection. This single EventSource
drives the whole cockpit (price tape, risk meters, signal feed) so the UI no
longer polls a dozen endpoints — or fakes data client-side.

SSE (not WebSocket) is deliberate: it survives the existing Next.js /api/gateway
proxy unchanged, auto-reconnects in the browser, and needs no extra dependency.
A WebSocket upgrade can come later for sub-second order flow.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncIterator

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from mezna_shared.redis_client import RedisKeys

log = structlog.get_logger()
router = APIRouter()

# How often the stream re-reads Redis and flushes to the client.
_TICK_INTERVAL_S = 1.0
# Cap per flush so a cold stream or a burst can't blow up a frame.
_MAX_TICKS = 64
_MAX_SIGNALS_PER_FLUSH = 25

_FLOAT_FIELDS = ("bid", "ask", "mid", "spread_bps")


def _sse(event: str, data: dict) -> str:
    """Encode one named SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _coerce_tick(raw: dict[str, str]) -> dict:
    out: dict = dict(raw)
    for field in _FLOAT_FIELDS:
        if field in out:
            try:
                out[field] = float(out[field])
            except (TypeError, ValueError):
                out[field] = None
    return out


async def _read_ticks(redis: Redis) -> list[dict]:
    """Snapshot every tick:latest:* hash (one SCAN + one pipelined HGETALL)."""
    keys: list[str] = []
    async for key in redis.scan_iter(match="tick:latest:*", count=200):
        keys.append(key)
        if len(keys) >= _MAX_TICKS:
            break
    if not keys:
        return []
    pipe = redis.pipeline()
    for key in keys:
        pipe.hgetall(key)
    rows = await pipe.execute()
    return [_coerce_tick(r) for r in rows if r]


async def _read_risk(redis: Redis) -> dict:
    """Kill-switch flag + the live risk-state hash the risk engine maintains."""
    pipe = redis.pipeline()
    pipe.get(RedisKeys.HALT)
    pipe.hgetall(RedisKeys.RISK_STATE)
    halt, state = await pipe.execute()
    return {"halted": halt == "1", "risk_state": state or {}}


async def _read_signals(redis: Redis, last_id: str) -> tuple[str, list[dict]]:
    """Return (new_last_id, signals) for opportunities added since last_id."""
    try:
        resp = await redis.xread(
            {RedisKeys.SIGNALS_OPPORTUNITIES: last_id},
            count=_MAX_SIGNALS_PER_FLUSH,
        )
    except Exception:
        return last_id, []
    if not resp:
        return last_id, []

    signals: list[dict] = []
    new_last = last_id
    for _stream, entries in resp:
        for entry_id, fields in entries:
            new_last = entry_id
            item = dict(fields)
            item["_id"] = entry_id
            payload = item.get("payload")
            if payload:
                try:
                    item["payload"] = json.loads(payload)
                except (TypeError, ValueError):
                    pass
            signals.append(item)
    return new_last, signals


async def _event_stream(request: Request, redis: Redis) -> AsyncIterator[str]:
    # Only stream opportunities that arrive after the connection opens.
    try:
        tail = await redis.xrevrange(RedisKeys.SIGNALS_OPPORTUNITIES, count=1)
        last_signal_id = tail[0][0] if tail else "0-0"
    except Exception:
        last_signal_id = "0-0"

    # Tell the browser how fast to reconnect, then flush an immediate snapshot.
    yield "retry: 3000\n\n"

    while True:
        if await request.is_disconnected():
            break
        try:
            ticks = await _read_ticks(redis)
            yield _sse("ticks", {"ticks": ticks, "ts": datetime.now(timezone.utc).isoformat()})

            yield _sse("risk", await _read_risk(redis))

            last_signal_id, signals = await _read_signals(redis, last_signal_id)
            if signals:
                yield _sse("signals", {"signals": signals})
            else:
                # Comment frame keeps the connection warm + lets us detect drops.
                yield ": hb\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("stream.read_error", error=str(exc))
            yield _sse("error", {"message": "stream read error"})

        await asyncio.sleep(_TICK_INTERVAL_S)


@router.get("/stream", summary="Real-time SSE stream — ticks, risk, signals")
async def stream(request: Request) -> StreamingResponse:
    redis = request.app.state.redis
    return StreamingResponse(
        _event_stream(request, redis),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Disable proxy buffering (nginx / Next) so frames flush immediately.
            "X-Accel-Buffering": "no",
        },
    )
