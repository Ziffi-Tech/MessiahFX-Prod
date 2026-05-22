"""
Kelly Position Sizing Advisory — GET /ai/portfolio/sizing

Computes half-Kelly optimal position sizes per strategy based on:
  - 30-day win rate and edge ratio from the trade journal
  - Current market regime fitness score (from Redis cache)
  - Live risk state: drawdown and consecutive losses apply further scaling

Kelly formula (half-Kelly for safety):
  f* = (win_rate - (1 - win_rate) / edge_ratio) × 0.5
  Capped hard at 5% of capital — non-negotiable safety rail.

Scaling applied on top of Kelly:
  × regime_scale  — reduced in volatile/crisis regimes (0.1 → 1.0)
  × drawdown_scale — reduced when daily drawdown > 1.5% (0.5 → 1.0)
  × loss_scale    — reduced after consecutive losses (0.5 → 1.0)

Claude Sonnet provides a qualitative overlay on top of the numbers:
  - Is the Kelly estimate trustworthy? (sufficient trade count?)
  - Does the regime fitness support the computed allocation?
  - Concrete sizing actions per strategy.

Typical latency: 4–8 seconds.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import anthropic
import httpx
import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from ..config import settings

log = structlog.get_logger()
router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────────

_MIN_TRADES_FOR_KELLY = 15   # Fewer trades → Kelly not trustworthy
_KELLY_MAX_PCT = 0.05         # Hard cap: 5% of capital per strategy

STRATEGIES = ["funding_arb", "stat_arb", "tv_signal"]

# ── Claude system prompt ──────────────────────────────────────────────────────

_SIZING_SYSTEM = """\
You are MeznaQuantFX's position sizing advisor. You receive half-Kelly fractions
computed from live trade data and provide a quantitative recommendation.

YOUR ROLE:
- Validate whether Kelly estimates are reliable (is trade count sufficient?)
- Adjust for market regime: volatile/crisis → reduce; trending → full Kelly
- Factor in risk state: drawdown > 1% → scale down; consecutive losses → reduce
- Produce specific, actionable recommendations per strategy

OUTPUT FORMAT:

## Position Sizing Recommendation

### [Strategy Name]
- Computed half-Kelly: X.XX%
- Recommended size: X.XX% [may differ from Kelly based on conditions]
- Confidence: High / Medium / Low
- Rationale: One sentence explaining the key factor driving this recommendation.

(Repeat for each strategy with data)

## Overall Assessment
One paragraph on portfolio-wide sizing posture and key risks.

## Key Monitoring Triggers
Bullet list of 3-4 conditions that should prompt a sizing review.

HARD CONSTRAINTS (non-negotiable):
- Never recommend > 5% per strategy — the system already caps at this level
- If trade count < 15, explicitly flag as "insufficient history — use default 1%"
- If drawdown > 2%, recommend reducing all sizes by 25–50%
- If regime = crisis, recommend reducing to minimum (0.25–0.5%)
- Never recommend overriding risk engine position limits
"""


# ── Kelly computation ─────────────────────────────────────────────────────────

def _compute_kelly(win_rate: float, edge_ratio: float) -> float:
    """
    Half-Kelly: f* = (win_rate - (1 - win_rate) / edge_ratio) × 0.5
    Returns fraction (0.0 → KELLY_MAX_PCT). Returns 0 when Kelly is negative.
    edge_ratio = avg_win / avg_loss (both positive)
    """
    if edge_ratio <= 0 or win_rate <= 0:
        return 0.0
    kelly = win_rate - (1.0 - win_rate) / edge_ratio
    half_kelly = kelly * 0.5
    return round(max(0.0, min(half_kelly, _KELLY_MAX_PCT)), 5)


# ── Data fetchers ─────────────────────────────────────────────────────────────

async def _fetch_kelly_stats(
    http: httpx.AsyncClient,
    journal_url: str,
    strategy: str,
    days: int = 30,
) -> Optional[dict]:
    """
    Fetch exact Kelly sizing inputs from the journal's /pnl/kelly-stats endpoint.
    Returns None on HTTP/network error.

    The returned dict includes `realized_pnl_populated` (bool) — when False,
    avg_win_usd and avg_loss_usd are zero (Phase 7 not yet live) and Kelly
    computation will fall back to the default 1% allocation.
    """
    try:
        resp = await http.get(
            f"{journal_url}/pnl/kelly-stats",
            params={"days": days, "strategy_type": strategy},
            timeout=8.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        log.warning("kelly.stats_fetch_failed", strategy=strategy, error=str(exc)[:60])
    return None


async def _fetch_risk_state(
    http: httpx.AsyncClient, risk_url: str
) -> dict:
    """Fetch live risk engine state. Returns empty dict on error."""
    try:
        resp = await http.get(f"{risk_url}/health/state", timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        log.warning("kelly.risk_state_failed", error=str(exc)[:60])
    return {}


# ── Main endpoint ─────────────────────────────────────────────────────────────

@router.get("/portfolio/sizing")
async def portfolio_sizing(request: Request) -> JSONResponse:
    """
    Compute half-Kelly position sizing per strategy with Claude qualitative overlay.

    Fetches 30-day trade history per strategy, computes half-Kelly fractions,
    applies regime and risk-state scaling, then asks Claude Sonnet for a
    qualitative assessment and final recommendation.

    Returns per-strategy: kelly_pct, recommended_pct, confidence, rationale.
    Returns 503 if ANTHROPIC_API_KEY is not configured.
    """
    if not settings.ai_configured:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "ANTHROPIC_API_KEY not configured"},
        )

    http: httpx.AsyncClient = request.app.state.agent_http_client
    redis = request.app.state.redis
    client: anthropic.AsyncAnthropic = request.app.state.anthropic_client

    # ── 1. Live risk state ────────────────────────────────────────────────────
    risk_state = await _fetch_risk_state(http, settings.RISK_URL)
    drawdown_pct: float = risk_state.get("daily_drawdown_pct") or 0.0
    consec_losses: int = risk_state.get("consecutive_losses") or 0
    is_halted: bool = bool(risk_state.get("halted", False))

    # ── 2. Market regime from cache ───────────────────────────────────────────
    regime = "unknown"
    strategy_fitness: dict = {}
    try:
        cached_regime = await redis.get("ai:regime:latest")
        if cached_regime:
            rd = json.loads(cached_regime)
            regime = rd.get("regime", "unknown")
            strategy_fitness = rd.get("strategy_fitness", {})
    except Exception:
        pass

    # ── 3. Drawdown & loss scaling factors (applied to all strategies) ────────
    if drawdown_pct > 0.025:          # > 2.5% — approaching auto-halt
        drawdown_scale = 0.40
    elif drawdown_pct > 0.015:        # > 1.5%
        drawdown_scale = 0.65
    elif drawdown_pct > 0.01:         # > 1.0%
        drawdown_scale = 0.85
    else:
        drawdown_scale = 1.0

    if consec_losses >= 4:
        loss_scale = 0.50
    elif consec_losses >= 3:
        loss_scale = 0.65
    elif consec_losses >= 2:
        loss_scale = 0.80
    else:
        loss_scale = 1.0

    # ── 4. Per-strategy Kelly ─────────────────────────────────────────────────
    sizing_data = []

    for strategy in STRATEGIES:
        stats = await _fetch_kelly_stats(http, settings.JOURNAL_URL, strategy)

        if stats is None:
            sizing_data.append({
                "strategy": strategy,
                "trade_count": 0,
                "win_rate": None,
                "avg_win_usd": None,
                "avg_loss_usd": None,
                "edge_ratio": None,
                "kelly_pct": None,
                "recommended_pct": 1.0,
                "confidence": "low",
                "regime_fitness": strategy_fitness.get(strategy),
                "data_quality": "journal_unavailable",
                "note": "Journal unreachable — using default 1% allocation",
            })
            continue

        total: int = stats.get("total_filled_trades", 0) or 0
        win_rate: float = stats.get("win_rate", 0.0) or 0.0
        avg_win: float = stats.get("avg_win_usd", 0.0) or 0.0
        avg_loss: float = stats.get("avg_loss_usd", 0.0) or 0.0
        edge_ratio: float = stats.get("edge_ratio", 0.0) or 0.0
        pnl_live: bool = stats.get("realized_pnl_populated", False)

        # Kelly is only meaningful when:
        #   (a) enough trade history, AND
        #   (b) realized P&L has been populated (Phase 7+)
        if total >= _MIN_TRADES_FOR_KELLY and pnl_live:
            kelly_fraction = _compute_kelly(win_rate, edge_ratio)
            data_quality = "live"
        elif total >= _MIN_TRADES_FOR_KELLY and not pnl_live:
            # Trade count is good but P&L not yet tracked — use default
            kelly_fraction = 0.01
            data_quality = "pending_phase7"
        else:
            kelly_fraction = 0.01
            data_quality = "insufficient_history"

        # Confidence tier
        if not pnl_live or total < _MIN_TRADES_FOR_KELLY:
            confidence = "low"
        elif total < 30:
            confidence = "medium"
        else:
            confidence = "high"

        # Regime scaling: base on strategy fitness score (0-100) or hard overrides
        fitness = strategy_fitness.get(strategy, 50)
        if regime == "crisis":
            regime_scale = 0.10
        elif regime == "volatile":
            regime_scale = 0.50
        elif isinstance(fitness, (int, float)):
            # Map fitness 0-100 → scale 0.30-1.00
            regime_scale = max(0.30, min(1.0, fitness / 100.0))
        else:
            regime_scale = 1.0

        recommended_fraction = kelly_fraction * regime_scale * drawdown_scale * loss_scale
        # Respect the hard cap again after scaling (in case kelly_fraction was default 1%)
        recommended_fraction = min(recommended_fraction, _KELLY_MAX_PCT)

        sizing_data.append({
            "strategy": strategy,
            "trade_count": total,
            "win_rate": round(win_rate, 4),
            "avg_win_usd": round(avg_win, 4),
            "avg_loss_usd": round(avg_loss, 4),
            "edge_ratio": round(edge_ratio, 4),
            "kelly_pct": round(kelly_fraction * 100, 3),
            "recommended_pct": round(recommended_fraction * 100, 3),
            "confidence": confidence,
            "regime_fitness": fitness,
            "regime_scale": round(regime_scale, 3),
            "drawdown_scale": round(drawdown_scale, 3),
            "consecutive_loss_scale": round(loss_scale, 3),
            "data_quality": data_quality,
            "note": (
                "Waiting for Phase 7 position-close tracking — using default 1% allocation"
                if data_quality == "pending_phase7"
                else f"Insufficient history ({total} trades; need {_MIN_TRADES_FOR_KELLY}) — using default 1%"
                if data_quality == "insufficient_history"
                else f"Based on {total} filled trades over 30 days (realized P&L verified)"
            ),
        })

    # ── 5. Build prompt for Claude ────────────────────────────────────────────
    prompt_lines = [
        f"Market Regime: {regime}",
        f"Daily Drawdown: {drawdown_pct:.2%}",
        f"Consecutive Losses: {consec_losses}",
        f"Open Positions: {risk_state.get('open_positions', 'N/A')}",
        f"Trading Halted: {is_halted}",
        "",
        f"Drawdown Scale Applied: {drawdown_scale:.2f}",
        f"Consecutive-Loss Scale Applied: {loss_scale:.2f}",
        "",
        "Half-Kelly Sizing Data Per Strategy:",
    ]
    for s in sizing_data:
        prompt_lines.append(
            f"  {s['strategy']}: {s['trade_count']} trades | "
            f"win_rate={s['win_rate']} | avg_win=${s['avg_win_usd']} | avg_loss=${s['avg_loss_usd']} | "
            f"edge_ratio={s['edge_ratio']} | half_kelly={s['kelly_pct']}% → recommended={s['recommended_pct']}% | "
            f"confidence={s['confidence']} | regime_fitness={s['regime_fitness']} | "
            f"data_quality={s['data_quality']}"
        )

    prompt_lines.append("\nProvide your position sizing assessment and recommendation.")
    prompt = "\n".join(prompt_lines)

    # ── 6. Claude qualitative overlay ─────────────────────────────────────────
    recommendation_text = ""
    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.AI_ANALYSIS_MODEL,
                max_tokens=1200,
                temperature=0.3,
                system=[
                    {
                        "type": "text",
                        "text": _SIZING_SYSTEM,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=30.0,
        )
        for block in response.content:
            if hasattr(block, "text") and block.type == "text":
                recommendation_text = block.text
                break
    except asyncio.TimeoutError:
        recommendation_text = "AI overlay timed out — use computed Kelly values directly."
    except anthropic.APIError as exc:
        log.error("kelly.claude_api_error", error=str(exc)[:100])
        recommendation_text = "AI overlay unavailable — use computed Kelly values directly."
    except Exception as exc:
        log.error("kelly.claude_error", error=str(exc)[:100])
        recommendation_text = "AI overlay unavailable — use computed Kelly values directly."

    log.info(
        "kelly.computed",
        strategies=STRATEGIES,
        regime=regime,
        drawdown_pct=drawdown_pct,
        consec_losses=consec_losses,
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "sizing": sizing_data,
            "regime": regime,
            "risk_state_summary": {
                "daily_drawdown_pct": drawdown_pct,
                "consecutive_losses": consec_losses,
                "open_positions": risk_state.get("open_positions"),
                "halted": is_halted,
            },
            "global_scales": {
                "drawdown_scale": drawdown_scale,
                "consecutive_loss_scale": loss_scale,
            },
            "recommendation": recommendation_text,
            "kelly_cap_pct": _KELLY_MAX_PCT * 100,
            "min_trades_for_kelly": _MIN_TRADES_FOR_KELLY,
            "model": settings.AI_ANALYSIS_MODEL,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
