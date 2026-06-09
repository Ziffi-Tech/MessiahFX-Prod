"""
Average-cost realized P&L accounting.

Pure, side-effect-free position math shared by the executor (writes realized P&L
per fill) and any service that needs to reason about round-trip outcomes.

Model
-----
A position is the signed net quantity for one (venue, symbol, strategy, paper)
key, plus the volume-weighted average ENTRY price and the entry-side fees not yet
realized. Each incoming fill either:

  * opens / adds   — same direction (or flat): VWAP the entry price, carry fees,
                     realized P&L = 0 (the entry fee is realized later, on close).
  * reduces        — opposing direction, smaller than the position: realize P&L on
                     the closed quantity, net of the closing fee and a pro-rata
                     share of the carried entry fees.
  * closes         — opposing, exactly flattens: as above, position → flat.
  * flips          — opposing, larger than the position: fully close (realize),
                     then open a new position in the fill direction with the
                     remainder (and the remaining share of the fill's fee).

realized_pnl is NET of fees (the trader-standard). Summing realized_pnl across all
fills of a fully-closed round trip equals its true net cash P&L — verified in
tests/test_pnl.py.
"""

from __future__ import annotations

from dataclasses import dataclass

# Quantities/prices are Numeric(20,8); treat anything below this as zero.
_EPS = 1e-9


@dataclass(frozen=True)
class PositionState:
    """Immutable snapshot of an open position (all 0 when flat)."""
    net_qty: float       # signed: >0 long, <0 short, 0 flat
    avg_price: float     # VWAP entry price of the currently open position
    open_fees: float     # entry-side fees carried until the position is closed


FLAT = PositionState(0.0, 0.0, 0.0)


@dataclass(frozen=True)
class FillOutcome:
    """Result of applying one fill to a position."""
    position: PositionState   # the new position state after the fill
    realized_pnl: float       # NET realized P&L produced by THIS fill (0 for opens)
    closed: bool              # True if the prior position was (fully) closed
    flipped: bool             # True if the fill reversed the position's direction


def apply_fill(
    position: PositionState,
    side: str,
    fill_qty: float,
    fill_price: float,
    fee: float = 0.0,
) -> FillOutcome:
    """
    Apply a single fill to a position using average-cost accounting.

    Args:
        position:   current PositionState (use FLAT when there is none)
        side:       "buy" or "sell"
        fill_qty:   filled quantity (positive)
        fill_price: average fill price (positive)
        fee:        fee paid on this fill, in account currency (positive)

    Returns a FillOutcome with the new position and the NET realized P&L for
    this fill. Never raises on normal inputs.
    """
    q = float(position.net_qty)
    avg = float(position.avg_price)
    of = float(position.open_fees)

    d = 1.0 if side == "buy" else -1.0
    a = abs(float(fill_qty))
    p = float(fill_price)
    f = max(float(fee or 0.0), 0.0)

    if a <= _EPS or p <= 0.0:
        # Nothing to apply (no quantity, or unusable price) — leave unchanged.
        return FillOutcome(position, 0.0, closed=False, flipped=False)

    cur = 1.0 if q > _EPS else (-1.0 if q < -_EPS else 0.0)

    # ── Opening or adding in the same direction ───────────────────────────────
    if cur == 0.0 or cur == d:
        prior = abs(q)
        new_abs = prior + a
        new_avg = (avg * prior + p * a) / new_abs
        return FillOutcome(
            PositionState(d * new_abs, new_avg, of + f),
            realized_pnl=0.0, closed=False, flipped=False,
        )

    # ── Opposing: close (and possibly flip) ───────────────────────────────────
    prior = abs(q)
    c = min(a, prior)                                  # quantity actually closed
    close_fee = f * (c / a)                            # fee for the closing portion
    entry_fee_portion = of * (c / prior) if prior > _EPS else 0.0
    gross = (p - avg) * c if q > 0 else (avg - p) * c  # long: sell high; short: buy low
    realized = gross - close_fee - entry_fee_portion

    leftover = a - c                                   # amount opening the new side

    if leftover > _EPS:
        # Flip: old position fully closed, remainder opens the opposite side.
        new_state = PositionState(d * leftover, p, f - close_fee)
        return FillOutcome(new_state, realized, closed=True, flipped=True)

    remaining = prior - c
    if remaining <= _EPS:
        # Exactly flat.
        return FillOutcome(FLAT, realized, closed=True, flipped=False)

    # Partial close: same direction, same avg price, reduced carried fees.
    new_state = PositionState(cur * remaining, avg, of - entry_fee_portion)
    return FillOutcome(new_state, realized, closed=False, flipped=False)
