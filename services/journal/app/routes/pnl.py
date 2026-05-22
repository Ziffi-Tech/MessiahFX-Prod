"""
P&L summary endpoints.

IMPORTANT — Phase 6 scope:
  Realized P&L per trade is populated only when a position closes
  (both legs of an arb are unwound). This is implemented in Phase 7
  (position management + exit signals).

  For now, these endpoints return:
    - total_fees  : known cost of trading (always accurate)
    - total_notional : total USD volume traded
    - realized_pnl   : sum of the realized_pnl column (will be 0 until Phase 7)

  This gives the operator a cost-of-trading view while P&L attribution
  is built out in Phase 7.
"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from .. import queries

router = APIRouter()


@router.get("/daily")
async def daily_pnl(
    request: Request,
    days: int = Query(30, ge=1, le=365, description="Number of calendar days to include"),
    strategy_type: str | None = Query(None, description="Filter to a single strategy"),
) -> JSONResponse:
    """
    Daily activity and P&L, grouped by strategy_type and paper_mode.

    Each row: trade_date, strategy_type, paper_mode,
              fill_count, total_notional, total_fees, realized_pnl

    Note: realized_pnl is 0 until position-close logic is implemented (Phase 7).
    Use total_fees as the primary cost metric for now.
    """
    rows = await queries.daily_pnl(
        request.app.state.db_engine, days=days, strategy_type=strategy_type
    )
    return JSONResponse(content={
        "days": days,
        "strategy_type": strategy_type,
        "rows": rows,
        "note": "realized_pnl populated in Phase 7 (position close tracking)",
    })


@router.get("/kelly-stats")
async def pnl_kelly_stats(
    request: Request,
    days: int = Query(30, ge=1, le=365, description="Lookback window in days"),
    strategy_type: str | None = Query(None, description="Filter to one strategy"),
) -> JSONResponse:
    """
    Kelly position-sizing inputs per strategy: win rate, avg win/loss, edge ratio.

    Returns exact database aggregates — no heuristics.

    IMPORTANT: Check ``realized_pnl_populated`` in the response.
    When False (Phase 6), avg_win_usd and avg_loss_usd are 0 because
    realized_pnl is not yet tracked (Phase 7 adds position-close logic).
    The ``total_filled_trades`` count is always accurate.
    """
    data = await queries.kelly_stats(
        request.app.state.db_engine, days=days, strategy_type=strategy_type
    )
    return JSONResponse(content=data)


@router.get("/summary")
async def pnl_summary(
    request: Request,
    days: int = Query(30, ge=1, le=365),
) -> JSONResponse:
    """
    Rolled-up P&L totals for the last N days.

    Suitable for the dashboard summary cards.
    """
    rows = await queries.daily_pnl(request.app.state.db_engine, days=days)

    total_fills = sum(int(r.get("fill_count", 0) or 0) for r in rows)
    total_notional = sum(float(r.get("total_notional", 0) or 0) for r in rows)
    total_fees = sum(float(r.get("total_fees", 0) or 0) for r in rows)
    total_realized_pnl = sum(float(r.get("realized_pnl", 0) or 0) for r in rows)

    return JSONResponse(content={
        "days": days,
        "total_fills": total_fills,
        "total_notional": round(total_notional, 4),
        "total_fees": round(total_fees, 6),
        "realized_pnl": round(total_realized_pnl, 6),
        "net_pnl": round(total_realized_pnl - total_fees, 6),
        "note": "realized_pnl populated in Phase 7 (position close tracking)",
    })
