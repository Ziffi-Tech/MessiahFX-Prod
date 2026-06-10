"""
Parameter governance — version + audit strategy parameters; detect drift.

  GET  /strategy/{type}             — current params + hash + latest version
  PUT  /strategy/{type}             — set params (records a versioned audit entry)
  GET  /strategy/{type}/history     — change history (from audit_log)
  POST /strategy/{type}/check-drift — compare a reference set (e.g. backtested) vs live

Current params live in strategy_configs.params (overwrite); the append-only
version history lives in audit_log (event_type=strategy.params_changed), so every
change is attributed (verified operator), diffed, and hashed — no silent drift
between the params a backtest validated and the params running live.
"""

import json

import structlog
from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from mezna_shared.db import get_async_session
from mezna_shared import param_governance as pg

from ..auth import resolve_identity, require_verified

log = structlog.get_logger()
router = APIRouter()


class SetParamsRequest(BaseModel):
    params: dict = Field(..., description="The full parameter set for the strategy")
    source: str = Field("manual", description="Where the params came from: manual | optimize | walk_forward | backtest")
    reason: str = Field("", description="Why the change is being made")


class CheckDriftRequest(BaseModel):
    reference_params: dict = Field(..., description="The reference set (e.g. backtested) to compare live against")


async def _current_params(session, strategy_type: str) -> dict | None:
    row = (await session.execute(
        text("SELECT params, updated_by, updated_at FROM strategy_configs WHERE strategy_type = :st"),
        {"st": strategy_type},
    )).fetchone()
    if not row:
        return None
    params = row.params if isinstance(row.params, dict) else json.loads(row.params or "{}")
    return {"params": params, "updated_by": row.updated_by, "updated_at": row.updated_at.isoformat() if row.updated_at else None}


async def _latest_version(session, strategy_type: str) -> int:
    row = (await session.execute(
        text("""
            SELECT (payload->>'version') AS version
            FROM audit_log
            WHERE event_type = 'strategy.params_changed' AND payload->>'strategy_type' = :st
            ORDER BY created_at DESC LIMIT 1
        """),
        {"st": strategy_type},
    )).fetchone()
    return int(row.version) if row and row.version else 0


@router.get("/strategy/{strategy_type}", summary="Current strategy params + hash + version")
async def get_params(strategy_type: str, request: Request) -> dict:
    async with get_async_session(request.app.state.db_engine) as session:
        current = await _current_params(session, strategy_type)
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown strategy: {strategy_type}")
        version = await _latest_version(session, strategy_type)
    return {
        "strategy_type": strategy_type,
        "params": current["params"],
        "hash": pg.param_hash(current["params"]),
        "version": version,
        "updated_by": current["updated_by"],
        "updated_at": current["updated_at"],
    }


@router.put("/strategy/{strategy_type}", summary="Set strategy params (versioned + audited)")
async def set_params(strategy_type: str, body: SetParamsRequest, request: Request) -> dict:
    identity = await resolve_identity(request)
    require_verified(identity)  # enforces GATEWAY_REQUIRE_AUTH; viewer already blocked at proxy
    actor = identity.user

    async with get_async_session(request.app.state.db_engine) as session:
        current = await _current_params(session, strategy_type)
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown strategy: {strategy_type}")

        old = current["params"]
        new = body.params
        diff = pg.diff_params(old, new)
        version = pg.next_version(await _latest_version(session, strategy_type))
        new_hash = pg.param_hash(new)

        await session.execute(
            text("""
                UPDATE strategy_configs
                SET params = :params::jsonb, updated_at = now(), updated_by = :by
                WHERE strategy_type = :st
            """),
            {"params": json.dumps(new), "by": actor, "st": strategy_type},
        )
        await session.execute(
            text("""
                INSERT INTO audit_log (event_type, service, payload, metadata, created_at)
                VALUES ('strategy.params_changed', 'gateway', :payload::jsonb, '{}'::jsonb, now())
            """),
            {"payload": json.dumps({
                "strategy_type": strategy_type,
                "params": new,
                "hash": new_hash,
                "version": version,
                "diff": diff,
                "source": body.source,
                "reason": body.reason,
                "by": actor,
            })},
        )

    log.info("governance.params_changed", strategy=strategy_type, version=version, hash=new_hash, by=actor, source=body.source)
    return {"strategy_type": strategy_type, "params": new, "hash": new_hash, "version": version, "diff": diff, "updated_by": actor}


@router.get("/strategy/{strategy_type}/history", summary="Param change history")
async def get_history(strategy_type: str, request: Request, limit: int = 20) -> dict:
    async with get_async_session(request.app.state.db_engine) as session:
        rows = (await session.execute(
            text("""
                SELECT payload, created_at
                FROM audit_log
                WHERE event_type = 'strategy.params_changed' AND payload->>'strategy_type' = :st
                ORDER BY created_at DESC LIMIT :lim
            """),
            {"st": strategy_type, "lim": min(limit, 100)},
        )).fetchall()
    history = []
    for r in rows:
        payload = r.payload if isinstance(r.payload, dict) else json.loads(r.payload or "{}")
        payload["created_at"] = r.created_at.isoformat() if r.created_at else None
        history.append(payload)
    return {"strategy_type": strategy_type, "count": len(history), "history": history}


@router.post("/strategy/{strategy_type}/check-drift", summary="Compare a reference param set vs live")
async def check_drift(strategy_type: str, body: CheckDriftRequest, request: Request) -> dict:
    async with get_async_session(request.app.state.db_engine) as session:
        current = await _current_params(session, strategy_type)
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown strategy: {strategy_type}")
    report = pg.drift_report(current["params"], body.reference_params)
    return {"strategy_type": strategy_type, **report}
