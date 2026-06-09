"""Tests for mezna_shared.reconciliation.compute_position_drift."""

from mezna_shared.reconciliation import compute_position_drift


def _ours(qty, px, venue="binance", symbol="BTC/USDT"):
    return {"venue": venue, "symbol": symbol, "net_qty": qty, "avg_price": px}


def _theirs(qty, px, venue="binance", symbol="BTC/USDT"):
    return {"venue": venue, "symbol": symbol, "qty": qty, "avg_price": px}


def test_matched_no_drift():
    r = compute_position_drift([_ours(1.0, 100.0)], [_theirs(1.0, 100.0)])
    assert r["ok"] is True
    assert r["summary"] == {"checked": 1, "matched": 1, "our_only": 0, "exch_only": 0, "drifted": 0}
    assert r["drifts"] == []


def test_qty_mismatch_flagged():
    r = compute_position_drift([_ours(1.0, 100.0)], [_theirs(0.5, 100.0)])
    assert r["ok"] is False
    assert r["summary"]["drifted"] == 1
    d = r["drifts"][0]
    assert d["type"] == "mismatch"
    assert d["qty_diff"] == 0.5


def test_price_drift_beyond_bps():
    # 100 -> 100.5 is 50 bps, above the 10 bps default tolerance.
    r = compute_position_drift([_ours(1.0, 100.5)], [_theirs(1.0, 100.0)])
    assert r["ok"] is False
    assert r["drifts"][0]["price_diff_bps"] == 50.0


def test_price_within_bps_ok():
    # 100 -> 100.05 is 5 bps, within tolerance.
    r = compute_position_drift([_ours(1.0, 100.05)], [_theirs(1.0, 100.0)])
    assert r["ok"] is True


def test_our_only_nonflat_is_drift():
    r = compute_position_drift([_ours(1.0, 100.0)], [])
    assert r["ok"] is False
    assert r["summary"]["our_only"] == 1
    assert r["drifts"][0]["type"] == "our_only"


def test_our_only_flat_not_drift():
    r = compute_position_drift([_ours(0.0, 0.0)], [])
    assert r["ok"] is True
    assert r["summary"]["our_only"] == 1
    assert r["drifts"] == []


def test_exch_only_untracked_is_drift():
    # The dangerous case: the exchange holds a position we are not tracking.
    r = compute_position_drift([], [_theirs(2.0, 100.0)])
    assert r["ok"] is False
    assert r["summary"]["exch_only"] == 1
    assert r["drifts"][0]["type"] == "exch_only"
    assert r["drifts"][0]["qty_diff"] == -2.0


def test_qty_tolerance_respected():
    r = compute_position_drift([_ours(1.0000000001, 100.0)], [_theirs(1.0, 100.0)], qty_tolerance=1e-6)
    assert r["ok"] is True


def test_multiple_symbols_summary():
    ours = [_ours(1.0, 100.0, symbol="BTC/USDT"), _ours(5.0, 10.0, symbol="ETH/USDT")]
    theirs = [_theirs(1.0, 100.0, symbol="BTC/USDT"), _theirs(4.0, 10.0, symbol="ETH/USDT")]
    r = compute_position_drift(ours, theirs)
    assert r["summary"]["matched"] == 2
    assert r["summary"]["drifted"] == 1  # only ETH differs
