"""Audit log and risk event endpoints."""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from .. import queries

router = APIRouter()


@router.get("")
async def list_audit_log(
    request: Request,
    event_type: str | None = Query(None, description="Filter by event_type (e.g. risk.rejected)"),
    service: str | None = Query(None, description="Filter by originating service"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> JSONResponse:
    """
    Paginated audit log.

    The audit log is append-only and records every system event:
    opportunity detection, AI scoring, risk decisions, trade fills,
    kill-switch activations, and strategy toggles.
    """
    entries, total = await queries.list_audit(
        request.app.state.db_engine,
        event_type=event_type,
        service=service,
        limit=limit,
        offset=offset,
    )
    return JSONResponse(content={
        "entries": entries,
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@router.get("/risk-events")
async def list_risk_events(
    request: Request,
    event_type: str | None = Query(
        None,
        description=(
            "halt.auto | halt.manual | cooldown.triggered | "
            "limit.daily_drawdown | limit.consecutive_losses"
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> JSONResponse:
    """
    Risk event history: halts, cooldowns, and limit breaches.

    Each event includes the trigger_value and threshold_value so you
    can see exactly how far the system was from its limits.
    """
    events, total = await queries.list_risk_events(
        request.app.state.db_engine,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    return JSONResponse(content={
        "risk_events": events,
        "total": total,
        "limit": limit,
        "offset": offset,
    })
