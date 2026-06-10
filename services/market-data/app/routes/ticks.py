"""
Tick snapshot endpoint — read the latest bid/ask for configured symbols.

  GET /ticks/latest            — latest tick for every configured feed symbol
  GET /ticks/latest?venues=binance,oanda
                               — restrict to specific venues

This is the HTTP read path over the `tick:latest:{venue}:{symbol}` Redis hashes
the feeds maintain (see app/feeds/publisher.py). It gives the dashboard a
first-paint snapshot and a polling fallback for the SSE stream the gateway
fans out. No DB access — pure Redis, one pipelined round-trip.
"""

import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from mezna_shared.redis_client import RedisKeys
from ..config import settings

log = structlog.get_logger()
router = APIRouter()

# Numeric fields stored as strings in the Redis hash (see NormalisedTick.to_redis_hash).
_FLOAT_FIELDS = ("bid", "ask", "mid", "spread_bps")


def _coerce(raw: dict[str, str]) -> dict:
    """Turn a flat string tick hash into a JSON-friendly object (numbers as floats)."""
    out: dict = dict(raw)
    for field in _FLOAT_FIELDS:
        if field in out:
            try:
                out[field] = float(out[field])
            except (TypeError, ValueError):
                out[field] = None
    return out


@router.get("/ticks/latest", summary="Latest bid/ask snapshot for configured symbols")
async def latest_ticks(request: Request) -> JSONResponse:
    """
    Return the most recent tick for every configured (venue, symbol) pair.

    Optional `venues` query param (comma-separated) restricts the result set.
    Symbols that have no tick yet (feed disabled or not warmed up) are returned
    with `live: false` and null prices so the UI can render a stable grid.
    """
    redis = request.app.state.redis

    venue_filter = request.query_params.get("venues")
    wanted = {v.strip().lower() for v in venue_filter.split(",")} if venue_filter else None

    targets = [
        (venue, symbol)
        for (venue, symbol) in settings.bar_writer_targets
        if wanted is None or venue.lower() in wanted
    ]

    # One pipelined round-trip for every requested hash.
    pipe = redis.pipeline()
    for venue, symbol in targets:
        pipe.hgetall(RedisKeys.latest_tick(venue, symbol))
    rows = await pipe.execute()

    ticks: list[dict] = []
    for (venue, symbol), raw in zip(targets, rows):
        if raw:
            tick = _coerce(raw)
            tick["live"] = True
            ticks.append(tick)
        else:
            ticks.append({
                "venue": venue,
                "symbol": symbol,
                "market_type": None,
                "bid": None,
                "ask": None,
                "mid": None,
                "spread_bps": None,
                "timestamp": None,
                "live": False,
            })

    return JSONResponse(
        content={
            "ticks": ticks,
            "count": len(ticks),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@router.get("/orderbook/latest", summary="Latest L2 order-book snapshot for one symbol")
async def latest_orderbook(
    request: Request,
    venue: str = Query(..., description="Venue, e.g. binance"),
    symbol: str = Query(..., description="ccxt unified symbol, e.g. BTC/USDT"),
) -> JSONResponse:
    """
    Return the most recent depth ladder (bids/asks) for one (venue, symbol),
    populated by the order-book feed. 404 when the feed is disabled for this
    symbol or the snapshot has expired (TTL = ORDERBOOK_TTL_SECONDS).
    """
    raw = await request.app.state.redis.get(RedisKeys.order_book(venue, symbol))
    if not raw:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "status": "empty",
                "detail": "No order book — enable it via ORDERBOOK_SYMBOLS or the snapshot expired",
                "venue": venue,
                "symbol": symbol,
            },
        )
    try:
        book = json.loads(raw)
    except (TypeError, ValueError):
        return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"status": "error", "detail": "corrupt snapshot"})
    return JSONResponse(content={"status": "ok", **book})
