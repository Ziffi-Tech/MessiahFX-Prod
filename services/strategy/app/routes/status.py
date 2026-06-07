"""
Strategy operational status + configuration endpoints.

GET  /strategy/configs         — list all strategy configs (from Redis state)
PATCH /strategy/configs/{name} — update a strategy config (enable/disable, latency)
GET  /strategy/rotation        — rotation engine state
GET  /strategy/edge            — rolling win-rate + edge decay state
GET  /strategy/drawdown        — per-strategy cumulative P&L proxy and drawdown
GET  /strategy/overview        — all three combined (single dashboard call)

Strategy config is stored in Redis hashes (strategy:state:{name}).
No database query needed — Redis is the authoritative config store.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()

_ALL_STRATEGIES = (
    "funding_arb", "stat_arb", "swing",
    "breakout", "mean_reversion_scalp", "momentum",
)

_ROTATION_THRESHOLD = 4
_EDGE_BASELINE      = 0.55


@router.get("/rotation")
async def rotation_status(request: Request) -> JSONResponse:
    """
    Return strategy rotation engine state.

    Reads the same Redis keys the executor consumer writes after each trade.
    Fields:
      preferred_strategy  — the currently rotation-preferred alternative (or null)
      current_regime      — ai:regime:current
      local_regime        — ai:regime:local (fast local detector result)
      strategies          — per-strategy: consecutive_losses, degraded, on_cooldown
    """
    redis = request.app.state.redis

    preferred_raw = await redis.get("strategy:rotation:preferred")
    preferred = preferred_raw.decode() if preferred_raw else None

    regime_raw = await redis.get("ai:regime:current") or b"unknown"
    regime = regime_raw.decode() if isinstance(regime_raw, bytes) else "unknown"

    local_regime_raw = await redis.get("ai:regime:local")
    local_regime = local_regime_raw.decode() if local_regime_raw else None

    strategies = {}
    for name in _ALL_STRATEGIES:
        losses_raw = await redis.get(f"strategy:consecutive_losses:{name}")
        losses = int(losses_raw) if losses_raw else 0
        degraded = bool(await redis.exists(f"strategy:degraded:{name}"))
        strategies[name] = {
            "consecutive_losses": losses,
            "degraded": degraded,
            "threshold": _ROTATION_THRESHOLD,
            "loss_pct": round(losses / _ROTATION_THRESHOLD * 100) if _ROTATION_THRESHOLD > 0 else 0,
        }

    return JSONResponse(content={
        "preferred_strategy": preferred,
        "current_regime": regime,
        "local_regime": local_regime,
        "strategies": strategies,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/edge")
async def edge_monitor(request: Request) -> JSONResponse:
    """
    Return alpha/edge decay status for all strategies.

    Fields per strategy:
      win_rate     — rolling window win rate (0.0–1.0), null if no data yet
      window_size  — number of trades in the rolling window
      decayed      — True when win_rate < baseline − threshold
      recent       — last 10 outcome bits (1=win, 0=loss) for sparkline
    """
    redis = request.app.state.redis
    strategies = {}

    for name in _ALL_STRATEGIES:
        win_rate_raw = await redis.get(f"edge:win_rate:{name}")
        decayed = bool(await redis.exists(f"edge:decayed:{name}"))
        raw = await redis.lrange(f"edge:outcomes:{name}", 0, -1)
        outcomes = [int(o) for o in raw]

        strategies[name] = {
            "win_rate": float(win_rate_raw) if win_rate_raw else None,
            "window_size": len(outcomes),
            "decayed": decayed,
            "baseline_win_rate": _EDGE_BASELINE,
            "recent": outcomes[-10:],
        }

    return JSONResponse(content={
        "strategies": strategies,
        "baseline_win_rate": _EDGE_BASELINE,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/drawdown")
async def strategy_drawdown(request: Request) -> JSONResponse:
    """
    Return per-strategy cumulative proxy P&L and drawdown from peak.

    Note: P&L is a proxy (net_edge_bps × position_usd) until Phase 4
    adds real realized P&L tracking.
    """
    redis = request.app.state.redis
    strategies = {}

    for name in _ALL_STRATEGIES:
        pnl_raw    = await redis.get(f"strategy:cum_pnl:{name}")
        peak_raw   = await redis.get(f"strategy:peak_pnl:{name}")
        dd_raw     = await redis.get(f"strategy:drawdown_pct:{name}")
        avg_win    = await redis.get(f"strategy:avg_win:{name}")
        avg_loss   = await redis.get(f"strategy:avg_loss:{name}")

        strategies[name] = {
            "cum_pnl_usd":    float(pnl_raw)  if pnl_raw  else None,
            "peak_pnl_usd":   float(peak_raw) if peak_raw else None,
            "drawdown_pct":   float(dd_raw)   if dd_raw   else None,
            "avg_win_usd":    float(avg_win)  if avg_win  else None,
            "avg_loss_usd":   float(avg_loss) if avg_loss else None,
        }

    return JSONResponse(content={
        "strategies": strategies,
        "note": "proxy_pnl_until_phase4",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/overview")
async def strategy_overview(request: Request) -> JSONResponse:
    """
    Single endpoint returning rotation + edge + drawdown for all strategies.
    The dashboard calls this once instead of making 3 separate requests.

    Uses a single Redis pipeline for all reads (1 round trip regardless of
    strategy count), replacing the prior N × 13 sequential gather pattern.
    """
    redis = request.app.state.redis

    # ── Build pipeline: shared keys + per-strategy keys ───────────────────
    # Shared keys issued once (not per strategy)
    SHARED = ["ai:regime:current", "ai:regime:local", "strategy:rotation:preferred"]
    # Per-strategy key templates (GET unless noted)
    PER_STRAT_GETS = [
        "strategy:consecutive_losses:{n}",
        "strategy:cum_pnl:{n}",
        "strategy:peak_pnl:{n}",
        "strategy:drawdown_pct:{n}",
        "edge:win_rate:{n}",
        "strategy:avg_win:{n}",
        "strategy:avg_loss:{n}",
    ]
    PER_STRAT_EXISTS = [
        "strategy:degraded:{n}",
        "edge:decayed:{n}",
    ]

    pipe = redis.pipeline(transaction=False)
    for key in SHARED:
        pipe.get(key)
    for name in _ALL_STRATEGIES:
        for tmpl in PER_STRAT_GETS:
            pipe.get(tmpl.format(n=name))
        for tmpl in PER_STRAT_EXISTS:
            pipe.exists(tmpl.format(n=name))
        pipe.lrange(f"edge:outcomes:{name}", 0, -1)

    results = await pipe.execute()

    # ── Unpack shared results ─────────────────────────────────────────────
    def _str(v) -> str | None:
        return v.decode() if isinstance(v, bytes) else (v if v else None)

    regime_raw, local_raw, preferred_raw = results[:3]
    current_regime = _str(regime_raw) or "unknown"
    local_regime   = _str(local_raw)
    preferred      = _str(preferred_raw)

    # ── Unpack per-strategy results ───────────────────────────────────────
    # Each strategy block: 7 GETs + 2 EXISTS + 1 LRANGE = 10 values
    BLOCK = len(PER_STRAT_GETS) + len(PER_STRAT_EXISTS) + 1
    strategies = {}
    offset = 3  # skip the 3 shared results

    for name in _ALL_STRATEGIES:
        block = results[offset : offset + BLOCK]
        offset += BLOCK

        (losses_raw, pnl_raw, peak_raw, dd_raw,
         win_rate_raw, avg_win_raw, avg_loss_raw,
         degraded_count, decayed_count,
         outcomes_raw) = block

        def _f(v) -> float | None:
            if v is None:
                return None
            s = v.decode() if isinstance(v, bytes) else str(v)
            try:
                return float(s)
            except (ValueError, TypeError):
                return None

        outcomes = [int(o) for o in outcomes_raw]
        strategies[name] = {
            "rotation": {
                "consecutive_losses": int(_str(losses_raw) or 0),
                "degraded": bool(degraded_count),
                "threshold": _ROTATION_THRESHOLD,
            },
            "edge": {
                "win_rate":    _f(win_rate_raw),
                "window_size": len(outcomes),
                "decayed":     bool(decayed_count),
                "recent":      outcomes[-10:],
            },
            "drawdown": {
                "cum_pnl_usd":  _f(pnl_raw),
                "drawdown_pct": _f(dd_raw),
                "avg_win_usd":  _f(avg_win_raw),
                "avg_loss_usd": _f(avg_loss_raw),
            },
        }

    return JSONResponse(content={
        "current_regime":     current_regime,
        "local_regime":       local_regime,
        "preferred_strategy": preferred,
        "baseline_win_rate":  _EDGE_BASELINE,
        "rotation_threshold": _ROTATION_THRESHOLD,
        "strategies":         strategies,
        "timestamp":          datetime.now(timezone.utc).isoformat(),
    })


# ── Strategy config CRUD (Redis-backed) ───────────────────────────────────────

def _state_to_config(name: str, state: dict) -> dict[str, Any]:
    """Convert a Redis strategy:state hash to a StrategyConfig response."""
    enabled = state.get(b"enabled", state.get("enabled", "0"))
    paper   = state.get(b"paper_mode", state.get("paper_mode", "1"))
    latency = state.get(b"latency_profile", state.get("latency_profile", b"standard"))
    if isinstance(enabled, bytes): enabled = enabled.decode()
    if isinstance(paper,   bytes): paper   = paper.decode()
    if isinstance(latency, bytes): latency = latency.decode()
    return {
        "id":               name,
        "strategy_type":    name,
        "enabled":          enabled == "1",
        "paper_mode":       paper == "1",
        "latency_profile":  latency or "standard",
        "params":           {},
        "risk_overrides":   {},
        "updated_at":       datetime.now(timezone.utc).isoformat(),
        "updated_by":       "system",
    }


@router.get("/configs")
async def list_configs(request: Request) -> JSONResponse:
    """
    Return configuration for all strategies.

    Reads from Redis strategy:state:{name} hashes — the live authoritative source.
    Includes all 6 strategies regardless of whether they have been explicitly configured.
    """
    redis = request.app.state.redis
    configs = []
    for name in _ALL_STRATEGIES:
        state = await redis.hgetall(f"strategy:state:{name}")
        configs.append(_state_to_config(name, state))
    return JSONResponse(content={"strategies": configs})


class ConfigUpdate(BaseModel):
    enabled:         bool | None = None
    latency_profile: str | None = None
    paper_mode:      bool | None = None


@router.patch("/configs/{strategy_type}")
async def update_config(
    strategy_type: str, body: ConfigUpdate, request: Request
) -> JSONResponse:
    """
    Update a strategy's runtime configuration in Redis.

    Changes take effect immediately — no service restart required.
    The strategy runner checks Redis state on every iteration.
    """
    if strategy_type not in _ALL_STRATEGIES:
        return JSONResponse(
            {"error": f"Unknown strategy: {strategy_type}"},
            status_code=404,
        )

    redis = request.app.state.redis
    key   = f"strategy:state:{strategy_type}"

    updates: dict[str, str] = {}
    if body.enabled is not None:
        updates["enabled"] = "1" if body.enabled else "0"
    if body.latency_profile is not None:
        updates["latency_profile"] = body.latency_profile
    if body.paper_mode is not None:
        updates["paper_mode"] = "1" if body.paper_mode else "0"

    if updates:
        await redis.hset(key, mapping=updates)

    state = await redis.hgetall(key)
    return JSONResponse(content=_state_to_config(strategy_type, state))
