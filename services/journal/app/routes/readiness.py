"""
Go-live readiness endpoint.

  GET /readiness  — objective pass/fail checklist for the live-trading gate,
                    evaluated from real trade + audit + risk-event data.

Backs the dashboard's Go-Live Readiness panel and the docs/go-live-checklist.md
process. Read-only; never mutates anything.
"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from .. import queries

router = APIRouter()


@router.get("/readiness")
async def go_live_readiness(
    request: Request,
    min_paper_days: int = Query(28, ge=1, le=365, description="Required days of paper activity"),
    min_trades: int = Query(50, ge=1, le=100_000, description="Required filled-trade count"),
) -> JSONResponse:
    """Return the go-live readiness checklist (criteria + advisory + summary)."""
    data = await queries.go_live_readiness(
        request.app.state.db_engine,
        min_paper_days=min_paper_days,
        min_trades=min_trades,
    )
    return JSONResponse(content=data)
