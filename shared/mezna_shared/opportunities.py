"""
Opportunity-lifecycle persistence to the `opportunities` table.

The opportunity funnel (detected → ai_scored → risk_approved → executed) lives in
Redis streams; nothing wrote the DB table, so the journal funnel/history read all
zeros. This persists it from the two services that already hold a DB engine and
the full picture:

  * risk consumer  → upsert_opportunity() on every approve/reject. By the time an
    opportunity reaches risk it carries the detected fields (publisher) AND the AI
    fields (ai-filter always forwards), plus risk's own decision — so one upsert
    captures detected + ai_scored + risk_approved in one place.
  * executor       → mark_opportunity_executed() once an order fills.

Both are best-effort: failures are logged and swallowed so persistence never
interrupts the trading path (the table simply stays as-is, i.e. prior behaviour).

Column set mirrors migrations/versions/001_initial_schema.py.
"""

import json
from datetime import datetime, timezone

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .db import get_async_session

log = structlog.get_logger()


def _f(v):
    """Coerce to float or None."""
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def opportunity_upsert_params(opportunity_id: str, opp: dict) -> dict:
    """
    Map an opportunity payload (detected + AI + risk enrichment) to upsert params.

    Pure and side-effect-free so it can be unit-tested. Unknown/missing keys fall
    back to sensible defaults; ai_reason maps to the ai_reasoning column.
    """
    ai_timeout = opp.get("ai_timeout", False)
    if isinstance(ai_timeout, str):
        ai_timeout = ai_timeout.lower() == "true"

    return {
        "id": str(opportunity_id),
        "strategy_type": opp.get("strategy_type") or "unknown",
        "venue": opp.get("venue") or "unknown",
        "source": opp.get("source") or "internal",
        "symbol_primary": opp.get("symbol_primary") or "",
        "symbol_secondary": opp.get("symbol_secondary"),
        "detected_at": opp.get("detected_at"),  # ISO str; cast in SQL, NULL→now()
        "latency_profile": opp.get("latency_profile") or "standard",
        "spread": _f(opp.get("spread")),
        "z_score": _f(opp.get("z_score")),
        "funding_rate": _f(opp.get("funding_rate")),
        "expected_return_bps": _f(opp.get("expected_return_bps")),
        "fee_cost_bps": _f(opp.get("fee_cost_bps")),
        "net_edge_bps": _f(opp.get("net_edge_bps")),
        "ai_score": opp.get("ai_score"),
        "ai_reasoning": opp.get("ai_reason") or opp.get("ai_reasoning"),
        "ai_timeout": bool(ai_timeout),
        "ai_scored_at": opp.get("ai_scored_at"),
        "risk_approved": opp.get("risk_approved"),
        "risk_rejection_reason": opp.get("risk_rejection_reason"),
        "risk_checked_at": opp.get("risk_checked_at"),
        "paper_mode": bool(opp.get("paper_mode", True)),
        "raw_signal": json.dumps(opp.get("raw_signal") or {}),
    }


_UPSERT_OPPORTUNITY = text("""
    INSERT INTO opportunities (
        id, strategy_type, venue, source, symbol_primary, symbol_secondary,
        detected_at, latency_profile, spread, z_score, funding_rate,
        expected_return_bps, fee_cost_bps, net_edge_bps,
        ai_score, ai_reasoning, ai_timeout, ai_scored_at,
        risk_approved, risk_rejection_reason, risk_checked_at,
        paper_mode, raw_signal
    ) VALUES (
        CAST(:id AS uuid), :strategy_type, :venue, :source, :symbol_primary, :symbol_secondary,
        COALESCE(CAST(:detected_at AS timestamptz), now()), :latency_profile, :spread, :z_score, :funding_rate,
        :expected_return_bps, :fee_cost_bps, :net_edge_bps,
        :ai_score, :ai_reasoning, :ai_timeout, CAST(:ai_scored_at AS timestamptz),
        :risk_approved, :risk_rejection_reason, CAST(:risk_checked_at AS timestamptz),
        :paper_mode, CAST(:raw_signal AS jsonb)
    )
    ON CONFLICT (id) DO UPDATE SET
        ai_score              = EXCLUDED.ai_score,
        ai_reasoning          = EXCLUDED.ai_reasoning,
        ai_timeout            = EXCLUDED.ai_timeout,
        ai_scored_at          = EXCLUDED.ai_scored_at,
        risk_approved         = EXCLUDED.risk_approved,
        risk_rejection_reason = EXCLUDED.risk_rejection_reason,
        risk_checked_at       = EXCLUDED.risk_checked_at
""")

_MARK_EXECUTED = text("""
    UPDATE opportunities SET executed = true WHERE id = CAST(:id AS uuid)
""")


async def upsert_opportunity(db_engine: AsyncEngine, opportunity_id: str | None, opp: dict) -> None:
    """Best-effort upsert of an opportunity row. Never raises."""
    if not opportunity_id:
        return
    try:
        params = opportunity_upsert_params(opportunity_id, opp)
        async with get_async_session(db_engine) as session:
            await session.execute(_UPSERT_OPPORTUNITY, params)
    except Exception as exc:
        log.error("opportunity.upsert_failed", opportunity_id=opportunity_id, error=str(exc))


async def mark_opportunity_executed(db_engine: AsyncEngine, opportunity_id: str | None) -> None:
    """Best-effort mark of an opportunity as executed. Never raises."""
    if not opportunity_id:
        return
    try:
        async with get_async_session(db_engine) as session:
            await session.execute(_MARK_EXECUTED, {"id": str(opportunity_id)})
    except Exception as exc:
        log.error("opportunity.mark_executed_failed", opportunity_id=opportunity_id, error=str(exc))
