"""
Deep trade analysis — Claude Sonnet with extended thinking + streaming.

Two endpoints:
  POST /ai/analyse         — full blocking response (JSON), extended thinking enabled
  POST /ai/analyse/stream  — Server-Sent Events stream (dashboard-friendly)

Both use Claude Sonnet with extended thinking (budget_tokens=8000).
Extended thinking lets Claude reason step-by-step through complex multi-leg
trade scenarios before writing the final analysis — quality is dramatically
better than a plain prompt for ambiguous or edge-case signals.

NOT on the live signal critical path (that's scorer.py / Haiku, 800ms).
Called after a trade is journaled for post-execution operator review.

Prompt caching on system prompt: billed once per 5-minute window.
Extended thinking: temperature must be 1 (Anthropic requirement).
"""

import asyncio
import json
from datetime import datetime, timezone

import anthropic
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..config import settings

log = structlog.get_logger()
router = APIRouter()

_ANALYSIS_TIMEOUT_SECS = 60.0   # Extended thinking needs more headroom
_THINKING_BUDGET_TOKENS = 8000  # Inner monologue budget before final answer

_ANALYSIS_SYSTEM_PROMPT = """\
You are MeznaQuantFX's senior quantitative trading analyst. You conduct post-trade
analysis to help operators understand signal quality, execution risk, and portfolio
implications. Your analysis is used for learning and process improvement — not
real-time decisions.

CONTEXT YOU RECEIVE:
- The full opportunity payload: strategy type, instruments, edge metrics, AI score
- Execution result: whether the trade was taken, execution price, slippage
- Market conditions at signal time: funding rates, Z-scores, spreads

YOUR ANALYSIS SHOULD COVER:
## 1. SIGNAL QUALITY
Was the edge real? Were metrics consistent with the strategy thesis?
Flag any values that look anomalous vs. strategy norms.

## 2. EXECUTION QUALITY
Did fills match expectation? Evaluate slippage vs. the net_edge_bps.
If slippage_bps > 30% of net_edge_bps, flag as execution risk.

## 3. RISK FACTORS
What could cause this position to lose money?
Key price levels, volatility events, or correlation breakdowns to watch.

## 4. AI SCORE vs OUTCOME
Did the Haiku score (0-100) align with execution quality?
If there's a significant divergence, explain the likely cause.

## 5. WATCHLIST
2-3 specific, actionable monitoring triggers for this open position.
Be quantitative: "Close if Z-score reverts to 0.5" not "monitor the position."

CONSTRAINTS:
- Maximum 450 words in the final answer
- Be precise and quantitative; this is a trading system, not an essay
- Never recommend overriding hard risk limits
- Cite specific numbers from the payload, not generic statements
"""


class AnalyseRequest(BaseModel):
    """Full context for post-trade deep analysis. All fields optional."""
    # Core opportunity
    strategy_type: str | None = None
    symbol_primary: str | None = None
    symbol_secondary: str | None = None
    venue: str | None = None
    net_edge_bps: float | None = None
    expected_return_bps: float | None = None
    fee_cost_bps: float | None = None
    spread: float | None = None
    z_score: float | None = None
    funding_rate: float | None = None
    direction: str | None = None
    position_usd: float | None = None
    paper_mode: bool = True

    # AI scoring context
    ai_score: int | None = None
    ai_reason: str | None = None
    ai_timeout: bool = False

    # Execution result
    executed: bool | None = None
    execution_price: float | None = None
    slippage_bps: float | None = None
    fill_qty: float | None = None
    reject_reason: str | None = None

    # Optional operator context
    operator_notes: str | None = None


def _build_analysis_prompt(req: AnalyseRequest) -> str:
    lines = ["## TRADE SUMMARY\n"]
    lines.append(f"Strategy: {req.strategy_type or 'unknown'}")
    lines.append(f"Instruments: {req.symbol_primary or '—'} / {req.symbol_secondary or '—'}")
    lines.append(f"Venue: {req.venue or '—'}")
    lines.append(f"Direction: {req.direction or '—'}")
    lines.append(f"Position size: ${req.position_usd or '—'}")
    lines.append(f"Mode: {'PAPER' if req.paper_mode else 'LIVE'}\n")

    lines.append("## SIGNAL METRICS\n")
    lines.append(f"Net edge: {req.net_edge_bps} bps")
    lines.append(f"Gross expected: {req.expected_return_bps} bps")
    lines.append(f"Fee cost: {req.fee_cost_bps} bps")
    if req.spread is not None:
        lines.append(f"Spread: {req.spread} bps")
    if req.funding_rate is not None:
        lines.append(f"Funding rate: {req.funding_rate * 100:.5f}% per 8h")
    if req.z_score is not None:
        lines.append(f"Z-score: {req.z_score:.4f}\n")

    lines.append("## AI SCORING\n")
    if req.ai_timeout:
        lines.append("AI scoring: TIMED OUT — risk engine acted alone")
    elif req.ai_score is not None:
        lines.append(f"Haiku score: {req.ai_score}/100")
        lines.append(f"Haiku reason: {req.ai_reason or '—'}")
    else:
        lines.append("AI scoring: not configured")

    lines.append("\n## EXECUTION RESULT\n")
    if req.executed is None:
        lines.append("Execution result: not provided")
    elif req.executed:
        lines.append("Trade: EXECUTED")
        if req.execution_price:
            lines.append(f"Fill price: {req.execution_price}")
        if req.slippage_bps is not None:
            lines.append(f"Slippage: {req.slippage_bps:.2f} bps")
        if req.fill_qty is not None:
            lines.append(f"Fill quantity: {req.fill_qty}")
    else:
        lines.append(f"Trade: REJECTED — {req.reject_reason or 'no reason provided'}")

    if req.operator_notes:
        lines.append(f"\n## OPERATOR NOTES\n{req.operator_notes}")

    lines.append("\n---\nPlease provide your analysis using the section headers above.")
    return "\n".join(lines)


def _make_api_params(req: AnalyseRequest) -> dict:
    """Build the shared API parameters for both blocking and streaming calls."""
    return {
        "model": settings.AI_ANALYSIS_MODEL,
        "max_tokens": 12000,   # Must be > thinking budget + answer tokens
        "temperature": 1,      # Required when extended thinking is enabled
        # Extended thinking: Claude reasons internally before writing the answer.
        # This dramatically improves quality for complex multi-leg analysis.
        "thinking": {
            "type": "enabled",
            "budget_tokens": _THINKING_BUDGET_TOKENS,
        },
        # Prompt caching on system prompt — billed once per 5-min window
        "system": [
            {
                "type": "text",
                "text": _ANALYSIS_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": _build_analysis_prompt(req)}],
    }


@router.post("/analyse")
async def analyse_trade(req: AnalyseRequest, request: Request) -> JSONResponse:
    """
    Deep post-trade analysis using Claude Sonnet with extended thinking.

    Claude thinks step-by-step (budget: 8000 tokens) before writing the
    final analysis. Thinking tokens are NOT returned — only the final answer.

    Typical latency: 8–25 seconds. Use /ai/analyse/stream for dashboard display.
    """
    if not settings.ai_configured:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "ANTHROPIC_API_KEY not configured", "analysis": None},
        )

    client: anthropic.AsyncAnthropic = request.app.state.anthropic_client
    strategy = req.strategy_type or "unknown"
    symbol = req.symbol_primary or "unknown"

    try:
        response = await asyncio.wait_for(
            client.messages.create(**_make_api_params(req)),
            timeout=_ANALYSIS_TIMEOUT_SECS,
        )

        # Extract only the text block (thinking blocks are internal to Claude)
        analysis_text = ""
        thinking_tokens_used = 0
        for block in response.content:
            if block.type == "text":
                analysis_text = block.text
            elif block.type == "thinking":
                thinking_tokens_used = len(block.thinking.split()) * 1.3  # rough estimate

        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0)
        cache_created = getattr(usage, "cache_creation_input_tokens", 0)

        log.info(
            "ai_filter.analysis_complete",
            strategy=strategy,
            symbol=symbol,
            model=settings.AI_ANALYSIS_MODEL,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_created_tokens=cache_created,
            thinking_enabled=True,
        )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "analysis": analysis_text,
                "model": settings.AI_ANALYSIS_MODEL,
                "thinking_enabled": True,
                "thinking_budget_tokens": _THINKING_BUDGET_TOKENS,
                "strategy": strategy,
                "symbol": symbol,
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": cache_read,
                    "cache_created_tokens": cache_created,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    except asyncio.TimeoutError:
        log.warning("ai_filter.analysis_timeout", strategy=strategy, symbol=symbol)
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            content={"error": "Analysis timed out", "analysis": None},
        )
    except anthropic.APIError as exc:
        log.error("ai_filter.analysis_api_error", strategy=strategy, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": f"Anthropic API error: {str(exc)[:100]}", "analysis": None},
        )
    except Exception as exc:
        log.error("ai_filter.analysis_error", strategy=strategy, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": f"Unexpected error: {str(exc)[:100]}", "analysis": None},
        )


@router.post("/analyse/stream")
async def analyse_trade_stream(req: AnalyseRequest, request: Request) -> StreamingResponse:
    """
    Streaming deep trade analysis — Server-Sent Events (SSE).

    Dashboard calls this and renders analysis token-by-token as Claude writes it.
    Thinking blocks are suppressed — only the final text answer is streamed.

    SSE event format:
      data: {"type": "text_delta", "text": "..."}
      data: {"type": "done", "model": "...", "usage": {...}}
      data: {"type": "error", "message": "..."}
    """
    if not settings.ai_configured:
        async def _error_stream():
            yield f'data: {json.dumps({"type": "error", "message": "ANTHROPIC_API_KEY not configured"})}\n\n'
        return StreamingResponse(_error_stream(), media_type="text/event-stream")

    client: anthropic.AsyncAnthropic = request.app.state.anthropic_client
    strategy = req.strategy_type or "unknown"
    symbol = req.symbol_primary or "unknown"

    async def _generate():
        try:
            in_thinking_block = False
            usage_data = {}

            async with client.messages.stream(**_make_api_params(req)) as stream:
                async for event in stream:
                    event_type = type(event).__name__

                    # Track thinking vs text blocks — suppress thinking from stream
                    if event_type == "ContentBlockStartEvent":
                        block = event.content_block
                        if hasattr(block, "type"):
                            in_thinking_block = (block.type == "thinking")

                    elif event_type == "ContentBlockDeltaEvent":
                        delta = event.delta
                        if not in_thinking_block and hasattr(delta, "text"):
                            yield f'data: {json.dumps({"type": "text_delta", "text": delta.text})}\n\n'

                    elif event_type == "ContentBlockStopEvent":
                        in_thinking_block = False

                    elif event_type == "MessageStreamEvent":
                        pass  # handled via per-block events above

                # Collect final usage after stream completes
                final_msg = await stream.get_final_message()
                u = final_msg.usage
                usage_data = {
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0),
                    "cache_created_tokens": getattr(u, "cache_creation_input_tokens", 0),
                }

            log.info(
                "ai_filter.analysis_stream_complete",
                strategy=strategy,
                symbol=symbol,
                **usage_data,
            )
            yield f'data: {json.dumps({"type": "done", "model": settings.AI_ANALYSIS_MODEL, "usage": usage_data})}\n\n'

        except asyncio.CancelledError:
            yield f'data: {json.dumps({"type": "error", "message": "Client disconnected"})}\n\n'
        except anthropic.APIError as exc:
            log.error("ai_filter.stream_api_error", strategy=strategy, error=str(exc))
            yield f'data: {json.dumps({"type": "error", "message": str(exc)[:100]})}\n\n'
        except Exception as exc:
            log.error("ai_filter.stream_error", strategy=strategy, error=str(exc))
            yield f'data: {json.dumps({"type": "error", "message": str(exc)[:100]})}\n\n'

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering if behind proxy
        },
    )
