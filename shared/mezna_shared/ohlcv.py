"""
OHLCV bar persistence to the `ohlcv_bars` table.

Bridges the in-memory bar layer (mezna_shared.bars.ticks_to_ohlcv) and the
exchange REST backfill to durable storage so the backtest service has real
history to simulate on, and the directional bar-mode strategies gain history
beyond the 500-tick live cache.

Two producers, last-writer-wins on the natural key (venue, symbol, interval,
bucket_start):
  * market-data live bar writer  → upsert_bars(..., source="live_ticks")
  * ccxt OHLCV backfill          → upsert_bars(..., source="exchange_rest")

Writes are best-effort (logged + swallowed) so persistence never interrupts the
feed/trading path. Reads return engine-shaped candle dicts ({ts, ts_dt, open,
high, low, close, volume, mid}) so backtest code consumes them like the Binance
REST path.

Schema: migrations/versions/004_ohlcv_bars.py / models/ohlcv_bar.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .db import get_async_session

log = structlog.get_logger()


# ── Interval label ↔ seconds ────────────────────────────────────────────────
# Canonical labels stored in ohlcv_bars.interval. Mirrors ccxt timeframe strings
# for the intervals we backfill, plus sub-minute labels for tick-resampled bars.
_INTERVAL_SECONDS: dict[str, int] = {
    "15s": 15,
    "30s": 30,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1d": 86400,
}
_SECONDS_INTERVAL: dict[int, str] = {v: k for k, v in _INTERVAL_SECONDS.items()}


def interval_to_seconds(label: str) -> int:
    """Seconds for a canonical interval label. Raises ValueError if unknown."""
    try:
        return _INTERVAL_SECONDS[label]
    except KeyError:
        raise ValueError(f"unknown interval label: {label!r}") from None


def seconds_to_interval(seconds: int) -> str:
    """
    Canonical label for a bar width in seconds.

    Falls back to "<n>s" for widths without a named label so the writer can still
    persist arbitrary BAR_WRITER_BAR_SECONDS without crashing.
    """
    return _SECONDS_INTERVAL.get(seconds, f"{seconds}s")


# ── Upsert ──────────────────────────────────────────────────────────────────

def _bucket_epoch(bar: dict) -> float | None:
    """
    Epoch-seconds bucket start from a bar dict.

    Accepts the ticks_to_ohlcv shape ("epoch" float / "ts" ISO) or a raw numeric
    "bucket_start". Returns None if unparsable so the caller can skip the row.
    """
    if bar.get("epoch") is not None:
        try:
            return float(bar["epoch"])
        except (TypeError, ValueError):
            pass
    raw = bar.get("bucket_start") if bar.get("bucket_start") is not None else bar.get("ts")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, datetime):
        return raw.timestamp()
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def bar_upsert_params(venue: str, symbol: str, interval: str, bar: dict, source: str) -> dict | None:
    """
    Map one bar dict to upsert params, or None if it can't be persisted.

    Pure and side-effect-free so it can be unit-tested. Drops bars with a missing
    bucket or a non-positive/invalid OHLC.
    """
    epoch = _bucket_epoch(bar)
    if epoch is None:
        return None
    try:
        o = float(bar["open"]); h = float(bar["high"])
        l = float(bar["low"]); c = float(bar["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(x > 0 for x in (o, h, l, c)):
        return None
    try:
        vol = float(bar.get("volume", 0) or 0)
    except (TypeError, ValueError):
        vol = 0.0
    return {
        "venue": venue,
        "symbol": symbol,
        "interval": interval,
        "bucket_epoch": epoch,
        "open": o, "high": h, "low": l, "close": c,
        "volume": vol,
        "source": source,
    }


_UPSERT_BAR = text("""
    INSERT INTO ohlcv_bars (
        venue, symbol, interval, bucket_start,
        open, high, low, close, volume, source, updated_at
    ) VALUES (
        :venue, :symbol, :interval, to_timestamp(:bucket_epoch),
        :open, :high, :low, :close, :volume, :source, now()
    )
    ON CONFLICT (venue, symbol, interval, bucket_start) DO UPDATE SET
        open       = EXCLUDED.open,
        high       = EXCLUDED.high,
        low        = EXCLUDED.low,
        close      = EXCLUDED.close,
        volume     = EXCLUDED.volume,
        source     = EXCLUDED.source,
        updated_at = now()
""")


async def upsert_bars(
    db_engine: AsyncEngine,
    venue: str,
    symbol: str,
    interval: str,
    bars: list[dict],
    *,
    source: str = "live_ticks",
) -> int:
    """
    Best-effort batch upsert of bars for one (venue, symbol, interval).

    Returns the number of rows written. Never raises — logs and returns what it
    managed so the feed/backfill path is never interrupted.
    """
    if not bars:
        return 0
    params = [
        p for p in (bar_upsert_params(venue, symbol, interval, b, source) for b in bars)
        if p is not None
    ]
    if not params:
        return 0
    try:
        async with get_async_session(db_engine) as session:
            await session.execute(_UPSERT_BAR, params)
        return len(params)
    except Exception as exc:
        log.error(
            "ohlcv.upsert_failed",
            venue=venue, symbol=symbol, interval=interval,
            count=len(params), error=str(exc),
        )
        return 0


# ── Read ──────────────────────────────────────────────────────────────────

_READ_BARS = text("""
    SELECT
        EXTRACT(EPOCH FROM bucket_start) AS epoch,
        bucket_start, open, high, low, close, volume
    FROM ohlcv_bars
    WHERE venue = :venue AND symbol = :symbol AND interval = :interval
      AND bucket_start >= to_timestamp(:start_epoch)
      AND bucket_start <  to_timestamp(:end_epoch)
    ORDER BY bucket_start ASC
    LIMIT :limit
""")

_COUNT_BARS = text("""
    SELECT COUNT(*) FROM ohlcv_bars
    WHERE venue = :venue AND symbol = :symbol AND interval = :interval
""")

_LATEST_EPOCH = text("""
    SELECT EXTRACT(EPOCH FROM MAX(bucket_start)) FROM ohlcv_bars
    WHERE venue = :venue AND symbol = :symbol AND interval = :interval
""")


def _row_to_candle(row) -> dict:
    """Map a DB row to the engine candle shape (ts in ms, like the Binance path)."""
    epoch = float(row.epoch)
    o, h, l, c = float(row.open), float(row.high), float(row.low), float(row.close)
    return {
        "ts": int(epoch * 1000),
        "ts_dt": datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(),
        "open": o, "high": h, "low": l, "close": c,
        "volume": float(row.volume),
        "mid": (o + c) / 2.0,
    }


async def read_bars(
    db_engine: AsyncEngine,
    venue: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    limit: int = 100_000,
) -> list[dict]:
    """
    Read candles for [start_ms, end_ms) as engine-shaped dicts (oldest first).

    Best-effort: returns [] on error or no data so the caller can fall back to a
    live REST fetch.
    """
    try:
        async with get_async_session(db_engine) as session:
            rows = (await session.execute(_READ_BARS, {
                "venue": venue, "symbol": symbol, "interval": interval,
                "start_epoch": start_ms / 1000.0,
                "end_epoch": end_ms / 1000.0,
                "limit": limit,
            })).fetchall()
        return [_row_to_candle(r) for r in rows]
    except Exception as exc:
        log.error("ohlcv.read_failed", venue=venue, symbol=symbol, interval=interval, error=str(exc))
        return []


async def count_bars(db_engine: AsyncEngine, venue: str, symbol: str, interval: str) -> int:
    """Total stored bars for a key. Best-effort (0 on error)."""
    try:
        async with get_async_session(db_engine) as session:
            row = (await session.execute(_COUNT_BARS, {
                "venue": venue, "symbol": symbol, "interval": interval,
            })).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception as exc:
        log.error("ohlcv.count_failed", venue=venue, symbol=symbol, interval=interval, error=str(exc))
        return 0


async def latest_bar_epoch(db_engine: AsyncEngine, venue: str, symbol: str, interval: str) -> float | None:
    """
    Epoch-seconds of the newest stored bucket for a key, or None if empty.

    The backfill uses this to resume incrementally instead of re-pulling history.
    """
    try:
        async with get_async_session(db_engine) as session:
            row = (await session.execute(_LATEST_EPOCH, {
                "venue": venue, "symbol": symbol, "interval": interval,
            })).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except Exception as exc:
        log.error("ohlcv.latest_failed", venue=venue, symbol=symbol, interval=interval, error=str(exc))
        return None
