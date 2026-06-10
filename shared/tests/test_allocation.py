"""Tests for mezna_shared.allocation."""

import math

from mezna_shared.allocation import allocate, _invert, _cov_matrix


def _approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_invert_2x2():
    m = [[4.0, 7.0], [2.0, 6.0]]  # det = 10
    inv = _invert(m)
    assert inv is not None
    # [[0.6,-0.7],[-0.2,0.4]]
    assert _approx(inv[0][0], 0.6) and _approx(inv[0][1], -0.7)
    assert _approx(inv[1][0], -0.2) and _approx(inv[1][1], 0.4)


def test_invert_singular_is_none():
    assert _invert([[1.0, 2.0], [2.0, 4.0]]) is None


def test_equal_weight():
    series = {"a": [1, -1, 1, -1], "b": [2, -2, 2, -2], "c": [1, 0, -1, 0]}
    r = allocate(series, method="equal_weight", capital=3000)
    for n in ("a", "b", "c"):
        assert _approx(r["weights"][n], 1 / 3)
    assert _approx(sum(r["weights"].values()), 1.0)


def test_inverse_vol_downweights_volatile():
    # b is exactly 2x as volatile as a → wA = 2/3, wB = 1/3
    series = {"a": [1, -1, 1, -1], "b": [2, -2, 2, -2]}
    r = allocate(series, method="inverse_vol", capital=0)
    assert _approx(r["weights"]["a"], 2 / 3, tol=1e-4)
    assert _approx(r["weights"]["b"], 1 / 3, tol=1e-4)


def test_weights_sum_to_one_all_methods():
    series = {
        "a": [1, -1, 2, -2, 1, -1],
        "b": [-1, 1, 1, -1, 2, -2],
        "c": [0.5, -0.5, 1, -1, 0.5, -0.5],
    }
    for method in ("inverse_vol", "risk_parity", "max_sharpe", "equal_weight"):
        r = allocate(series, method=method, capital=10_000)
        assert _approx(sum(r["weights"].values()), 1.0), method
        assert all(w >= -1e-9 for w in r["weights"].values()), method  # long-only


def test_capital_split_matches_weights():
    series = {"a": [1, -1, 1, -1], "b": [2, -2, 2, -2]}
    r = allocate(series, method="inverse_vol", capital=6000)
    by_name = {s["strategy_type"]: s for s in r["strategies"]}
    assert _approx(by_name["a"]["capital"], round(r["weights"]["a"] * 6000, 2), tol=0.01)


def test_unusable_strategy_gets_zero_weight():
    # 'flat' never trades (all zeros → zero variance) → excluded.
    series = {"a": [1, -1, 1, -1], "flat": [0, 0, 0, 0]}
    r = allocate(series, method="risk_parity", capital=1000)
    assert r["weights"]["flat"] == 0.0
    assert _approx(r["weights"]["a"], 1.0)
    assert r["usable_count"] == 1


def test_risk_parity_equalizes_risk_contributions():
    series = {
        "a": [1, -1, 2, -2, 1, -1, 1, -1],
        "b": [-1, 1, 1, -1, 2, -2, -1, 1],
    }
    r = allocate(series, method="risk_parity", capital=0)
    rcs = [s["risk_contribution"] for s in r["strategies"] if s["risk_contribution"] is not None]
    assert len(rcs) == 2
    # equal risk contribution → both ≈ 0.5
    assert all(_approx(rc, 0.5, tol=0.05) for rc in rcs)
