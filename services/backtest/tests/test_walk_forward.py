"""Tests for the pure walk-forward helpers (window split + summary)."""

import pytest

from app.walk_forward import wfa_windows, summarize_walk_forward


def test_wfa_windows_basic():
    # n=1000, IS=500, OOS=150, step=150 → is_start ∈ {0,150,300}
    w = wfa_windows(1000, 500, 150, 150)
    assert w == [
        (0, 500, 500, 650),
        (150, 650, 650, 800),
        (300, 800, 800, 950),
    ]


def test_wfa_windows_too_short_is_empty():
    assert wfa_windows(500, 500, 150, 150) == []  # not even one IS+OOS fits


def test_wfa_windows_validates_positive():
    for bad in [(1000, 0, 150, 150), (1000, 500, 0, 150), (1000, 500, 150, 0)]:
        with pytest.raises(ValueError):
            wfa_windows(*bad)


def _fold(is_s, oos_s, pnl, trades, window=100, entry_z=2.0):
    return {
        "is_sharpe": is_s, "oos_sharpe": oos_s, "oos_net_pnl": pnl, "oos_trades": trades,
        "params": {"window": window, "entry_z": entry_z},
    }


def test_summary_empty_is_insufficient():
    s = summarize_walk_forward([])
    assert s["verdict"] == "insufficient_data"
    assert s["folds"] == 0


def test_summary_robust():
    folds = [
        _fold(1.2, 1.0, 100, 5, window=100, entry_z=2.0),
        _fold(1.1, 0.9, 80, 4, window=100, entry_z=2.0),
        _fold(1.3, 1.1, 120, 6, window=150, entry_z=2.5),
    ]
    s = summarize_walk_forward(folds)
    assert s["verdict"] == "robust"
    assert s["positive_fold_fraction"] == 1.0
    assert s["walk_forward_efficiency"] is not None and s["walk_forward_efficiency"] >= 0.5
    # window is stable (100 in 2/3 folds)
    assert s["parameter_stability"]["window"]["mode"] == 100
    assert s["parameter_stability"]["window"]["mode_fraction"] == round(2 / 3, 4)


def test_summary_overfit():
    folds = [
        _fold(2.0, -0.5, -50, 3, window=50, entry_z=1.5),
        _fold(2.2, -0.2, -30, 2, window=150, entry_z=3.0),
        _fold(1.9, 0.1, 10, 1, window=75, entry_z=2.0),
    ]
    s = summarize_walk_forward(folds)
    assert s["verdict"] == "overfit"
    assert s["median_oos_sharpe"] <= 0
    # parameters are unstable — all distinct
    assert s["parameter_stability"]["window"]["distinct"] == 3
