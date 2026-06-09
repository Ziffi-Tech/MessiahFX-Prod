"""Tests for the ccxt OHLCV row mapping (app.backfill.ccxt_rows_to_bars).

The async backfill loop and exchange construction are exercised against a live
exchange; here we lock down the pure row → bar-dict mapping, including the drop
conditions, since that feeds straight into the DB upsert.
"""

from app.backfill import ccxt_rows_to_bars, CCXT_VENUES


def test_maps_ccxt_rows():
    # ccxt fetch_ohlcv: [ts_ms, open, high, low, close, volume]
    rows = [
        [1_700_000_000_000, 100.0, 101.0, 99.0, 100.5, 12.5],
        [1_700_000_060_000, 100.5, 102.0, 100.0, 101.5, 8.0],
    ]
    bars = ccxt_rows_to_bars(rows)
    assert len(bars) == 2
    assert bars[0]["epoch"] == 1_700_000_000.0       # ms → s
    assert bars[0]["ts"].startswith("2023-11-14T22:13:20")
    assert (bars[0]["open"], bars[0]["high"], bars[0]["low"], bars[0]["close"]) == (100.0, 101.0, 99.0, 100.5)
    assert bars[0]["volume"] == 12.5
    assert bars[1]["epoch"] == 1_700_000_060.0


def test_volume_optional():
    bars = ccxt_rows_to_bars([[1_700_000_000_000, 100.0, 101.0, 99.0, 100.5]])
    assert len(bars) == 1 and bars[0]["volume"] == 0.0


def test_drops_malformed_and_nonpositive():
    rows = [
        [1_700_000_000_000, 100.0, 101.0, 99.0, 100.5, 1.0],  # good
        [1_700_000_060_000, 0.0, 101.0, 99.0, 100.5, 1.0],    # non-positive open
        ["bad", 1, 2, 3, 4, 5],                               # bad ts
        [1_700_000_120_000, 100.0],                           # too short
    ]
    bars = ccxt_rows_to_bars(rows)
    assert len(bars) == 1 and bars[0]["epoch"] == 1_700_000_000.0


def test_supported_venues():
    assert CCXT_VENUES == {"binance", "bybit", "okx", "kraken"}
