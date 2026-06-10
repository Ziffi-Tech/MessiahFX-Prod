"""Tests for the pure performance/TCA helpers in app.queries."""

from app.queries import curve_performance, cost_bps, _daily_series, _sortino, align_daily_returns


def _rows(*pairs):
    return [{"trade_date": d, "realized_pnl": p} for d, p in pairs]


def test_daily_series_aggregates_by_date():
    rows = _rows(("a", 5), ("a", 5), ("b", -3))
    assert _daily_series(rows) == [10.0, -3.0]


def test_curve_performance_drawdown_and_ratios():
    # +10, +10, -8 → peak 20, trough 12 → 40% drawdown; has downside → sortino set
    perf = curve_performance(_rows(("a", 10), ("b", 10), ("c", -8)))
    assert perf["max_drawdown_pct"] == 40.0
    assert perf["sharpe_ratio"] is not None
    assert perf["sortino_ratio"] is not None


def test_curve_performance_all_positive_no_sortino():
    perf = curve_performance(_rows(("a", 5), ("b", 7), ("c", 3)))
    assert perf["max_drawdown_pct"] == 0.0
    assert perf["sharpe_ratio"] is not None
    assert perf["sortino_ratio"] is None  # no downside deviation


def test_curve_performance_empty():
    perf = curve_performance([])
    assert perf["max_drawdown_pct"] == 0.0
    assert perf["sharpe_ratio"] is None
    assert perf["sortino_ratio"] is None


def test_sortino_single_point_none():
    assert _sortino([1.0]) is None


def test_cost_bps():
    assert cost_bps(10.0, 100_000.0) == 1.0   # $10 fee on $100k = 1 bp
    assert cost_bps(0.0, 100.0) == 0.0
    assert cost_bps(5.0, 0.0) == 0.0           # no notional → 0, not div-by-zero


def test_align_daily_returns_fills_zero():
    rows = [
        {"trade_date": "d1", "strategy_type": "a", "realized_pnl": 10},
        {"trade_date": "d2", "strategy_type": "a", "realized_pnl": -5},
        {"trade_date": "d1", "strategy_type": "b", "realized_pnl": 3},
        {"trade_date": "d3", "strategy_type": "b", "realized_pnl": 7},
    ]
    s = align_daily_returns(rows)
    # date axis = d1,d2,d3; non-trading days filled with 0
    assert s["a"] == [10.0, -5.0, 0.0]
    assert s["b"] == [3.0, 0.0, 7.0]


def test_align_daily_returns_sums_same_date():
    rows = [
        {"trade_date": "d1", "strategy_type": "a", "realized_pnl": 4},
        {"trade_date": "d1", "strategy_type": "a", "realized_pnl": 6},
    ]
    assert align_daily_returns(rows)["a"] == [10.0]
