"""Trade history and summary endpoints."""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from .. import queries

router = APIRouter()


@router.get("")
async def list_trades(
    request: Request,
    strategy_type: str | None = Query(None, description="Filter by strategy (funding_arb|stat_arb|swing)"),
    venue: str | None = Query(None, description="Filter by venue (binance|oanda)"),
    status: str | None = Query(None, description="Filter by status (filled|rejected|error|pending)"),
    paper_mode: bool | None = Query(None, description="Filter by trading mode"),
    since: str | None = Query(None, description="ISO datetime lower bound on opened_at"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> JSONResponse:
    """Paginated list of trades with optional filters."""
    trades, total = await queries.list_trades(
        request.app.state.db_engine,
        strategy_type=strategy_type,
        venue=venue,
        status=status,
        paper_mode=paper_mode,
        since=since,
        limit=limit,
        offset=offset,
    )
    return JSONResponse(content={
        "trades": trades,
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@router.get("/summary")
async def trades_summary(
    request: Request,
    since: str | None = Query(None, description="ISO datetime lower bound — default: today UTC"),
) -> JSONResponse:
    """
    Aggregate trade stats grouped by strategy_type and paper_mode.

    Returns fill rate, total notional, and fees per strategy.
    Used by the dashboard Journal tab for the summary cards.
    """
    from datetime import datetime, timezone
    if since is None:
        # Default to start of today UTC
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        since = today.isoformat()

    rows = await queries.trades_summary(request.app.state.db_engine, since=since)

    # Roll up totals across all strategies
    total_filled = sum(int(r.get("filled", 0) or 0) for r in rows)
    total_rejected = sum(int(r.get("rejected", 0) or 0) for r in rows)
    total_errors = sum(int(r.get("errors", 0) or 0) for r in rows)
    total_notional = sum(float(r.get("total_notional", 0) or 0) for r in rows)
    total_fees = sum(float(r.get("total_fees", 0) or 0) for r in rows)

    return JSONResponse(content={
        "since": since,
        "by_strategy": rows,
        "totals": {
            "filled": total_filled,
            "rejected": total_rejected,
            "errors": total_errors,
            "total_notional": round(total_notional, 4),
            "total_fees": round(total_fees, 6),
            "fill_rate": round(
                total_filled / (total_filled + total_rejected + total_errors), 4
            ) if (total_filled + total_rejected + total_errors) > 0 else 0.0,
        },
    })


@router.get("/{client_order_id}")
async def get_trade(request: Request, client_order_id: str) -> JSONResponse:
    """Fetch a single trade by client_order_id."""
    trade = await queries.get_trade(request.app.state.db_engine, client_order_id)
    if trade is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Trade not found: {client_order_id}"},
        )
    return JSONResponse(content=trade)
