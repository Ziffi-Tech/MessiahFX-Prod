"""
Claude Haiku opportunity scorer.

Contract (non-negotiable):
  - score_opportunity() NEVER raises. All errors → timeout=True, score=None.
  - If Claude times out → pass-through. The risk engine decides alone.
  - The AI score is ADVISORY. A score of 0 does not block a trade.
  - NEVER log the full opportunity payload at INFO level (may contain prices).

Implementation:
  - Tool use (score_signal) — Claude MUST call the tool; no text parsing needed.
    Falls back to regex extraction if the tool block is absent (defensive).
  - Prompt caching — system prompt cached 5 min; 60-80% token cost reduction
    at high signal frequency.
  - temperature=0 — deterministic, reproducible scoring for audit trail.
  - max_tokens=256 — tool envelope uses ~100 tokens on top of score+reason.

Score interpretation (advisory guide for operators):
  80-100  Strong signal — all metrics align, low execution risk
  50-79   Decent signal — some uncertainty or marginal conditions
  20-49   Weak signal — thin edge, wide spreads, or data quality concerns
  0-19    Adverse signal — data appears suspect, do not recommend
"""

import asyncio
import json
import re
from typing import Optional

import anthropic
import structlog
from redis.asyncio import Redis

from .config import Settings

log = structlog.get_logger()

# ── Tool definition — Claude MUST call this. Eliminates text/JSON parsing. ────
_SCORE_TOOL = {
    "name": "score_signal",
    "description": (
        "Score the arbitrage trading signal quality from 0 to 100 "
        "and provide a single concise reason."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Signal quality score: 80-100 strong, 50-79 moderate, 20-49 weak, 0-19 reject.",
            },
            "reason": {
                "type": "string",
                "description": "One sentence max 20 words explaining the score.",
            },
        },
        "required": ["score", "reason"],
    },
}

_SYSTEM_PROMPT = """\
You are a senior quantitative analyst reviewing arbitrage signals for MeznaQuantFX.
Your assessment is ADVISORY ONLY — hard risk controls always override your score.

STRATEGIES IN USE:
- funding_arb: Long spot / short perpetual when funding rate is persistently positive.
  Healthy edge: net_edge >= 5 bps after costs. Suspicious if > 50 bps (data error likely).
  Typical BTC/ETH funding: 0.005%–0.03% per 8h. > 0.1%/8h = elevated, verify carefully.

- stat_arb: Mean-reversion on cointegrated pair when Z-score deviates from equilibrium.
  Entry signal: |Z-score| >= 1.5. Strong signal: |Z-score| >= 2.0. Extreme: > 4.0 (possible regime break).
  Typical holding: 1–4 hours. Net edge should exceed 3 bps minimum.

- tv_signal: Discretionary signal from TradingView webhook. Score on consistency of
  direction, plausible edge, and absence of data anomalies.

SCORE 0-100 on edge reliability and execution risk:
  80-100  Strong: edge clear, costs normal, data consistent, conditions stable
  50-79   Moderate: decent edge but some uncertainty or marginal cost structure
  20-49   Weak: thin edge (< 3 bps net), wide spreads, or Z-score near threshold only
  0-19    Reject: likely data error, extreme/implausible values, or adverse conditions

RED FLAGS (lean toward 0-30):
- Net edge <= 0 bps (costs exceed gross)
- Funding rate > 0.15%/8h (probable data spike, not real opportunity)
- Z-score > 5.0 (regime break, not mean-reversion)
- Spread > 20 bps (execution will consume edge)
- LIVE mode with edge < 5 bps (insufficient margin of safety)

Respond with ONLY valid JSON — no preamble, no markdown:
{"score": <integer 0-100>, "reason": "<one sentence, max 20 words>"}\
"""


async def _read_sentiment_context(
    redis: Optional[Redis], payload: dict
) -> Optional[str]:
    """
    Read cached news sentiment from Redis and format it as a prompt snippet.
    Returns None if Redis is unavailable or no sentiment is cached.
    Cost: < 1ms (single Redis GET from local cache).
    """
    if redis is None:
        return None

    # Determine asset class from the strategy/symbols
    strategy = payload.get("strategy_type", "")
    symbol = payload.get("symbol_primary", "").upper()

    # FX instruments: oanda venue or common FX symbols
    is_fx = (
        payload.get("venue") == "oanda"
        or "_" in symbol  # Oanda format: EUR_USD
        or any(ccy in symbol for ccy in ("EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"))
    )
    asset_class = "fx" if is_fx else "crypto"

    try:
        cached = await redis.get(f"ai:sentiment:{asset_class}")
        if not cached:
            return None
        data = json.loads(cached)
        score = data.get("score", 0)
        urgency = data.get("urgency", "low")
        summary = data.get("summary", "")
        themes = ", ".join(data.get("key_themes", []))
        updated = data.get("updated_at", "")[:16]  # trim to minute

        sentiment_label = (
            "strongly bullish" if score >= 60 else
            "mildly bullish" if score >= 20 else
            "neutral" if score >= -20 else
            "mildly bearish" if score >= -60 else
            "strongly bearish"
        )
        return (
            f"\n[News Sentiment — {asset_class.upper()} as of {updated} UTC]\n"
            f"Score: {score:+d} ({sentiment_label})  Urgency: {urgency}\n"
            f"Summary: {summary}\n"
            f"Key themes: {themes}"
        )
    except Exception:
        return None


def _build_prompt(payload: dict, sentiment_context: Optional[str] = None) -> str:
    """
    Build a compact, Haiku-optimised prompt from the opportunity payload.
    Include only the metrics relevant to evaluating signal quality.
    Appends cached news sentiment context when available.
    """
    strategy = payload.get("strategy_type", "unknown")
    symbol_primary = payload.get("symbol_primary", "—")
    symbol_secondary = payload.get("symbol_secondary") or "—"
    net_edge = payload.get("net_edge_bps")
    expected = payload.get("expected_return_bps")
    fees = payload.get("fee_cost_bps")
    spread = payload.get("spread")
    z_score = payload.get("z_score")
    funding_rate = payload.get("funding_rate")
    paper_mode = payload.get("paper_mode", True)

    lines = [
        f"Strategy: {strategy}",
        f"Pair: {symbol_primary} / {symbol_secondary}",
        f"Net edge: {net_edge} bps  (gross {expected} bps, costs {fees} bps)",
    ]

    if spread is not None:
        lines.append(f"Spread: {spread} bps")

    if funding_rate is not None:
        pct_per_8h = funding_rate * 100
        lines.append(f"Funding rate: {pct_per_8h:.4f}% per 8h")

    if z_score is not None:
        lines.append(f"Z-score: {z_score:.4f}")

    lines.append(f"Mode: {'PAPER' if paper_mode else 'LIVE'}")

    if sentiment_context:
        lines.append(sentiment_context)

    lines.append("\nScore this signal's quality.")
    return "\n".join(lines)


def _extract_from_tool_use(response) -> tuple[int | None, str | None]:
    """
    Extract score and reason from a tool_use content block.
    This is the primary extraction path when tool_choice is forced.
    Returns (None, None) if no tool_use block is present.
    """
    for block in response.content:
        if block.type == "tool_use" and block.name == "score_signal":
            try:
                score = int(block.input["score"])
                score = max(0, min(100, score))
                reason = str(block.input.get("reason", ""))[:120]
                return score, reason
            except (KeyError, TypeError, ValueError):
                return None, None
    return None, None


def _extract_from_text(text: str) -> tuple[int | None, str | None]:
    """
    Fallback: extract score/reason from raw text when tool block is absent.
    Tries strict JSON first, then regex. Used only when tool_use is unavailable.
    """
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{[^}]+\}', text, re.DOTALL)
        if not match:
            return None, None
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return None, None

    try:
        score = int(data["score"])
        score = max(0, min(100, score))
    except (KeyError, TypeError, ValueError):
        return None, None

    reason = str(data.get("reason", ""))[:120]
    return score, reason


async def score_opportunity(
    client: anthropic.AsyncAnthropic,
    settings: Settings,
    opportunity_payload: dict,
    redis: Optional[Redis] = None,
) -> dict:
    """
    Score one opportunity. Always returns a result dict — never raises.

    Args:
        client:              Anthropic async client
        settings:            Service configuration
        opportunity_payload: Signal dict from the Redis stream
        redis:               Optional Redis connection for reading cached news sentiment.
                             When provided, up-to-date sentiment context is prepended
                             to the scoring prompt at < 1ms cost.

    Returns:
        {
            "score":   int | None  — 0-100, or None on timeout/error
            "reason":  str | None  — brief reasoning, or None
            "timeout": bool        — True if Claude API call timed out or errored
        }
    """
    sentiment_context = await _read_sentiment_context(redis, opportunity_payload)
    prompt = _build_prompt(opportunity_payload, sentiment_context)
    timeout_secs = settings.AI_TIMEOUT_MS / 1000.0
    strategy = opportunity_payload.get("strategy_type", "?")
    symbol = opportunity_payload.get("symbol_primary", "?")

    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.AI_SCORING_MODEL,
                max_tokens=256,     # Tool envelope needs ~100 tokens on top of answer
                temperature=0.0,    # Deterministic — reproducible for audit trail
                # Prompt caching: billed once per 5-min TTL; 60-80% cost saving at volume
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                # Tool use: force Claude to call score_signal — no text parsing needed
                tools=[_SCORE_TOOL],
                tool_choice={"type": "tool", "name": "score_signal"},
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=timeout_secs,
        )

        # Primary: extract from tool_use block (forced by tool_choice)
        score, reason = _extract_from_tool_use(response)

        # Defensive fallback: if somehow no tool block, try text extraction
        if score is None:
            text_blocks = [b.text for b in response.content if hasattr(b, "text")]
            if text_blocks:
                score, reason = _extract_from_text(text_blocks[0])

        if score is None:
            log.warning(
                "ai_filter.unparseable_response",
                strategy=strategy,
                symbol=symbol,
                stop_reason=response.stop_reason,
            )
            return {"score": None, "reason": "parse_error", "timeout": False}

        log.info(
            "ai_filter.scored",
            strategy=strategy,
            symbol=symbol,
            score=score,
            reason=reason,
            net_edge_bps=opportunity_payload.get("net_edge_bps"),
        )
        return {"score": score, "reason": reason, "timeout": False}

    except asyncio.TimeoutError:
        log.warning(
            "ai_filter.timeout",
            strategy=strategy,
            symbol=symbol,
            timeout_ms=settings.AI_TIMEOUT_MS,
        )
        return {"score": None, "reason": None, "timeout": True}

    except anthropic.APIError as exc:
        log.error("ai_filter.api_error", strategy=strategy, error=str(exc))
        return {"score": None, "reason": None, "timeout": True}

    except Exception as exc:
        log.error("ai_filter.unexpected_error", strategy=strategy, error=str(exc))
        return {"score": None, "reason": None, "timeout": True}
