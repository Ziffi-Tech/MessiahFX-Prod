"""
Trade Investigation Agent — POST /ai/agent/trade/{client_order_id}

Given a trade ID, this agent autonomously reconstructs the full picture:
  1. Fetches the trade and linked opportunity from the journal
  2. Runs a backtest for the same symbol/period to compare expectations vs. actuals
  3. Checks what market conditions (regime, live tick) looked like at trade time
  4. Searches the knowledge base for strategy notes relevant to the trade type
  5. Produces a structured verdict: what happened, why, and what to watch

Use cases:
  - Trade went wrong — why?
  - Trade had unexpected slippage — was it execution or market conditions?
  - AI score was 85 but trade lost — was the score miscalibrated?
  - Fill rate is low on a strategy — where are trades being blocked?

The agent has access to all investigation tools and runs up to 10 iterations.
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
from ..core.tools import ToolContext, TRADE_INVESTIGATION_TOOLS, execute_tool

log = structlog.get_logger()
router = APIRouter()

_TRADE_INVESTIGATION_PROMPT = """\
You are MeznaQuantFX's trade investigation specialist. You perform root-cause
analysis of individual trades to help operators understand what happened and why.

YOUR INVESTIGATION APPROACH:
1. Always start with get_trade_details to get the full trade + opportunity data
2. Run a backtest for the same symbol and period to compare live vs. expected
3. Check get_market_regime to understand conditions at the time
4. Use search_knowledge_base if you need strategy-specific context
5. Check get_live_tick to see if current spreads are normal for this instrument

KEY QUESTIONS TO ANSWER:
- Was the entry signal genuine? (AI score, net_edge_bps, Z-score or funding rate)
- Was execution quality acceptable? (slippage_bps vs. net_edge_bps)
- Did the backtest predict this outcome?
- Were market conditions conducive to this strategy type?
- What should the operator monitor going forward?

OUTPUT FORMAT:
## Trade Summary
One-paragraph overview: what trade was taken, result, key metrics.

## Signal Quality Assessment
Was the entry signal genuine? Score the signal quality based on the metrics.

## Execution Quality
Slippage analysis. Was execution within expected bounds?

## Backtest vs. Reality
Compare the live trade to what backtesting predicted for this period.

## Root Cause
2-3 paragraphs identifying the primary cause of the outcome (good or bad).

## Monitoring Triggers
3-4 specific conditions to watch for this open position (or lessons for future trades).

CONSTRAINTS:
- Be quantitative. Reference the actual numbers from the trade.
- If slippage_bps > 30% of net_edge_bps, flag this as a significant issue.
- Never recommend overriding risk limits.
- If the trade is still open, focus on what to monitor, not just what happened.
"""


class TradeAgentRequest(BaseModel):
    client_order_id: str
    additional_context: str | None = None   # Operator notes about this specific trade


@router.post("/agent/trade/{client_order_id}")
async def trade_investigation_agent(
    client_order_id: str, request: Request, body: TradeAgentRequest | None = None
) -> JSONResponse:
    """
    Autonomous trade investigation. Claude fetches the trade, runs a backtest
    for context, checks market conditions, and produces a root-cause analysis.

    Typical latency: 30-60 seconds (backtest download is the slow step).
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

    task = (
        f"Investigate trade {client_order_id}. "
        f"Fetch the full details, run a backtest for context, check market conditions, "
        f"and produce a complete root-cause analysis."
    )
    extra = body.additional_context if body else None

    log.info("trade_agent.started", client_order_id=client_order_id)

    result = await run_agent(
        client=client,
        model=settings.AI_ANALYSIS_MODEL,
        system_prompt=_TRADE_INVESTIGATION_PROMPT,
        tools=TRADE_INVESTIGATION_TOOLS,
        task=task,
        tool_executor=bound_executor,
        max_iterations=10,
        timeout_secs=120.0,
        extra_context=extra,
    )

    log.info(
        "trade_agent.completed",
        client_order_id=client_order_id,
        iterations=result["iterations"],
        tools_called=result["tools_called"],
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "analysis": result["answer"],
            "client_order_id": client_order_id,
            "iterations": result["iterations"],
            "tools_called": result["tools_called"],
            "timed_out": result["timed_out"],
            "error": result.get("error"),
            "model": settings.AI_ANALYSIS_MODEL,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
