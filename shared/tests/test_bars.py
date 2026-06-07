"""Tests for tick → OHLCV resampling (mezna_shared.bars)."""

from mezna_shared.bars import ticks_to_ohlcv, ohlcv_columns


def _tick(ts, mid):
    return {"timestamp": ts, "mid": str(mid), "bid": str(mid - 0.5), "ask": str(mid + 0.5)}


def test_buckets_by_interval_oldest_first():
    # Two 60s buckets; input is newest-first (like the Redis LPUSH cache).
    ticks = [
        _tick("2026-06-07T00:01:30+00:00", 105),  # bucket 00:01
        _tick("2026-06-07T00:01:10+00:00", 101),
        _tick("2026-06-07T00:00:50+00:00", 103),  # bucket 00:00
        _tick("2026-06-07T00:00:10+00:00", 100),
    ]
    bars = ticks_to_ohlcv(ticks, 60)
    assert len(bars) == 2
    b0, b1 = bars                                   # returned oldest-first
    assert b0["open"] == 100 and b0["close"] == 103
    assert b0["high"] == 103 and b0["low"] == 100 and b0["volume"] == 2
    assert b1["open"] == 101 and b1["close"] == 105 and b1["high"] == 105
    assert b0["epoch"] < b1["epoch"]
    assert b0["ts"].endswith("+00:00")


def test_single_bar_ohlc():
    ticks = [_tick(f"2026-06-07T00:00:{s:02d}+00:00", p)
             for s, p in [(5, 100), (15, 110), (25, 90), (35, 105)]]
    (bar,) = ticks_to_ohlcv(ticks, 60)
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (100, 110, 90, 105)
    assert bar["volume"] == 4


def test_skips_bad_ticks_and_handles_z_suffix():
    ticks = [
        {"timestamp": "2026-06-07T00:00:05Z", "mid": "100"},     # Z suffix ok
        {"timestamp": "bad", "mid": "999"},                       # bad ts -> skip
        {"timestamp": "2026-06-07T00:00:15Z", "mid": "nope"},     # bad price -> skip
        {"timestamp": "2026-06-07T00:00:25Z", "mid": "0"},        # non-positive -> skip
        {"timestamp": "2026-06-07T00:00:35Z", "mid": "110"},
    ]
    (bar,) = ticks_to_ohlcv(ticks, 60)
    assert bar["open"] == 100 and bar["close"] == 110 and bar["volume"] == 2


def test_empty_and_bad_interval():
    assert ticks_to_ohlcv([], 60) == []
    assert ticks_to_ohlcv([_tick("2026-06-07T00:00:05Z", 100)], 0) == []


def test_ohlcv_columns_shape():
    ticks = [_tick("2026-06-07T00:00:05Z", 100), _tick("2026-06-07T00:01:05Z", 101)]
    cols = ohlcv_columns(ticks_to_ohlcv(ticks, 60))
    assert set(cols) == {"ts", "open", "high", "low", "close", "volume"}
    assert len(cols["close"]) == 2 and cols["close"] == [100, 101]
