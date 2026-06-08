"""Tests for OHLCV persistence pure logic (mezna_shared.ohlcv).

Covers the interval label <-> seconds mapping, the bar -> upsert-param mapping
(including the drop conditions), and the DB-row -> engine-candle shaping. The
async DB wrappers are best-effort and exercised against a live DB elsewhere.
"""

from datetime import datetime, timezone

import pytest

from mezna_shared.ohlcv import (
    bar_upsert_params,
    interval_to_seconds,
    seconds_to_interval,
    _row_to_candle,
)


# ── Interval label <-> seconds ──────────────────────────────────────────────

def test_interval_roundtrip_named():
    for label, secs in [("15s", 15), ("1m", 60), ("5m", 300), ("1h", 3600), ("1d", 86400)]:
        assert interval_to_seconds(label) == secs
        assert seconds_to_interval(secs) == label


def test_seconds_to_interval_unknown_falls_back():
    # Arbitrary writer cadence still gets a stable label instead of crashing.
    assert seconds_to_interval(45) == "45s"


def test_interval_to_seconds_unknown_raises():
    with pytest.raises(ValueError):
        interval_to_seconds("7x")


# ── bar_upsert_params ───────────────────────────────────────────────────────

def _bar(**over):
    b = {"epoch": 1_700_000_000.0, "ts": "2023-11-14T22:13:20+00:00",
         "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 7}
    b.update(over)
    return b


def test_maps_a_full_bar():
    p = bar_upsert_params("binance", "BTC/USDT", "1m", _bar(), "live_ticks")
    assert p is not None
    assert p["venue"] == "binance" and p["symbol"] == "BTC/USDT" and p["interval"] == "1m"
    assert p["bucket_epoch"] == 1_700_000_000.0
    assert (p["open"], p["high"], p["low"], p["close"]) == (100.0, 101.0, 99.0, 100.5)
    assert p["volume"] == 7.0 and p["source"] == "live_ticks"


def test_bucket_from_iso_ts_when_no_epoch():
    bar = _bar()
    del bar["epoch"]
    p = bar_upsert_params("binance", "BTC/USDT", "1m", bar, "exchange_rest")
    assert p is not None and p["bucket_epoch"] == pytest.approx(1_700_000_000.0)


def test_bucket_from_datetime_object():
    dt = datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    p = bar_upsert_params("okx", "ETH/USDT", "5m", _bar(bucket_start=dt, epoch=None, ts=None), "exchange_rest")
    assert p is not None and p["bucket_epoch"] == pytest.approx(dt.timestamp())


def test_drops_bar_with_no_bucket():
    assert bar_upsert_params("binance", "BTC/USDT", "1m", _bar(epoch=None, ts=None, bucket_start=None), "live_ticks") is None


def test_drops_bar_with_nonpositive_or_invalid_ohlc():
    assert bar_upsert_params("binance", "BTC/USDT", "1m", _bar(low=0.0), "live_ticks") is None
    assert bar_upsert_params("binance", "BTC/USDT", "1m", _bar(close="nope"), "live_ticks") is None
    bad = _bar(); del bad["high"]
    assert bar_upsert_params("binance", "BTC/USDT", "1m", bad, "live_ticks") is None


def test_volume_defaults_to_zero_when_missing_or_bad():
    bar = _bar(); del bar["volume"]
    assert bar_upsert_params("binance", "BTC/USDT", "1m", bar, "live_ticks")["volume"] == 0.0
    assert bar_upsert_params("binance", "BTC/USDT", "1m", _bar(volume="x"), "live_ticks")["volume"] == 0.0


# ── _row_to_candle ──────────────────────────────────────────────────────────

class _Row:
    def __init__(self, epoch, o, h, l, c, v):
        self.epoch, self.open, self.high, self.low, self.close, self.volume = epoch, o, h, l, c, v


def test_row_to_candle_shape():
    cand = _row_to_candle(_Row(1_700_000_000.0, 100.0, 101.0, 99.0, 100.5, 7))
    assert cand["ts"] == 1_700_000_000_000          # ms
    assert cand["ts_dt"].startswith("2023-11-14T22:13:20")
    assert cand["mid"] == (100.0 + 100.5) / 2.0      # (open+close)/2
    assert cand["volume"] == 7.0
