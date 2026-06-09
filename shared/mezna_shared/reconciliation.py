"""
Exchange-ledger reconciliation — compare OUR recorded positions to the venue's.

Pure, dependency-free, and tested. Callers (the executor's ledger reconciler)
supply two position lists; this computes per-(venue,symbol) drift and a summary
so a mismatch — the dangerous case where our books and the exchange disagree —
is detected and can be alerted on before it compounds.

Position shapes (only the listed keys are read; extras ignored):
  ours:   {"venue", "symbol", "net_qty", "avg_price"}     (from the positions table)
  theirs: {"venue", "symbol", "qty",     "avg_price"}     (from ccxt fetch_positions)
"""

from __future__ import annotations

from typing import Any


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def compute_position_drift(
    ours: list[dict],
    theirs: list[dict],
    *,
    qty_tolerance: float = 1e-8,
    price_tolerance_bps: float = 10.0,
) -> dict:
    """
    Return a reconciliation report:

      {
        "ok": bool,                       # True when no drift
        "summary": {checked, matched, our_only, exch_only, drifted},
        "drifts": [ {venue, symbol, type, our_qty, exch_qty, qty_diff,
                     our_avg_price, exch_avg_price, price_diff_bps} ],
        "tolerances": {qty, price_bps},
      }

    Drift types:
      - "mismatch":  present both sides, qty or avg-price differs beyond tolerance
      - "our_only":  we hold a non-flat position the exchange does not report
      - "exch_only": the exchange reports a position we are not tracking
    """
    our_map = {(p.get("venue"), p.get("symbol")): p for p in ours}
    their_map = {(p.get("venue"), p.get("symbol")): p for p in theirs}
    keys = set(our_map) | set(their_map)

    drifts: list[dict] = []
    matched = our_only = exch_only = drifted = 0

    for venue, symbol in sorted(keys, key=lambda k: (str(k[0]), str(k[1]))):
        o = our_map.get((venue, symbol))
        t = their_map.get((venue, symbol))

        if o is not None and t is None:
            our_only += 1
            our_qty = _f(o.get("net_qty"))
            if abs(our_qty) > qty_tolerance:
                drifted += 1
                drifts.append({
                    "venue": venue, "symbol": symbol, "type": "our_only",
                    "our_qty": our_qty, "exch_qty": 0.0, "qty_diff": our_qty,
                    "our_avg_price": _f(o.get("avg_price")), "exch_avg_price": None,
                    "price_diff_bps": None,
                })
            continue

        if t is not None and o is None:
            exch_only += 1
            exch_qty = _f(t.get("qty"))
            if abs(exch_qty) > qty_tolerance:
                drifted += 1
                drifts.append({
                    "venue": venue, "symbol": symbol, "type": "exch_only",
                    "our_qty": 0.0, "exch_qty": exch_qty, "qty_diff": -exch_qty,
                    "our_avg_price": None, "exch_avg_price": _f(t.get("avg_price")),
                    "price_diff_bps": None,
                })
            continue

        matched += 1
        our_qty = _f(o.get("net_qty"))
        exch_qty = _f(t.get("qty"))
        our_px = _f(o.get("avg_price"))
        exch_px = _f(t.get("avg_price"))
        qty_diff = our_qty - exch_qty
        price_diff_bps = ((our_px - exch_px) / exch_px * 10_000.0) if exch_px else 0.0

        if abs(qty_diff) > qty_tolerance or abs(price_diff_bps) > price_tolerance_bps:
            drifted += 1
            drifts.append({
                "venue": venue, "symbol": symbol, "type": "mismatch",
                "our_qty": our_qty, "exch_qty": exch_qty, "qty_diff": qty_diff,
                "our_avg_price": our_px, "exch_avg_price": exch_px,
                "price_diff_bps": round(price_diff_bps, 4),
            })

    return {
        "ok": drifted == 0,
        "summary": {
            "checked": len(keys),
            "matched": matched,
            "our_only": our_only,
            "exch_only": exch_only,
            "drifted": drifted,
        },
        "drifts": drifts,
        "tolerances": {"qty": qty_tolerance, "price_bps": price_tolerance_bps},
    }
