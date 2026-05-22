"""Opportunity history and funnel endpoints."""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from .. import queries

router = APIRouter()


@router.get("")
async def list_opportunities(
    request: Request,
    strategy_type: str | None = Query(None),
    risk_approved: bool | None = Query(None, description="Filter by risk gate outcome"),
    executed: bool | None = Query(None, description="Filter by whether execution was attempted"),
    since: str | None = Query(None, description="ISO datetime lower bound on detected_at"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> JSONResponse:
    """Paginated opportunity list with full funnel metadata."""
    opps, total = await queries.list_opportunities(
        request.app.state.db_engine,
        strategy_type=strategy_type,
        risk_approved=risk_approved,
        executed=executed,
        since=since,
        limit=limit,
        offset=offset,
    )
    return JSONResponse(content={
        "opportunities": opps,
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@router.get("/funnel")
async def funnel(
    request: Request,
    since: str | None = Query(None, description="ISO datetime lower bound — default: today UTC"),
) -> JSONResponse:
    """
    Opportunity funnel conversion rates.

    detected → ai_scored → risk_approved → executed

    Use this to diagnose where signals are dropping off:
      Low ai_filter_rate  → AI is filtering too aggressively (or API key missing)
      Low risk_approval_rate → Risk limits are too tight or positions are full
      Low execution_rate  → Executor errors or stale tick data
    """
    from datetime import datetime, timezone
    if since is None:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        since = today.isoformat()

    stats = await queries.funnel_stats(request.app.state.db_engine, since=since)
    stats["since"] = since
    return JSONResponse(content=stats)


@router.get("/{opportunity_id}")
async def get_opportunity(request: Request, opportunity_id: str) -> JSONResponse:
    """
    Fetch a single opportunity with all linked trade fills.

    Use this to trace a signal through the full pipeline:
    strategy → AI filter → risk gate → executor → fills
    """
    opp = await queries.get_opportunity_with_trades(
        request.app.state.db_engine, opportunity_id
    )
    if opp is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Opportunity not found: {opportunity_id}"},
        )
    return JSONResponse(content=opp)
