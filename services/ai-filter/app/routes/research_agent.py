"""
Research Agent — POST /ai/agent/research

Answers complex financial and operational questions by autonomously:
  1. Searching the RAG knowledge base for relevant research/strategy notes
  2. Querying journal data (trades, P&L, funnel stats)
  3. Running backtests to validate or compare performance
  4. Checking live risk state and market regime
  5. Synthesising a comprehensive, evidence-based answer

Example questions this agent handles well:
  - "Why did our stat_arb underperform this week vs. backtest expectations?"
  - "What is the optimal funding_arb entry threshold for ETHUSDT?"
  - "Are we taking too much risk given the current market regime?"
  - "What does our journal data tell us about execution quality?"
  - "Compare our live fill rate against backtest expectations"

This agent uses Claude Sonnet — it has full access to all 10 tools and
runs up to 12 tool-call iterations before producing its final answer.
Typical latency: 30–90 seconds for complex multi-step research.

Use /ai/agent/research/stream for real-time progress updates.
"""

import json
import functools
from datetime import datetime, timezone

import anthropic
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..config import settings
from ..core.agent_loop import run_agent
from ..core.tools import ToolContext, RESEARCH_TOOLS, execute_tool

log = structlog.get_logger()
router = APIRouter()

_RESEARCH_SYSTEM_PROMPT = """\
You are MeznaQuantFX's senior quantitative research analyst. You answer complex
financial and operational questions by autonomously gathering and analysing data.

YOUR CAPABILITIES (use them):
- search_knowledge_base: Find relevant strategy notes, research, risk policies
- get_trades: Analyse execution quality, fill rates, slippage
- get_pnl_summary: Review overall performance over any period
- get_risk_state: Check live risk posture, drawdown, position counts
- run_funding_arb_backtest: Validate funding arb performance for a single parameter set
- run_stat_arb_backtest: Validate stat arb for a single parameter set
- run_funding_arb_sweep: Sweep multiple min_edge_bps values — use to find OPTIMAL threshold
- run_stat_arb_sweep: Sweep multiple entry_z thresholds — use to find OPTIMAL Z-score
- run_regime_split: Split backtest results by realised volatility — reveals regime dependency
- get_market_regime: Understand current market regime and strategy fitness
- get_signal_funnel: See where signals are being filtered out

TOOL SELECTION GUIDE:
- "What is the optimal threshold for X?" → use sweep tools (run_funding_arb_sweep or run_stat_arb_sweep)
- "Does strategy X work in volatile markets?" → use run_regime_split
- "How has strategy X performed?" → use run_funding_arb_backtest / run_stat_arb_backtest + get_pnl_summary
- "Where are signals dropping out?" → use get_signal_funnel
- "What is the current risk posture?" → use get_risk_state + get_market_regime

RESEARCH METHODOLOGY:
1. Start with a broad search to understand what data is relevant
2. Gather specific data points (trades, P&L, backtests) to support or refute hypotheses
3. For parameter questions, ALWAYS use sweep tools — single backtests are not enough
4. Cross-reference multiple data sources before drawing conclusions
5. Quantify everything — use actual numbers, not vague descriptions
6. If data is insufficient or contradictory, say so explicitly

OUTPUT FORMAT:
## Summary
One paragraph executive summary with your main finding.

## Evidence
Bullet points of key data you gathered and what each tells you.

## Analysis
2-4 paragraphs of quantitative analysis. Reference specific numbers.

## Recommendations
2-4 concrete, actionable items. Be specific about thresholds and parameters.

## Confidence
Rate your confidence (High/Medium/Low) and note any data gaps.

CONSTRAINTS:
- Never recommend overriding hard risk limits
- Always flag when data is thin (< 5 trades) and qualify conclusions accordingly
- Be explicit when backtest results diverge from live performance
- Sweep results are IN-SAMPLE — always note they need out-of-sample validation
"""


class ResearchRequest(BaseModel):
    question: str
    context: str | None = None       # Optional additional operator context


@router.post("/agent/research")
async def research_agent(req: ResearchRequest, request: Request) -> JSONResponse:
    """
    Agentic research endpoint. Claude autonomously gathers data and answers
    your question using multiple tool calls.

    Returns when Claude has produced a complete answer (or hits max iterations).
    Typical latency: 30-90 seconds for complex questions.
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

    # Bind context to executor
    bound_executor = functools.partial(execute_tool, ctx=ctx)

    log.info("research_agent.started", question_preview=req.question[:80])

    result = await run_agent(
        client=client,
        model=settings.AI_ANALYSIS_MODEL,
        system_prompt=_RESEARCH_SYSTEM_PROMPT,
        tools=RESEARCH_TOOLS,
        task=req.question,
        tool_executor=bound_executor,
        max_iterations=12,
        timeout_secs=120.0,
        extra_context=req.context,
    )

    log.info(
        "research_agent.completed",
        iterations=result["iterations"],
        tools_called=result["tools_called"],
        timed_out=result["timed_out"],
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "answer": result["answer"],
            "question": req.question,
            "iterations": result["iterations"],
            "tools_called": result["tools_called"],
            "timed_out": result["timed_out"],
            "error": result.get("error"),
            "model": settings.AI_ANALYSIS_MODEL,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@router.post("/agent/research/stream")
async def research_agent_stream(req: ResearchRequest, request: Request) -> StreamingResponse:
    """
    Streaming research agent. Emits SSE events for each tool call and the final answer.

    SSE event types:
      {"type": "tool_call",  "tool": "get_trades", "iteration": 1}
      {"type": "tool_result","tool": "get_trades", "summary": "50 trades returned"}
      {"type": "thinking",   "text": "..."}          ← intermediate text if any
      {"type": "answer",     "text": "...", "chunk": true}  ← streamed answer chunks
      {"type": "done",       "iterations": 3, "tools_called": [...]}
      {"type": "error",      "message": "..."}
    """
    if not settings.ai_configured:
        async def _err():
            yield f'data: {json.dumps({"type": "error", "message": "ANTHROPIC_API_KEY not configured"})}\n\n'
        return StreamingResponse(_err(), media_type="text/event-stream")

    client: anthropic.AsyncAnthropic = request.app.state.anthropic_client
    ctx = ToolContext(
        http_client=request.app.state.agent_http_client,
        redis=request.app.state.redis,
        journal_url=settings.JOURNAL_URL,
        backtest_url=settings.BACKTEST_URL,
        rag_url=settings.RAG_URL,
        risk_url=settings.RISK_URL,
    )

    async def _generate():
        import asyncio

        tools_called: list[str] = []
        messages = [{"role": "user", "content": req.question}]

        yield f'data: {json.dumps({"type": "started", "question": req.question[:80]})}\n\n'

        for iteration in range(12):
            try:
                response = await asyncio.wait_for(
                    client.messages.create(
                        model=settings.AI_ANALYSIS_MODEL,
                        max_tokens=4096,
                        system=[
                            {
                                "type": "text",
                                "text": _RESEARCH_SYSTEM_PROMPT,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                        tools=RESEARCH_TOOLS,
                        messages=messages,
                    ),
                    timeout=60.0,
                )
            except asyncio.TimeoutError:
                yield f'data: {json.dumps({"type": "error", "message": "Claude API call timed out"})}\n\n'
                return
            except Exception as exc:
                yield f'data: {json.dumps({"type": "error", "message": str(exc)[:100]})}\n\n'
                return

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # Stream the final answer
                answer = ""
                for block in response.content:
                    if hasattr(block, "text") and block.type == "text":
                        answer = block.text
                        # Stream in chunks
                        for i in range(0, len(answer), 100):
                            chunk = answer[i:i+100]
                            yield f'data: {json.dumps({"type": "answer", "text": chunk, "chunk": True})}\n\n'

                yield f'data: {json.dumps({"type": "done", "iterations": iteration + 1, "tools_called": tools_called})}\n\n'
                return

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    tools_called.append(block.name)
                    yield f'data: {json.dumps({"type": "tool_call", "tool": block.name, "iteration": iteration + 1})}\n\n'

                    result = await execute_tool(block.name, block.input, ctx)
                    result_str = json.dumps(result) if not isinstance(result, str) else result

                    # Summarise result for the stream (don't send raw data)
                    if "error" in result:
                        summary = f"Error: {result['error']}"
                    elif isinstance(result, dict):
                        summary = f"{len(result)} fields returned"
                    else:
                        summary = "Data received"

                    yield f'data: {json.dumps({"type": "tool_result", "tool": block.name, "summary": summary})}\n\n'

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

                messages.append({"role": "user", "content": tool_results})

        yield f'data: {json.dumps({"type": "error", "message": "Max iterations reached"})}\n\n'

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
