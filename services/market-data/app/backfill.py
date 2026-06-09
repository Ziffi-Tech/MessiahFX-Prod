"""
Historical OHLCV backfill via ccxt REST.

Seeds the ohlcv_bars table with deep history straight from an exchange's public
OHLCV endpoint (no API key needed), so a backtest has real candles to simulate
on immediately rather than waiting for the live bar writer to accumulate them.

Uses ccxt.async_support (plain REST) against MAINNET — testnet has no real
history. Paginates fetch_ohlcv from `since` up to now, upserting in batches with
source='exchange_rest'. Incremental by default: resumes from the newest stored
bucket so re-runs only fetch the gap.

Only ccxt-backed crypto venues are supported (binance/bybit/okx/kraken). Oanda
FX history would need a separate v20 path — not handled here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog

from mezna_shared.ohlcv import upsert_bars, latest_bar_epoch, interval_to_seconds

log = structlog.get_logger()

# Venues with a ccxt class whose public OHLCV we can pull. Oanda excluded (no ccxt).
CCXT_VENUES: set[str] = {"binance", "bybit", "okx", "kraken"}

_FETCH_LIMIT = 1000          # ccxt per-call cap for most exchanges
_MAX_CALLS = 500             # hard stop so a bad range can't loop forever


def ccxt_rows_to_bars(rows: list[list]) -> list[dict]:
    """
    Map ccxt fetch_ohlcv rows ([ts_ms, open, high, low, close, volume]) to the
    bar dict shape mezna_shared.ohlcv.upsert_bars expects. Pure + testable.

    Skips malformed rows and non-positive prices.
    """
    bars: list[dict] = []
    for r in rows:
        try:
            ts_ms = int(r[0])
            o, h, l, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
            v = float(r[5]) if len(r) > 5 and r[5] is not None else 0.0
        except (IndexError, TypeError, ValueError):
            continue
        if not all(x > 0 for x in (o, h, l, c)):
            continue
        epoch = ts_ms / 1000.0
        bars.append({
            "epoch": epoch,
            "ts": datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(),
            "open": o, "high": h, "low": l, "close": c, "volume": v,
        })
    return bars


def _make_exchange(venue: str):
    """Construct a rate-limited ccxt.async_support REST exchange for a venue."""
    import ccxt.async_support as ccxt  # imported lazily — only market-data needs it

    if venue not in CCXT_VENUES:
        raise ValueError(f"backfill unsupported for venue {venue!r} (ccxt venues: {sorted(CCXT_VENUES)})")
    klass = getattr(ccxt, venue)
    return klass({"enableRateLimit": True})


async def backfill_symbol(
    db_engine,
    venue: str,
    symbol: str,
    *,
    timeframe: str = "1m",
    days: int = 7,
    incremental: bool = True,
    exchange=None,
) -> dict:
    """
    Backfill one (venue, symbol) at `timeframe` for the last `days`.

    Returns a summary dict {venue, symbol, timeframe, written, calls, since, until}.
    Best-effort upserts; raises only on exchange-construction / fatal fetch errors
    so the caller (endpoint) can surface them.
    """
    interval_seconds = interval_to_seconds(timeframe)  # validates the label early
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    since_ms = now_ms - days * 86_400_000
    if incremental:
        latest = await latest_bar_epoch(db_engine, venue, symbol, timeframe)
        if latest is not None:
            # Resume just after the newest stored bucket.
            since_ms = max(since_ms, int(latest * 1000) + interval_seconds * 1000)

    owns_exchange = exchange is None
    if owns_exchange:
        exchange = _make_exchange(venue)

    written = 0
    calls = 0
    cursor = since_ms
    try:
        while cursor < now_ms and calls < _MAX_CALLS:
            rows = await exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=_FETCH_LIMIT)
            calls += 1
            if not rows:
                break
            bars = ccxt_rows_to_bars(rows)
            written += await upsert_bars(
                db_engine, venue, symbol, timeframe, bars, source="exchange_rest"
            )
            last_ts = int(rows[-1][0])
            next_cursor = last_ts + interval_seconds * 1000
            if next_cursor <= cursor:  # no forward progress — stop
                break
            cursor = next_cursor
            if len(rows) < _FETCH_LIMIT:
                break
            await asyncio.sleep((getattr(exchange, "rateLimit", 200) or 200) / 1000.0)
    finally:
        if owns_exchange:
            await exchange.close()

    summary = {
        "venue": venue,
        "symbol": symbol,
        "timeframe": timeframe,
        "written": written,
        "calls": calls,
        "since": datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).isoformat(),
        "until": datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
    }
    log.info("backfill.done", **summary)
    return summary
