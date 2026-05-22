"""
Market regime detection — Claude Sonnet classifies current market conditions
to help operators and the strategy service understand whether current
conditions favour each strategy type.

Regime classifications:
  trending       — directional momentum; stat_arb (mean-reversion) performs poorly
  mean_reverting — price oscillates around equilibrium; stat_arb thrives
  volatile       — high VIX-like conditions; widen spreads, reduce sizing
  ranging        — low volatility consolidation; funding_arb most reliable
  crisis         — extreme conditions; halt discretionary strategies

This endpoint is called:
  - By the dashboard on load (overview tab)
  - By the strategy service before generating signals (optional context)
  - Manually by operators before enabling live trading

Uses Claude Sonnet (not Haiku) — regime analysis is a higher-stakes judgment
that benefits from deeper reasoning. Extended thinking enabled.
Prompt caching on system prompt.
"""

import json
from datetime import datetime, timezone

import anthropic
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import settings

log = structlog.get_logger()
router = APIRouter()

_REGIME_SYSTEM_PROMPT = """\
You are a senior quantitative analyst specialising in market microstructure
and regime detection for crypto and FX markets.

You classify the current market regime based on provided metrics and explain
implications for each strategy type used by MeznaQuantFX:

STRATEGIES:
- funding_arb  : Long spot / short perp. Best in ranging/low-vol regimes with
                 persistent positive funding. Harmed by sudden trend reversals.
- stat_arb     : Pairs mean-reversion. Best in mean-reverting regimes.
                 Harmed by trending markets where pairs diverge further.
- tv_signal    : TradingView discretionary. Works in trending regimes but
                 requires tight risk management in volatile conditions.

REGIME DEFINITIONS:
- trending       : Strong directional movement, >60% of assets trending same direction
- mean_reverting : Price oscillates; rolling 24h range is compressed; Z-scores reliable
- volatile       : Rapid price swings; bid/ask spreads widening; funding spikes
- ranging        : Sideways consolidation; funding stable; low realised volatility
- crisis         : Extreme moves, liquidity impaired, spreads > 3x normal

Your output must use the classify_regime tool. Be conservative — when uncertain,
use the more cautious classification. Operators may use this to adjust risk limits.
"""

_REGIME_TOOL = {
    "name": "classify_regime",
    "description": "Classify the current market regime and assess strategy fitness.",
    "input_schema": {
        "type": "object",
        "properties": {
            "regime": {
                "type": "string",
                "enum": ["trending", "mean_reverting", "volatile", "ranging", "crisis"],
                "description": "Primary regime classification.",
            },
            "confidence": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Confidence in regime classification (0-100).",
            },
            "regime_summary": {
                "type": "string",
                "description": "2-3 sentence summary of current conditions. Max 80 words.",
            },
            "strategy_fitness": {
                "type": "object",
                "description": "Fitness score 0-100 for each strategy in current regime.",
                "properties": {
                    "funding_arb": {"type": "integer", "minimum": 0, "maximum": 100},
                    "stat_arb": {"type": "integer", "minimum": 0, "maximum": 100},
                    "tv_signal": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["funding_arb", "stat_arb", "tv_signal"],
            },
            "risk_adjustment": {
                "type": "string",
                "enum": ["normal", "reduce_sizing", "halt_discretionary", "halt_all"],
                "description": "Recommended risk posture for current conditions.",
            },
            "key_indicators": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 specific metrics that drove this classification.",
            },
        },
        "required": [
            "regime", "confidence", "regime_summary",
            "strategy_fitness", "risk_adjustment", "key_indicators",
        ],
    },
}


class RegimeDataPoint(BaseModel):
    symbol: str
    price_change_24h_pct: float | None = None
    price_change_1h_pct: float | None = None
    volume_24h_usd: float | None = None
    funding_rate: float | None = None       # Per 8h, as decimal
    spread_bps: float | None = None
    realized_vol_24h: float | None = None   # Annualised %
    z_score: float | None = None            # For stat_arb pairs


class RegimeRequest(BaseModel):
    """
    Market data snapshot for regime classification.
    Provide as many fields as available — Claude works with partial data.
    """
    data_points: list[RegimeDataPoint]
    paper_mode: bool = True
    operator_context: str | None = None     # e.g. "Major US CPI release in 2 hours"


_REGIME_CACHE_KEY = "ai:regime:latest"


@router.get("/regime/cached")
async def get_cached_regime(request: Request) -> JSONResponse:
    """
    Return the last cached regime classification without calling Claude.
    Returns 404 if no cached result exists yet.
    """
    redis = request.app.state.redis
    try:
        cached = await redis.get(_REGIME_CACHE_KEY)
        if cached:
            return JSONResponse(status_code=status.HTTP_200_OK, content=json.loads(cached))
    except Exception:
        pass
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": "No cached regime available. POST /ai/regime to generate one."},
    )


@router.post("/regime")
async def detect_regime(req: RegimeRequest, request: Request) -> JSONResponse:
    """
    Classify current market regime using Claude Sonnet + extended thinking.

    Returns regime classification, strategy fitness scores, and recommended
    risk posture. Dashboard displays this in the Overview tab.

    Result is cached in Redis for AI_REGIME_CACHE_TTL_SECONDS (default: 15 min).
    Use GET /ai/regime/cached to retrieve without re-running Claude.

    Typical latency: 10-20 seconds (extended thinking enabled).
    """
    if not settings.ai_configured:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "ANTHROPIC_API_KEY not configured"},
        )

    client: anthropic.AsyncAnthropic = request.app.state.anthropic_client
    redis = request.app.state.redis

    # Build the data prompt
    lines = [f"Market regime analysis — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"]
    lines.append("## CURRENT MARKET DATA\n")

    for dp in req.data_points:
        lines.append(f"### {dp.symbol}")
        if dp.price_change_1h_pct is not None:
            lines.append(f"  Price change 1h:  {dp.price_change_1h_pct:+.3f}%")
        if dp.price_change_24h_pct is not None:
            lines.append(f"  Price change 24h: {dp.price_change_24h_pct:+.3f}%")
        if dp.funding_rate is not None:
            lines.append(f"  Funding rate:     {dp.funding_rate * 100:.4f}% per 8h")
        if dp.spread_bps is not None:
            lines.append(f"  Spread:           {dp.spread_bps:.1f} bps")
        if dp.realized_vol_24h is not None:
            lines.append(f"  Realized vol 24h: {dp.realized_vol_24h:.1f}% annualised")
        if dp.z_score is not None:
            lines.append(f"  Z-score:          {dp.z_score:.3f}")
        if dp.volume_24h_usd is not None:
            lines.append(f"  Volume 24h:       ${dp.volume_24h_usd:,.0f}")
        lines.append("")

    lines.append(f"Mode: {'PAPER' if req.paper_mode else 'LIVE'}")
    if req.operator_context:
        lines.append(f"\nOperator context: {req.operator_context}")

    lines.append("\nClassify the market regime and assess strategy fitness.")
    user_prompt = "\n".join(lines)

    try:
        response = await client.messages.create(
            model=settings.AI_ANALYSIS_MODEL,
            max_tokens=4096,
            temperature=1,      # Required for extended thinking
            thinking={
                "type": "enabled",
                "budget_tokens": 3000,   # Smaller budget — regime is faster to reason
            },
            system=[
                {
                    "type": "text",
                    "text": _REGIME_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[_REGIME_TOOL],
            tool_choice={"type": "tool", "name": "classify_regime"},
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Extract tool_use block
        result = None
        for block in response.content:
            if block.type == "tool_use" and block.name == "classify_regime":
                result = block.input
                break

        if result is None:
            raise ValueError("Claude did not call classify_regime tool")

        usage = response.usage
        log.info(
            "ai_filter.regime_classified",
            regime=result.get("regime"),
            confidence=result.get("confidence"),
            risk_adjustment=result.get("risk_adjustment"),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

        response_body = {
            **result,
            "model": settings.AI_ANALYSIS_MODEL,
            "thinking_enabled": True,
            "symbols_analysed": [dp.symbol for dp in req.data_points],
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Cache in Redis — regime analysis is expensive; reuse for TTL window
        try:
            await redis.set(
                _REGIME_CACHE_KEY,
                json.dumps(response_body),
                ex=settings.AI_REGIME_CACHE_TTL_SECONDS,
            )
        except Exception as cache_exc:
            log.warning("ai_filter.regime_cache_failed", error=str(cache_exc))

        return JSONResponse(status_code=status.HTTP_200_OK, content=response_body)

    except anthropic.APIError as exc:
        log.error("ai_filter.regime_api_error", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": f"Anthropic API error: {str(exc)[:100]}"},
        )
    except Exception as exc:
        log.error("ai_filter.regime_error", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(exc)[:100]},
        )
