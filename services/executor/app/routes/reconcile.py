"""
Ledger reconciliation endpoint.

  GET /reconcile/ledger  — compare our open live positions against the venues'
                           reported positions; persist the report + audit drift.

Inert in paper mode / when no venue has credentials (returns status=skipped).
"""

import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mezna_shared.audit import write_audit_log
from ..config import settings
from .. import ledger_reconciler as lr

log = structlog.get_logger()
router = APIRouter()

_REDIS_KEY = "reconciliation:ledger"
_TTL_SECONDS = 3600


@router.get("/reconcile/ledger", summary="Reconcile our positions against the exchange ledger")
async def reconcile_ledger(request: Request) -> JSONResponse:
    db_engine = request.app.state.db_engine
    redis = request.app.state.redis

    our_positions = [] if settings.is_paper else await lr.read_open_live_positions(db_engine)
    venues = lr.live_venues(settings)

    async def fetcher(venue: str) -> list[dict]:
        return await lr.ccxt_fetch_positions(venue, settings)

    report = await lr.reconcile_ledger(
        our_positions, fetcher, venues, paper_mode=settings.is_paper
    )

    # Best-effort persist for the dashboard + alerting; never break the response.
    try:
        await redis.set(_REDIS_KEY, json.dumps(report), ex=_TTL_SECONDS)
    except Exception as exc:
        log.warning("ledger.persist_failed", error=str(exc))

    # Audit + risk event on real drift.
    if report.get("status") == "ok" and not report.get("ok"):
        log.warning("ledger.drift_detected", summary=report.get("summary"))
        try:
            await write_audit_log(
                db_engine,
                event_type="reconciliation.drift",
                service="executor",
                payload={"summary": report.get("summary"), "drifts": report.get("drifts")},
            )
        except Exception as exc:
            log.warning("ledger.audit_failed", error=str(exc))

    report["served_at"] = datetime.now(timezone.utc).isoformat()
    return JSONResponse(content=report)
