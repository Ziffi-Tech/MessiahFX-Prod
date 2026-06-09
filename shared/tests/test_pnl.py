"""Tests for average-cost realized P&L accounting (mezna_shared.pnl)."""

from mezna_shared.pnl import apply_fill, PositionState, FLAT


def _step(pos, side, qty, price, fee=0.0):
    out = apply_fill(pos, side, qty, price, fee)
    return out.position, round(out.realized_pnl, 8), out.closed, out.flipped


def test_open_add_partial_full_close_matches_cashflow():
    """Sum of realized P&L over a full round trip == true net cash flow."""
    pos = FLAT
    pos, r, *_ = _step(pos, "buy", 10, 100, 1.0)
    assert r == 0.0 and pos.net_qty == 10
    pos, r, *_ = _step(pos, "buy", 10, 110, 1.0)
    assert r == 0.0 and pos.avg_price == 105 and pos.open_fees == 2.0
    pos, r1, *_ = _step(pos, "sell", 5, 120, 0.6)      # partial close
    assert r1 == 73.9
    pos, r2, closed, flipped = _step(pos, "sell", 15, 90, 1.5)   # full close
    assert closed and not flipped and pos.net_qty == 0
    # cash flow: -1000-1 -1100-1 +600-0.6 +1350-1.5 = -154.1
    assert round(r1 + r2, 4) == -154.1


def test_short_cover_for_profit():
    pos = FLAT
    pos, r, *_ = _step(pos, "sell", 10, 100, 1.0)
    assert r == 0.0 and pos.net_qty == -10
    pos, r, closed, _ = _step(pos, "buy", 10, 90, 1.0)
    assert closed and r == 98.0          # (100-90)*10 - 1 - 1


def test_flip_long_to_short():
    pos = FLAT
    pos, _, *_ = _step(pos, "buy", 10, 100, 0.0)
    pos, r, closed, flipped = _step(pos, "sell", 15, 120, 1.5)
    assert closed and flipped
    assert r == 199.0                    # +200 closing 10, minus 10/15 of the 1.5 fee
    assert pos.net_qty == -5 and pos.avg_price == 120 and pos.open_fees == 0.5


def test_pure_opens_never_realize():
    pos = FLAT
    for _ in range(3):
        pos, r, *_ = _step(pos, "buy", 1, 50, 0.1)
        assert r == 0.0
    assert pos.net_qty == 3


def test_guards_zero_qty_and_bad_price():
    assert apply_fill(FLAT, "buy", 0, 100, 1).position == FLAT
    assert apply_fill(FLAT, "buy", 5, 0, 1).position == FLAT
