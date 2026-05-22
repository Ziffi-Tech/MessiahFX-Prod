"""
Risk rejection narrative — Claude Haiku translates machine risk codes into
plain English explanations for operators and the dashboard.

The risk engine produces terse rejection codes like:
  "daily_drawdown_limit_breached:0.0341"
  "consecutive_loss_limit:5"
  "max_open_positions_reached:5"

Operators — especially non-technical ones using the dashboard — can't act
on these codes. This endpoint produces a clear, actionable explanation of:
  1. What happened
  2. Why the rule exists
  3. What the operator should check or wait for

Uses Claude Haiku (fast, cheap) with tool use for structured output.
Prompt caching on system prompt — called frequently from dashboard.
"""

from datetime import datetime, timezone

import anthropic
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import settings

log = structlog.get_logger()
router = APIRouter()

_NARRATIVE_SYSTEM_PROMPT = """\
You are MeznaQuantFX's risk management explainer. Your job is to translate
terse machine-generated risk rejection codes into clear, actionable language
for trading operators — some of whom are not deeply technical.

REJECTION CODE REFERENCE:
- kill_switch_active          : Operator manually halted all trading
- strategy_disabled           : This strategy was toggled off in the dashboard
- strategy_on_cooldown        : Strategy paused after consecutive losses; wait for TTL
- daily_drawdown_limit_breached:<value> : Total losses today hit the daily limit
- max_open_positions_reached:<count>    : Too many simultaneous positions open
- consecutive_loss_limit:<count>        : Too many losses in a row; cooldown triggered
- net_edge_not_positive:<value>         : Signal edge was <= 0 after costs

RISK LIMITS CONTEXT:
- Daily drawdown limit: stops trading at 3% capital loss per day
- Max open positions: capped at 5 simultaneous
- Consecutive loss limit: 5 losses → 30 min cooldown per strategy
- These limits exist to prevent catastrophic loss during adverse market conditions

Your explanation should:
1. Say clearly what triggered the rejection (1 sentence)
2. Explain why this rule exists (1-2 sentences)
3. Tell the operator what to check or how long to wait (1 sentence)
Keep it under 120 words total. No jargon. No markdown.
"""

_NARRATIVE_TOOL = {
    "name": "explain_rejection",
    "description": "Provide a plain English explanation of a risk rejection.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {
                "type": "string",
                "description": "One sentence: what happened. Max 15 words.",
            },
            "explanation": {
                "type": "string",
                "description": "Why this rule exists and what it protects against. Max 50 words.",
            },
            "action": {
                "type": "string",
                "description": "What the operator should do or wait for. Max 30 words.",
            },
            "severity": {
                "type": "string",
                "enum": ["info", "warning", "critical"],
                "description": "info=temporary/normal, warning=needs attention, critical=immediate action needed.",
            },
        },
        "required": ["headline", "explanation", "action", "severity"],
    },
}


class NarrativeRequest(BaseModel):
    rejection_reason: str                     # Raw rejection code from risk engine
    strategy_type: str | None = None
    symbol_primary: str | None = None
    venue: str | None = None
    net_edge_bps: float | None = None
    ai_score: int | None = None
    paper_mode: bool = True


@router.post("/risk-narrative")
async def risk_narrative(req: NarrativeRequest, request: Request) -> JSONResponse:
    """
    Translate a machine risk rejection code into plain English.

    Used by the dashboard to show operators WHY a signal was blocked,
    not just the raw rejection code string.
    """
    if not settings.ai_configured:
        # Graceful fallback — return the raw code with a generic message
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "headline": "Signal rejected by risk engine.",
                "explanation": f"Rejection code: {req.rejection_reason}",
                "action": "Check risk engine settings in the dashboard.",
                "severity": "warning",
                "ai_generated": False,
            },
        )

    client: anthropic.AsyncAnthropic = request.app.state.anthropic_client

    context_lines = [
        f"Rejection code: {req.rejection_reason}",
        f"Strategy: {req.strategy_type or 'unknown'}",
        f"Symbol: {req.symbol_primary or 'unknown'}",
        f"Venue: {req.venue or 'unknown'}",
        f"Mode: {'PAPER' if req.paper_mode else 'LIVE'}",
    ]
    if req.net_edge_bps is not None:
        context_lines.append(f"Signal net edge: {req.net_edge_bps} bps")
    if req.ai_score is not None:
        context_lines.append(f"AI score at time of rejection: {req.ai_score}/100")

    user_prompt = "\n".join(context_lines) + "\n\nExplain this rejection to the operator."

    try:
        response = await client.messages.create(
            model=settings.AI_SCORING_MODEL,   # Haiku — fast + cheap for simple task
            max_tokens=256,
            temperature=0.0,
            system=[
                {
                    "type": "text",
                    "text": _NARRATIVE_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[_NARRATIVE_TOOL],
            tool_choice={"type": "tool", "name": "explain_rejection"},
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Extract from tool_use block
        result = {"headline": "", "explanation": "", "action": "", "severity": "warning"}
        for block in response.content:
            if block.type == "tool_use" and block.name == "explain_rejection":
                result = block.input
                break

        log.info(
            "ai_filter.risk_narrative_generated",
            rejection_reason=req.rejection_reason,
            severity=result.get("severity"),
        )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                **result,
                "raw_rejection_code": req.rejection_reason,
                "ai_generated": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    except anthropic.APIError as exc:
        log.error("ai_filter.narrative_api_error", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_200_OK,   # Never block dashboard on AI error
            content={
                "headline": "Signal rejected by risk engine.",
                "explanation": f"Code: {req.rejection_reason}",
                "action": "Check the risk engine logs for details.",
                "severity": "warning",
                "ai_generated": False,
            },
        )
    except Exception as exc:
        log.error("ai_filter.narrative_error", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "headline": "Signal rejected by risk engine.",
                "explanation": f"Code: {req.rejection_reason}",
                "action": "Check the risk engine logs for details.",
                "severity": "warning",
                "ai_generated": False,
            },
        )
