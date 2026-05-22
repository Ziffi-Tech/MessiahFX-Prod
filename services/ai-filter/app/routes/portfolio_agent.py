"""
Portfolio Health Agent — POST /ai/agent/portfolio

Autonomous portfolio review. Claude checks the live system state from multiple
angles and produces a comprehensive health assessment with specific action items.

What it checks autonomously:
  1. Live risk state (halt flag, drawdown, open positions, consecutive losses)
  2. Recent trade performance (fill rate, slippage, fees)
  3. P&L summary (7-day and 30-day)
  4. Signal funnel health (where are signals dropping out?)
  5. Market regime (is the current regime favourable for active strategies?)
  6. Live tick spreads for active instruments (is execution feasible?)

Output:
  - Overall health rating (green / amber / red)
  - Specific concerns with severity
  - Recommended immediate actions (if any)
  - Strategy-level assessment

This agent is designed to be called by:
  - Dashboard "Portfolio Health" tab (on demand)
  - Scheduled daily morning briefing
  - Automatically after any auto-halt event
"""

import functools
from datetime import datetime, timezone

import anthropic
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import settings
from ..core.agent_loop import run_agent
from ..core.tools import ToolContext, PORTFOLIO_TOOLS, execute_tool

log = structlog.get_logger()
router = APIRouter()

_PORTFOLIO_HEALTH_PROMPT = """\
You are MeznaQuantFX's portfolio health monitor. You perform a systematic,
autonomous health check of the live trading system and produce an actionable
status report for operators.

YOUR ASSESSMENT METHODOLOGY:
1. get_risk_state — always first. Check halt status, drawdown, open positions.
2. get_pnl_summary (7 days) — recent performance baseline
3. get_pnl_summary (30 days) — trend context
4. get_signal_funnel (7 days) — pipeline health
5. get_market_regime — environmental fitness check
6. get_trades (last 7 days) — recent execution quality review
7. get_live_tick — spot-check spreads on 1-2 key instruments if needed

HEALTH THRESHOLDS (use these for assessment):
- Daily drawdown > 2%         : Amber alert
- Daily drawdown > 2.5%       : Red alert (approaching auto-halt at 3%)
- Consecutive losses >= 3     : Amber alert (halt triggers at 5)
- Fill rate < 60%             : Investigate — risk engine blocking too much?
- AI timeout rate > 20%       : Infrastructure concern
- Signal funnel exec rate < 20%: Possible strategy misconfiguration
- Spread > 15 bps on key instruments: Elevated execution cost
- Market regime = "volatile"  : Amber — reduce discretionary exposure
- Market regime = "crisis"    : Red — halt discretionary strategies immediately

OUTPUT FORMAT:
## Overall Health: [GREEN / AMBER / RED]
One sentence summary of current system health.

## Risk Posture
Current drawdown, position count, consecutive losses, halt status.
Flag any metrics approaching limits.

## Performance (7-Day)
Fill rate, P&L, fees, key trends.

## Pipeline Health
Signal funnel analysis — where are signals being filtered?
Is the AI filter adding value? Is risk engine too aggressive?

## Market Fit
Does the current market regime favour active strategies?
Strategy fitness scores.

## Action Items
Numbered list of concrete actions (most urgent first).
If all is healthy, say so explicitly.

## Next Review
When to review again (based on current conditions).

CONSTRAINTS:
- Never recommend disabling hard risk controls
- If trading is halted, lead with that — it's the most critical state
- If data is unavailable for any section, note it and proceed
- Be specific — "reduce position size by 20%" not "consider reducing risk"
"""


class PortfolioHealthRequest(BaseModel):
    focus_strategies: list[str] | None = None   # e.g. ["funding_arb", "stat_arb"]
    operator_notes: str | None = None


@router.post("/agent/portfolio")
async def portfolio_health_agent(req: PortfolioHealthRequest, request: Request) -> JSONResponse:
    """
    Autonomous portfolio health assessment.

    Claude systematically checks risk state, performance, pipeline health,
    and market conditions, then produces a structured health report with
    prioritised action items.

    Typical latency: 20-45 seconds.
    """
    if not settings.ai_configured:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "ANTHROPIC_API_KEY not configured"},
        )

    client: anthropic.AsyncAnthropic = request.app.state.anthropic_client
    ctx = ToolContext(
        http_client=request.app.state.agent_http_client,
        redis=request.app.state.redis,
        journal_url=settings.JOURNAL_URL,
        backtest_url=settings.BACKTEST_URL,
        rag_url=settings.RAG_URL,
        risk_url=settings.RISK_URL,
    )

    bound_executor = functools.partial(execute_tool, ctx=ctx)

    task_parts = [
        "Perform a comprehensive portfolio health assessment.",
        "Check risk state, performance, pipeline health, and market conditions.",
        "Produce a structured health report with prioritised action items.",
    ]
    if req.focus_strategies:
        task_parts.append(f"Pay particular attention to these strategies: {', '.join(req.focus_strategies)}.")
    task = " ".join(task_parts)

    log.info("portfolio_agent.started", focus=req.focus_strategies)

    result = await run_agent(
        client=client,
        model=settings.AI_ANALYSIS_MODEL,
        system_prompt=_PORTFOLIO_HEALTH_PROMPT,
        tools=PORTFOLIO_TOOLS,
        task=task,
        tool_executor=bound_executor,
        max_iterations=8,
        timeout_secs=90.0,
        extra_context=req.operator_notes,
    )

    # Extract health rating from answer for structured response
    answer = result["answer"]
    health = "unknown"
    for line in answer.splitlines():
        if "overall health" in line.lower():
            if "green" in line.lower():
                health = "green"
            elif "red" in line.lower():
                health = "red"
            elif "amber" in line.lower():
                health = "amber"
            break

    log.info(
        "portfolio_agent.completed",
        health=health,
        iterations=result["iterations"],
        tools_called=result["tools_called"],
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "health_report": answer,
            "health_rating": health,
            "iterations": result["iterations"],
            "tools_called": result["tools_called"],
            "timed_out": result["timed_out"],
            "error": result.get("error"),
            "model": settings.AI_ANALYSIS_MODEL,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
