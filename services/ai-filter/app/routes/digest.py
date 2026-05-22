"""
AI performance digest — Claude Sonnet narrates trading performance data
into a structured daily/weekly report for operators.

Two modes:
  POST /ai/digest           — synchronous (submits + waits, good for ≤20 trades)
  POST /ai/digest/batch     — async Batch API (good for 20+ trades; returns batch_id)
  GET  /ai/digest/batch/{id} — poll batch result

The digest pulls structured journal data (trade list + funnel stats + P&L)
and generates:
  - Executive summary: how did we do?
  - Strategy breakdown: which strategies performed vs. expectation?
  - Risk events: were any limits triggered? Why?
  - AI scoring calibration: did high AI scores correlate with good outcomes?
  - Recommendations: concrete operational improvements

Batch API is used for large datasets (historical weekly reviews, 100+ trades).
It processes asynchronously at ~50% the cost of the synchronous Messages API.
"""

import json
from datetime import datetime, timezone
from typing import Any

import anthropic
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import settings

log = structlog.get_logger()
router = APIRouter()

_DIGEST_SYSTEM_PROMPT = """\
You are MeznaQuantFX's performance analyst. You write concise, actionable
performance digests for trading operators based on structured journal data.

Your digest must use the write_digest tool and cover:

1. EXECUTIVE SUMMARY (2-3 sentences)
   Overall performance verdict. Total fills, fill rate, fees vs. notional.
   Did the system perform within expectations?

2. STRATEGY BREAKDOWN
   Per-strategy assessment: fill rate, cost efficiency, any anomalies.
   Flag if any strategy significantly underperformed vs. historical norms.

3. AI SCORING CALIBRATION
   Did signals with AI score > 70 have better outcomes than score < 50?
   Is the AI filter adding value or generating noise?
   If timeout rate > 20%, flag as infrastructure concern.

4. RISK ENGINE ACTIVITY
   How many rejections? Which checks triggered most?
   Were any cooldowns or halts activated? Were they appropriate?

5. OPERATIONAL RECOMMENDATIONS (2-4 bullet points)
   Specific, actionable items. Not generic advice.
   Examples: "Reduce stat_arb position size — slippage is 40% of edge"
             "Funding rate on BTC has dropped — review entry threshold"

CONSTRAINTS:
- Be quantitative. Reference actual numbers from the data.
- Flag anything statistically unusual (not just bad — unusual).
- Total output must be under 600 words.
- If data is thin (< 5 trades), say so and qualify all conclusions.
"""

_DIGEST_TOOL = {
    "name": "write_digest",
    "description": "Write a structured performance digest from journal data.",
    "input_schema": {
        "type": "object",
        "properties": {
            "executive_summary": {
                "type": "string",
                "description": "2-3 sentence overall performance verdict.",
            },
            "overall_rating": {
                "type": "string",
                "enum": ["excellent", "good", "acceptable", "poor", "insufficient_data"],
                "description": "Overall period performance rating.",
            },
            "strategy_breakdown": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "strategy": {"type": "string"},
                        "assessment": {"type": "string"},
                        "flag": {
                            "type": "string",
                            "enum": ["none", "underperforming", "anomaly", "excellent"],
                        },
                    },
                    "required": ["strategy", "assessment", "flag"],
                },
            },
            "ai_calibration": {
                "type": "string",
                "description": "Assessment of AI scoring effectiveness this period.",
            },
            "risk_activity": {
                "type": "string",
                "description": "Summary of risk engine rejections and any limit triggers.",
            },
            "recommendations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 specific, actionable operational recommendations.",
            },
            "data_quality_note": {
                "type": "string",
                "description": "Note on data completeness / statistical confidence.",
            },
        },
        "required": [
            "executive_summary", "overall_rating", "strategy_breakdown",
            "ai_calibration", "risk_activity", "recommendations", "data_quality_note",
        ],
    },
}


class StrategyStats(BaseModel):
    strategy_type: str
    total_trades: int
    filled: int
    rejected: int
    errors: int
    total_notional: float
    total_fees: float
    avg_ai_score: float | None = None
    ai_timeout_count: int = 0


class DigestRequest(BaseModel):
    """Structured journal data — fetch from journal service before calling this."""
    period_label: str                          # e.g. "2026-05-22 (daily)" or "Week 21"
    period_start: str                          # ISO datetime
    period_end: str                            # ISO datetime
    paper_mode: bool = True

    # From journal /trades/summary
    strategy_stats: list[StrategyStats]

    # From journal /pnl/summary
    total_fills: int = 0
    total_notional: float = 0.0
    total_fees: float = 0.0
    realized_pnl: float = 0.0

    # From journal /opportunities (funnel)
    opportunities_detected: int = 0
    ai_scored: int = 0
    risk_approved: int = 0
    executed: int = 0
    risk_rejected: int = 0

    # Risk events (from journal /risk-events)
    risk_events: list[dict[str, Any]] = []

    # Optional additional context
    operator_notes: str | None = None


def _build_digest_prompt(req: DigestRequest) -> str:
    lines = [f"## PERFORMANCE DIGEST: {req.period_label}\n"]
    lines.append(f"Period: {req.period_start} → {req.period_end}")
    lines.append(f"Mode: {'PAPER' if req.paper_mode else 'LIVE'}\n")

    lines.append("## SIGNAL FUNNEL\n")
    lines.append(f"Opportunities detected: {req.opportunities_detected}")
    lines.append(f"AI scored:              {req.ai_scored}")
    lines.append(f"Risk approved:          {req.risk_approved}")
    lines.append(f"Executed:               {req.executed}")
    lines.append(f"Risk rejected:          {req.risk_rejected}")
    if req.opportunities_detected > 0:
        lines.append(
            f"Fill rate (detected→exec): "
            f"{req.executed / req.opportunities_detected * 100:.1f}%"
        )

    lines.append("\n## P&L SUMMARY\n")
    lines.append(f"Total fills:      {req.total_fills}")
    lines.append(f"Total notional:   ${req.total_notional:,.2f}")
    lines.append(f"Total fees:       ${req.total_fees:,.4f}")
    lines.append(f"Realized P&L:     ${req.realized_pnl:,.4f}")
    if req.total_notional > 0:
        fee_bps = (req.total_fees / req.total_notional) * 10000
        lines.append(f"Fee rate:         {fee_bps:.2f} bps of notional")

    lines.append("\n## STRATEGY BREAKDOWN\n")
    for s in req.strategy_stats:
        fill_rate = s.filled / s.total_trades if s.total_trades else 0
        lines.append(f"### {s.strategy_type}")
        lines.append(f"  Trades: {s.total_trades} | Filled: {s.filled} ({fill_rate:.0%}) | Rejected: {s.rejected} | Errors: {s.errors}")
        lines.append(f"  Notional: ${s.total_notional:,.2f} | Fees: ${s.total_fees:,.4f}")
        if s.avg_ai_score is not None:
            lines.append(f"  Avg AI score: {s.avg_ai_score:.1f}/100 | AI timeouts: {s.ai_timeout_count}")

    if req.risk_events:
        lines.append("\n## RISK ENGINE EVENTS\n")
        for evt in req.risk_events[:10]:  # Cap at 10 for prompt length
            lines.append(f"  [{evt.get('event_type', '?')}] {evt.get('description', evt.get('payload', ''))}")

    if req.operator_notes:
        lines.append(f"\n## OPERATOR NOTES\n{req.operator_notes}")

    lines.append("\nWrite the performance digest.")
    return "\n".join(lines)


@router.post("/digest")
async def generate_digest(req: DigestRequest, request: Request) -> JSONResponse:
    """
    Generate a structured performance digest using Claude Sonnet.

    Synchronous — waits for Claude to complete. Use /ai/digest/batch
    for large datasets (100+ trades) or historical weekly reviews.
    """
    if not settings.ai_configured:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "ANTHROPIC_API_KEY not configured"},
        )

    client: anthropic.AsyncAnthropic = request.app.state.anthropic_client
    prompt = _build_digest_prompt(req)

    try:
        response = await client.messages.create(
            model=settings.AI_ANALYSIS_MODEL,
            max_tokens=2048,
            temperature=0.3,
            system=[
                {
                    "type": "text",
                    "text": _DIGEST_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[_DIGEST_TOOL],
            tool_choice={"type": "tool", "name": "write_digest"},
            messages=[{"role": "user", "content": prompt}],
        )

        result = None
        for block in response.content:
            if block.type == "tool_use" and block.name == "write_digest":
                result = block.input
                break

        if result is None:
            raise ValueError("Claude did not call write_digest tool")

        usage = response.usage
        log.info(
            "ai_filter.digest_generated",
            period=req.period_label,
            overall_rating=result.get("overall_rating"),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
        )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                **result,
                "period_label": req.period_label,
                "period_start": req.period_start,
                "period_end": req.period_end,
                "model": settings.AI_ANALYSIS_MODEL,
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    except anthropic.APIError as exc:
        log.error("ai_filter.digest_api_error", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": f"Anthropic API error: {str(exc)[:100]}"},
        )
    except Exception as exc:
        log.error("ai_filter.digest_error", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(exc)[:100]},
        )


@router.post("/digest/batch")
async def submit_digest_batch(req: DigestRequest, request: Request) -> JSONResponse:
    """
    Submit a digest generation request to the Anthropic Batch API.

    Processes asynchronously at ~50% cost vs. synchronous Messages API.
    Ideal for: weekly reviews, historical analysis, multiple period comparisons.

    Returns a batch_id. Poll GET /ai/digest/batch/{batch_id} for results.
    """
    if not settings.ai_configured:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "ANTHROPIC_API_KEY not configured"},
        )

    client: anthropic.AsyncAnthropic = request.app.state.anthropic_client
    prompt = _build_digest_prompt(req)
    custom_id = f"digest-{req.period_label.replace(' ', '-')}-{int(datetime.now().timestamp())}"

    try:
        batch = await client.messages.batches.create(
            requests=[
                {
                    "custom_id": custom_id,
                    "params": {
                        "model": settings.AI_ANALYSIS_MODEL,
                        "max_tokens": 2048,
                        "temperature": 0.3,
                        "system": [
                            {
                                "type": "text",
                                "text": _DIGEST_SYSTEM_PROMPT,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                        "tools": [_DIGEST_TOOL],
                        "tool_choice": {"type": "tool", "name": "write_digest"},
                        "messages": [{"role": "user", "content": prompt}],
                    },
                }
            ]
        )

        log.info(
            "ai_filter.digest_batch_submitted",
            batch_id=batch.id,
            period=req.period_label,
            custom_id=custom_id,
        )

        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "batch_id": batch.id,
                "custom_id": custom_id,
                "status": batch.processing_status,
                "poll_url": f"/ai/digest/batch/{batch.id}",
                "note": "Poll the poll_url every 30-60 seconds until status is 'ended'.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    except anthropic.APIError as exc:
        log.error("ai_filter.digest_batch_error", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": f"Batch API error: {str(exc)[:100]}"},
        )


@router.get("/digest/batch/{batch_id}")
async def get_digest_batch(batch_id: str, request: Request) -> JSONResponse:
    """
    Poll a Batch API digest request for results.

    Returns:
      processing_status: "in_progress" | "ended" | "errored" | "canceling" | "canceled"
      result: populated only when processing_status == "ended"
    """
    if not settings.ai_configured:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "ANTHROPIC_API_KEY not configured"},
        )

    client: anthropic.AsyncAnthropic = request.app.state.anthropic_client

    try:
        batch = await client.messages.batches.retrieve(batch_id)

        if batch.processing_status != "ended":
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "batch_id": batch_id,
                    "status": batch.processing_status,
                    "result": None,
                    "request_counts": {
                        "processing": batch.request_counts.processing,
                        "succeeded": batch.request_counts.succeeded,
                        "errored": batch.request_counts.errored,
                    },
                },
            )

        # Batch ended — collect results
        results = []
        async for item in await client.messages.batches.results(batch_id):
            if item.result.type == "succeeded":
                msg = item.result.message
                for block in msg.content:
                    if block.type == "tool_use" and block.name == "write_digest":
                        results.append({
                            "custom_id": item.custom_id,
                            "digest": block.input,
                            "usage": {
                                "input_tokens": msg.usage.input_tokens,
                                "output_tokens": msg.usage.output_tokens,
                            },
                        })
            else:
                results.append({
                    "custom_id": item.custom_id,
                    "error": str(item.result),
                })

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "batch_id": batch_id,
                "status": "ended",
                "results": results,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    except anthropic.APIError as exc:
        log.error("ai_filter.batch_retrieve_error", batch_id=batch_id, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": f"Batch API error: {str(exc)[:100]}"},
        )
