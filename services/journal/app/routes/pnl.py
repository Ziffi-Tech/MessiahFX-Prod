"""
P&L summary + positions endpoints.

Realized P&L per fill is populated by the executor using average-cost accounting
(mezna_shared.pnl): a fill that reduces or closes a position writes its NET (fee-
inclusive) realized P&L to trades.realized_pnl. realized_pnl therefore reads 0
until the first position closes, then reflects true round-trip P&L.

These endpoints return:
  - realized_pnl    : sum of trades.realized_pnl — NET of fees already
  - total_fees      : gross fees paid (informational; includes still-open
                      positions whose entry fees are not yet in realized_pnl)
  - total_notional  : total USD volume traded
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
        "note": "realized_pnl is net of fees; 0 for a strategy until its first position close",
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

    IMPORTANT: Check ``realized_pnl_populated`` in the response. It is False only
    until the first position closes; while False, avg_win_usd / avg_loss_usd are 0
    and must not be used for Kelly. ``total_filled_trades`` is always accurate.
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
    engine = request.app.state.db_engine
    rows = await queries.daily_pnl(engine, days=days)

    total_fills = sum(int(r.get("fill_count", 0) or 0) for r in rows)
    total_notional = sum(float(r.get("total_notional", 0) or 0) for r in rows)
    total_fees = sum(float(r.get("total_fees", 0) or 0) for r in rows)
    total_realized_pnl = sum(float(r.get("realized_pnl", 0) or 0) for r in rows)

    # Trade-level win/loss stats (exact) reuse the Kelly aggregate; a "win"/"loss"
    # is a fill with realized_pnl >/< 0. Curve metrics come from the daily series.
    stats = await queries.kelly_stats(engine, days=days)
    wins = int(stats.get("winning_trades", 0) or 0)
    losses = int(stats.get("losing_trades", 0) or 0)
    avg_win = float(stats.get("avg_win_usd", 0) or 0)
    avg_loss = float(stats.get("avg_loss_usd", 0) or 0)

    gross_profit = avg_win * wins
    gross_loss = avg_loss * losses
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None

    max_drawdown_pct, sharpe_ratio = queries.summary_curve_metrics(rows)

    return JSONResponse(content={
        "days": days,
        "total_fills": total_fills,
        "total_notional": round(total_notional, 4),
        "total_fees": round(total_fees, 6),
        # realized_pnl is already NET of fees (average-cost accounting), so it IS
        # the net P&L of closed round trips. total_fees is reported separately as a
        # gross cost metric and must NOT be subtracted again.
        "realized_pnl": round(total_realized_pnl, 6),
        "net_pnl": round(total_realized_pnl, 6),
        "total_fees_gross": round(total_fees, 6),
        # ── Performance stats for the dashboard summary cards ──────────────────
        "winning_trades": wins,
        "losing_trades": losses,
        "win_rate": float(stats.get("win_rate", 0.0) or 0.0),   # fraction 0..1
        "average_win": round(avg_win, 6),
        "average_loss": round(avg_loss, 6),
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe_ratio": sharpe_ratio,
        "realized_pnl_populated": bool(stats.get("realized_pnl_populated", False)),
    })


@router.get("/by-strategy")
async def pnl_by_strategy(
    request: Request,
    days: int = Query(30, ge=1, le=365),
) -> JSONResponse:
    """Per-strategy performance review (win/loss + Sharpe/Sortino/max drawdown)."""
    data = await queries.performance_by_strategy(request.app.state.db_engine, days=days)
    return JSONResponse(content=data)


@router.get("/allocation")
async def pnl_allocation(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    method: str = Query("risk_parity", description="equal_weight | inverse_vol | risk_parity | max_sharpe"),
    capital: float = Query(0.0, ge=0, description="Total capital to split across strategies"),
) -> JSONResponse:
    """Capital allocation across strategies from their date-aligned return series."""
    from mezna_shared.allocation import allocate

    rows = await queries.daily_pnl(request.app.state.db_engine, days=days)
    series = queries.align_daily_returns(rows)
    result = allocate(series, method=method, capital=capital)
    return JSONResponse(content={"days": days, **result})


@router.get("/tca")
async def pnl_tca(
    request: Request,
    days: int = Query(30, ge=1, le=365),
) -> JSONResponse:
    """Transaction-cost analysis — realised fees + slippage per (strategy, venue)."""
    data = await queries.tca_report(request.app.state.db_engine, days=days)
    return JSONResponse(content=data)


@router.get("/positions")
async def pnl_positions(
    request: Request,
    status: str | None = Query("open", description="Filter by status: open | flat | (omit for all)"),
    strategy_type: str | None = Query(None, description="Filter to one strategy"),
    paper_mode: bool | None = Query(None, description="Filter by paper/live"),
    limit: int = Query(100, ge=1, le=500),
) -> JSONResponse:
    """
    Net positions per (venue, symbol, strategy, paper_mode).

    Each row carries the signed net_qty, VWAP avg_price, carried open_fees and the
    cumulative (net) realized_pnl for that key. Pass status='' (empty) to include
    flat/closed positions.
    """
    rows = await queries.list_positions(
        request.app.state.db_engine,
        status=status or None,
        strategy_type=strategy_type,
        paper_mode=paper_mode,
        limit=limit,
    )
    open_rows = [r for r in rows if r.get("status") == "open"]
    return JSONResponse(content={
        "positions": rows,
        "open_count": len(open_rows),
        "total_realized_pnl": round(
            sum(float(r.get("realized_pnl", 0) or 0) for r in rows), 6
        ),
    })
