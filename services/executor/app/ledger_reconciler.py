"""
Exchange-ledger reconciliation (executor).

Compares OUR open live positions (positions table) against what each venue
actually reports (ccxt fetch_positions), using the pure drift engine in
mezna_shared.reconciliation. Catches the dangerous case where our books and the
exchange disagree — before it compounds.

Split into:
  - reconcile_ledger(): pure orchestration (gating + drift) — injectable fetcher,
    so it is unit-testable without an exchange or a DB.
  - read_open_live_positions(): our side, from the positions table.
  - ccxt_fetch_positions(): the exchange side (guarded ccxt import; live only).
  - live_venues(): which venues have credentials to reconcile against.

Inert in paper mode and when no venue has credentials — same default-off posture
as the order-book feed and backfill.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable

import structlog
from sqlalchemy import text

from mezna_shared.db import get_async_session
from mezna_shared.reconciliation import compute_position_drift

log = structlog.get_logger()

FetchPositions = Callable[[str], Awaitable[list[dict]]]


def _skipped(reason: str) -> dict:
    return {
        "status": "skipped",
        "reason": reason,
        "ok": True,
        "summary": {"checked": 0, "matched": 0, "our_only": 0, "exch_only": 0, "drifted": 0},
        "drifts": [],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def reconcile_ledger(
    our_positions: list[dict],
    fetch_positions: FetchPositions,
    live_venues_list: list[str],
    *,
    paper_mode: bool,
) -> dict:
    """
    Orchestrate a ledger reconciliation. Pure over its inputs — the fetcher is
    injected — so it is fully testable. Returns the drift report (status=ok) or a
    skipped result in paper mode / with no live venues.
    """
    if paper_mode:
        return _skipped("paper mode — no live exchange ledger to reconcile")
    if not live_venues_list:
        return _skipped("no live venues configured (no API credentials)")

    theirs: list[dict] = []
    fetch_errors: dict[str, str] = {}
    for venue in live_venues_list:
        try:
            theirs.extend(await fetch_positions(venue))
        except Exception as exc:  # one venue failing must not abort the rest
            fetch_errors[venue] = str(exc)
            log.warning("ledger.fetch_failed", venue=venue, error=str(exc))

    report = compute_position_drift(our_positions, theirs)
    return {
        "status": "ok",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "live_venues": list(live_venues_list),
        "fetch_errors": fetch_errors,
        **report,
    }


def live_venues(settings) -> list[str]:
    """Venues with credentials we can reconcile against (crypto/ccxt only)."""
    venues: list[str] = []
    if settings.BINANCE_API_KEY:
        venues.append("binance")
    if settings.BYBIT_API_KEY:
        venues.append("bybit")
    if settings.OKX_API_KEY and settings.OKX_API_PASSWORD:
        venues.append("okx")
    if settings.KRAKEN_API_KEY:
        venues.append("kraken")
    return venues


_OPEN_LIVE_POSITIONS = text("""
    SELECT venue, symbol, net_qty, avg_price
    FROM positions
    WHERE status = 'open' AND paper_mode = false
""")


async def read_open_live_positions(db_engine) -> list[dict]:
    """Our open LIVE positions from the positions table."""
    async with get_async_session(db_engine) as session:
        rows = await session.execute(_OPEN_LIVE_POSITIONS)
    return [
        {"venue": r.venue, "symbol": r.symbol, "net_qty": float(r.net_qty or 0), "avg_price": float(r.avg_price or 0)}
        for r in rows
    ]


def _ccxt_credentials(venue: str, settings) -> dict | None:
    if venue == "binance":
        return {"apiKey": settings.BINANCE_API_KEY, "secret": settings.BINANCE_API_SECRET} if settings.BINANCE_API_KEY else None
    if venue == "bybit":
        return {"apiKey": settings.BYBIT_API_KEY, "secret": settings.BYBIT_API_SECRET} if settings.BYBIT_API_KEY else None
    if venue == "okx":
        if not (settings.OKX_API_KEY and settings.OKX_API_PASSWORD):
            return None
        return {"apiKey": settings.OKX_API_KEY, "secret": settings.OKX_API_SECRET, "password": settings.OKX_API_PASSWORD}
    if venue == "kraken":
        return {"apiKey": settings.KRAKEN_API_KEY, "secret": settings.KRAKEN_API_SECRET} if settings.KRAKEN_API_KEY else None
    return None


async def ccxt_fetch_positions(venue: str, settings) -> list[dict]:
    """
    Fetch live positions from a venue via ccxt and normalise to
    {venue, symbol, qty, avg_price}. Guarded import; returns [] when ccxt or the
    venue is unavailable. LIVE only — never called in paper mode.
    """
    try:
        import ccxt.async_support as ccxt  # noqa: PLC0415 (guarded, prod-only)
    except Exception:
        log.warning("ledger.ccxt_unavailable")
        return []

    creds = _ccxt_credentials(venue, settings)
    klass = getattr(ccxt, venue, None)
    if creds is None or klass is None:
        return []

    exchange = klass({**creds, "enableRateLimit": True, "options": {"defaultType": "swap"}})
    try:
        raw = await exchange.fetch_positions()
    finally:
        try:
            await exchange.close()
        except Exception:
            pass

    out: list[dict] = []
    for p in raw or []:
        contracts = p.get("contracts")
        if contracts in (None, 0, 0.0):
            continue
        side = (p.get("side") or "").lower()
        qty = float(contracts)
        if side == "short":
            qty = -qty
        out.append({
            "venue": venue,
            "symbol": p.get("symbol"),
            "qty": qty,
            "avg_price": float(p.get("entryPrice") or 0),
        })
    return out
