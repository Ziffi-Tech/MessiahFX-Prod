"""
Tests for regime-conditional backtesting helpers.

Covers:
  - _to_epoch_ms: normalises int/float/ISO-string timestamps
  - _rolling_realised_vol: window behaviour, NaN sentinels
  - _classify_trades_by_vol: tercile assignment, timestamp format robustness,
      fallback when no trades can be matched, unmatched-trade flag

No HTTP, no Binance API, no database — pure unit tests.
"""

import math
import sys
import os
import pytest

# ── Service isolation ─────────────────────────────────────────────────────────
# All three services share the top-level package name "app".  When pytest runs
# the full suite from the repo root, the ai-filter tests are collected first and
# cache `sys.modules["app"]` pointing at ai-filter's package.  Clear those
# entries so that the import below loads backtest's own `app` package fresh.
for _k in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
    del sys.modules[_k]

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.routes.regime_backtest import (
    _to_epoch_ms,
    _rolling_realised_vol,
    _classify_trades_by_vol,
    _aggregate,
)


# ── _to_epoch_ms ──────────────────────────────────────────────────────────────

class TestToEpochMs:
    """Every timestamp format we might encounter must normalise correctly."""

    # Reference: 2024-01-15 12:00:00 UTC
    # Verified: datetime(2024,1,15,12,0,0,tzinfo=timezone.utc).timestamp() == 1705320000.0
    _REF_S  = 1_705_320_000
    _REF_MS = 1_705_320_000_000
    _REF_ISO      = "2024-01-15T12:00:00+00:00"
    _REF_ISO_Z    = "2024-01-15T12:00:00Z"
    _REF_ISO_BARE = "2024-01-15T12:00:00"  # no tz info — treated as local/UTC

    def test_epoch_ms_int(self):
        assert _to_epoch_ms(self._REF_MS) == self._REF_MS

    def test_epoch_s_int_promoted(self):
        """10-digit int (epoch-s) is multiplied by 1000."""
        result = _to_epoch_ms(self._REF_S)
        assert result == self._REF_MS

    def test_epoch_ms_float(self):
        assert _to_epoch_ms(float(self._REF_MS)) == self._REF_MS

    def test_iso_with_offset(self):
        assert _to_epoch_ms(self._REF_ISO) == self._REF_MS

    def test_iso_with_z(self):
        assert _to_epoch_ms(self._REF_ISO_Z) == self._REF_MS

    def test_none_returns_none(self):
        assert _to_epoch_ms(None) is None

    def test_empty_string_returns_none(self):
        assert _to_epoch_ms("") is None

    def test_garbage_string_returns_none(self):
        assert _to_epoch_ms("not-a-timestamp") is None

    def test_zero_int(self):
        # 0 epoch-s → 0 ms (epoch origin)
        result = _to_epoch_ms(0)
        assert result == 0

    def test_large_ms_not_multiplied(self):
        """13-digit value must NOT be doubled."""
        big = 1_705_316_400_123
        assert _to_epoch_ms(big) == big

    @pytest.mark.parametrize("raw,expected_ms", [
        (1_705_320_000,     1_705_320_000_000),   # epoch-s int
        (1_705_320_000_000, 1_705_320_000_000),   # epoch-ms int
        ("2024-01-15T12:00:00Z", 1_705_320_000_000),
        ("2024-01-15T12:00:00+00:00", 1_705_320_000_000),
    ])
    def test_parametrize(self, raw, expected_ms):
        assert _to_epoch_ms(raw) == expected_ms


# ── _rolling_realised_vol ─────────────────────────────────────────────────────

class TestRollingRealisedVol:
    def _candles(self, closes: list[float]) -> list[dict]:
        return [{"close": c, "open_time": i * 60_000} for i, c in enumerate(closes)]

    def test_first_window_minus_one_are_nan(self):
        candles = self._candles([100.0] * 30)
        vols = _rolling_realised_vol(candles, window=10)
        for v in vols[:10]:
            assert math.isnan(v)

    def test_values_after_window_are_not_nan(self):
        candles = self._candles([100.0 + i * 0.5 for i in range(30)])
        vols = _rolling_realised_vol(candles, window=5)
        for v in vols[5:]:
            assert not math.isnan(v)

    def test_flat_price_vol_is_zero(self):
        """Constant price → zero log returns → zero vol."""
        candles = self._candles([100.0] * 50)
        vols = _rolling_realised_vol(candles, window=10)
        for v in vols[10:]:
            assert v == pytest.approx(0.0, abs=1e-10)

    def test_output_length_matches_input(self):
        candles = self._candles([100.0 + i for i in range(25)])
        vols = _rolling_realised_vol(candles, window=10)
        assert len(vols) == len(candles)

    def test_higher_volatility_gives_larger_vol(self):
        """Noisy prices → higher vol than smooth prices."""
        import random
        random.seed(42)
        smooth = self._candles([100 + i * 0.01 for i in range(50)])
        noisy  = self._candles([100 + i * 0.01 + random.gauss(0, 2) for i in range(50)])
        vol_s  = [v for v in _rolling_realised_vol(smooth, 10) if not math.isnan(v)]
        vol_n  = [v for v in _rolling_realised_vol(noisy,  10) if not math.isnan(v)]
        assert sum(vol_n) > sum(vol_s)

    def test_single_candle_all_nan(self):
        candles = self._candles([100.0])
        vols = _rolling_realised_vol(candles, window=5)
        assert len(vols) == 1
        assert math.isnan(vols[0])

    def test_empty_candles(self):
        vols = _rolling_realised_vol([], window=10)
        assert vols == []


# ── _classify_trades_by_vol ───────────────────────────────────────────────────

class TestClassifyTradesByVol:
    """The critical test class — covers all timestamp format combinations."""

    # 30 candles with realistic prices (slightly trending)
    _CLOSES = [100.0 + i * 0.3 + (i % 5) * 0.5 for i in range(30)]
    _WINDOW = 5

    def _make_candles(self, ts_format: str = "epoch_ms") -> list[dict]:
        candles = []
        for i, c in enumerate(self._CLOSES):
            ts_ms = 1_705_316_400_000 + i * 60_000  # 1-minute candles
            if ts_format == "epoch_ms":
                ot = ts_ms
            elif ts_format == "epoch_s":
                ot = ts_ms // 1000
            elif ts_format == "iso_z":
                from datetime import datetime, timezone
                ot = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            elif ts_format == "iso_offset":
                from datetime import datetime, timezone
                ot = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
            else:
                ot = ts_ms
            candles.append({"close": c, "open_time": ot})
        return candles

    def _make_trades(self, candles: list[dict], ts_format: str = "epoch_ms") -> list[dict]:
        """Create 9 trades spread across the candle range."""
        indices = [6, 8, 10, 14, 16, 18, 22, 24, 26]
        trades = []
        for idx in indices:
            c = candles[idx]
            ot = c["open_time"]
            if ts_format == "epoch_ms":
                if isinstance(ot, str):
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(ot.replace("Z", "+00:00"))
                    entry_ts = int(dt.timestamp() * 1000)
                else:
                    entry_ts = int(ot)
            elif ts_format == "epoch_s":
                raw = _to_epoch_ms(ot)
                entry_ts = raw // 1000 if raw else 0
            elif ts_format in ("iso_z", "iso_offset"):
                from datetime import datetime, timezone
                raw = _to_epoch_ms(ot)
                entry_ts = datetime.fromtimestamp(raw / 1000, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            else:
                entry_ts = ot
            trades.append({
                "entry_ts": entry_ts,
                "net_pnl_usd": 5.0 if idx % 3 == 0 else -2.5,
                "symbol": "BTCUSDT",
            })
        return trades

    def _run(self, candle_fmt: str, trade_fmt: str) -> dict[str, list]:
        candles = self._make_candles(candle_fmt)
        vols = _rolling_realised_vol(candles, self._WINDOW)
        trades = self._make_trades(candles, trade_fmt)
        return _classify_trades_by_vol(trades, candles, vols)

    # ── Format compatibility matrix ───────────────────────────────────────────

    @pytest.mark.parametrize("candle_fmt,trade_fmt", [
        ("epoch_ms", "epoch_ms"),
        ("epoch_ms", "epoch_s"),
        ("epoch_ms", "iso_z"),
        ("epoch_s",  "epoch_ms"),
        ("epoch_s",  "epoch_s"),
        ("iso_z",    "epoch_ms"),
        ("iso_z",    "iso_z"),
        ("iso_offset", "iso_offset"),
    ])
    def test_all_trades_classified_regardless_of_format(self, candle_fmt, trade_fmt):
        """
        The total number of classified trades must equal the input trade count
        for every combination of candle / trade timestamp formats.
        """
        result = self._run(candle_fmt, trade_fmt)
        total = sum(len(v) for v in result.values())
        assert total == 9, (
            f"candle_fmt={candle_fmt}, trade_fmt={trade_fmt}: "
            f"Expected 9 trades classified, got {total}"
        )

    def test_no_trades_silently_lost(self):
        """No trade should disappear from low+mid+high."""
        result = self._run("epoch_ms", "epoch_ms")
        assert len(result["low_vol"]) + len(result["mid_vol"]) + len(result["high_vol"]) == 9

    def test_terciles_non_empty(self):
        """With 9 evenly spaced trades and real vol variation, all terciles should have entries."""
        result = self._run("epoch_ms", "epoch_ms")
        # Each tercile should contain at least one trade
        assert len(result["low_vol"]) >= 1
        assert len(result["mid_vol"]) >= 1
        assert len(result["high_vol"]) >= 1

    def test_empty_trade_log_returns_empty_dict(self):
        candles = self._make_candles("epoch_ms")
        vols = _rolling_realised_vol(candles, self._WINDOW)
        result = _classify_trades_by_vol([], candles, vols)
        assert result == {"low_vol": [], "mid_vol": [], "high_vol": []}

    def test_single_trade_goes_somewhere(self):
        candles = self._make_candles("epoch_ms")
        vols = _rolling_realised_vol(candles, self._WINDOW)
        trade = {"entry_ts": 1_705_316_400_000 + 10 * 60_000, "net_pnl_usd": 5.0}
        result = _classify_trades_by_vol([trade], candles, vols)
        total = sum(len(v) for v in result.values())
        assert total == 1

    def test_garbage_timestamps_fallback_to_mid_vol(self):
        """
        If all entry_ts values are unparseable, the function must NOT crash —
        it should fall back to mid_vol with a warning.
        """
        candles = self._make_candles("epoch_ms")
        vols = _rolling_realised_vol(candles, self._WINDOW)
        bad_trades = [
            {"entry_ts": "not-a-date", "net_pnl_usd": 1.0},
            {"entry_ts": None, "net_pnl_usd": 2.0},
            {"entry_ts": "", "net_pnl_usd": 3.0},
        ]
        result = _classify_trades_by_vol(bad_trades, candles, vols)
        total = sum(len(v) for v in result.values())
        assert total == 3
        assert len(result["low_vol"]) == 0
        assert len(result["mid_vol"]) == 3  # fallback

    def test_unmatched_trades_flagged(self):
        """
        Trades that cannot be matched to a candle (entry before all candles)
        must be placed in mid_vol with _vol_unmatched=True, not silently dropped.
        """
        candles = self._make_candles("epoch_ms")
        vols = _rolling_realised_vol(candles, self._WINDOW)
        # This trade's timestamp is before the first candle
        early_trade = {"entry_ts": 0, "net_pnl_usd": 5.0}
        normal_trades = self._make_trades(candles, "epoch_ms")
        all_trades = normal_trades + [early_trade]
        result = _classify_trades_by_vol(all_trades, candles, vols)
        total = sum(len(v) for v in result.values())
        assert total == len(all_trades)

    def test_constant_vol_all_same_tercile(self):
        """
        Flat prices → zero vol everywhere → q33 == q67 == 0
        → all trades land in low_vol (vol <= q33 = 0).
        """
        n = 30
        candles = [{"close": 100.0, "open_time": 1_705_316_400_000 + i * 60_000} for i in range(n)]
        vols = _rolling_realised_vol(candles, window=5)
        trades = [
            {"entry_ts": 1_705_316_400_000 + i * 60_000, "net_pnl_usd": 1.0}
            for i in range(6, 25, 3)
        ]
        result = _classify_trades_by_vol(trades, candles, vols)
        total = sum(len(v) for v in result.values())
        assert total == len(trades)


# ── _aggregate ────────────────────────────────────────────────────────────────

class TestAggregate:
    def test_empty_returns_zeros(self):
        r = _aggregate([], capital_usd=5000.0)
        assert r["count"] == 0
        assert r["win_rate"] == 0.0
        assert r["net_pnl_usd"] == 0.0

    def test_all_winning(self):
        trades = [{"net_pnl_usd": 10.0}, {"net_pnl_usd": 5.0}, {"net_pnl_usd": 3.0}]
        r = _aggregate(trades, capital_usd=1000.0)
        assert r["count"] == 3
        assert r["win_rate"] == 1.0
        assert r["net_pnl_usd"] == pytest.approx(18.0)

    def test_all_losing(self):
        trades = [{"net_pnl_usd": -5.0}, {"net_pnl_usd": -3.0}]
        r = _aggregate(trades, capital_usd=1000.0)
        assert r["win_rate"] == 0.0
        assert r["net_pnl_usd"] == pytest.approx(-8.0)

    def test_mixed(self):
        trades = [
            {"net_pnl_usd": 10.0},
            {"net_pnl_usd": -5.0},
            {"net_pnl_usd": 3.0},
            {"net_pnl_usd": -1.0},
        ]
        r = _aggregate(trades, capital_usd=1000.0)
        assert r["count"] == 4
        assert r["win_rate"] == pytest.approx(0.5)
        assert r["net_pnl_usd"] == pytest.approx(7.0)

    def test_total_return_pct(self):
        trades = [{"net_pnl_usd": 100.0}]
        r = _aggregate(trades, capital_usd=1000.0)
        assert r["total_return_pct"] == pytest.approx(10.0)

    def test_zero_capital_does_not_raise(self):
        trades = [{"net_pnl_usd": 5.0}]
        r = _aggregate(trades, capital_usd=0.0)
        assert r["total_return_pct"] == 0.0
