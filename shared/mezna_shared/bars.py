"""
Tick → OHLCV bar resampling.

Pure and dependency-free (stdlib only) so any service can turn the Redis quote-
tick cache into OHLCV candles — the prerequisite for standard indicator
libraries (pandas-ta) and vectorised backtests (vectorbt), which expect bars
rather than the raw bid/ask tick stream the strategies currently consume.

Input ticks are QUOTE ticks (bid/ask/mid), not trades, so there is no traded
volume — `volume` is the number of ticks in the bar (a liquidity proxy).
"""

from __future__ import annotations

from datetime import datetime, timezone


def _parse_ts(value) -> float | None:
    """Epoch seconds from an ISO-8601 string or numeric epoch; None if unparsable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        try:
            return float(s)  # epoch encoded as a string
        except ValueError:
            return None


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _make_bar(bucket: int, o: float, h: float, l: float, c: float, vol: int) -> dict:
    return {
        "epoch": float(bucket),
        "ts": datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat(),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": vol,
    }


def ticks_to_ohlcv(
    ticks: list[dict],
    interval_seconds: int,
    *,
    price_field: str = "mid",
    ts_field: str = "timestamp",
    newest_first: bool = True,
) -> list[dict]:
    """
    Resample quote ticks into time-bucketed OHLCV bars (returned OLDEST first).

    Each bar: {"epoch", "ts" (ISO UTC bucket start), "open", "high", "low",
               "close", "volume" (tick count in the bar)}.

    Args:
        ticks:            tick dicts with a timestamp and a price field.
        interval_seconds: bar width in seconds (e.g. 60 = 1-minute bars).
        price_field:      price used for OHLC (default "mid").
        ts_field:         timestamp field name (default "timestamp").
        newest_first:     True if `ticks` is most-recent-first (the Redis LPUSH
                          cache is). Only affects which tick wins ties within the
                          same millisecond; bars are sorted chronologically anyway.

    Ticks with an unparsable timestamp or non-positive price are skipped.
    Returns [] for no usable data or interval_seconds <= 0.
    """
    if interval_seconds <= 0 or not ticks:
        return []

    rows: list[tuple[float, float]] = []
    for t in ticks:
        ts = _parse_ts(t.get(ts_field))
        px = _to_float(t.get(price_field))
        if ts is None or px is None or px <= 0:
            continue
        rows.append((ts, px))

    if not rows:
        return []

    # Chronological. newest_first only matters for stable ordering of equal ts.
    rows.sort(key=lambda r: r[0], reverse=False)

    bars: list[dict] = []
    cur_bucket: int | None = None
    o = h = l = c = 0.0
    vol = 0

    for ts, px in rows:
        bucket = int(ts // interval_seconds) * interval_seconds
        if cur_bucket is None or bucket != cur_bucket:
            if cur_bucket is not None:
                bars.append(_make_bar(cur_bucket, o, h, l, c, vol))
            cur_bucket = bucket
            o = h = l = c = px
            vol = 1
        else:
            h = max(h, px)
            l = min(l, px)
            c = px
            vol += 1

    if cur_bucket is not None:
        bars.append(_make_bar(cur_bucket, o, h, l, c, vol))

    return bars


def ohlcv_columns(bars: list[dict]) -> dict[str, list]:
    """
    Transpose a bar list into column arrays — convenient for numpy / pandas-ta:

        cols = ohlcv_columns(bars)
        pandas.DataFrame(cols)   # columns: ts, open, high, low, close, volume
    """
    keys = ("ts", "open", "high", "low", "close", "volume")
    return {k: [b[k] for b in bars] for k in keys}
