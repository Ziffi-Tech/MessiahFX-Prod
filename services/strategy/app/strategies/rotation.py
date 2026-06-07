"""
Strategy Rotation Engine — fail-safe switching when a strategy degrades.

When a strategy accumulates N consecutive losses it enters cooldown (handled
by the risk engine). The rotation engine goes further:

  1. Detects per-strategy consecutive loss count from Redis
  2. When count >= ROTATION_THRESHOLD → marks strategy "degraded"
  3. Selects the BEST alternative strategy for the current market regime
  4. Emits a rotation event to Redis (dashboard + notifications can consume)
  5. Auto-resets when the underlying strategy exits cooldown and wins again

Regime → best strategies mapping:
  trending_bull   → momentum, breakout, swing
  trending_bear   → breakout, momentum, stat_arb
  ranging         → mean_reversion_scalp, stat_arb, funding_arb
  high_volatility → funding_arb, stat_arb, mean_reversion_scalp
  low_volatility  → mean_reversion_scalp, funding_arb
  unknown         → funding_arb, stat_arb (most regime-neutral strategies first)

The rotation does NOT disable strategies — it sets a Redis key that the
strategy runner reads as a "prefer this strategy" hint. Hard enables/disables
remain the operator's responsibility via the dashboard.
"""

from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis

from mezna_shared.regime_map import (
    ALL_STRATEGIES,
    REGIME_STRATEGY_MAP,
    preferred_for_regime,
)

log = structlog.get_logger()

# Per-strategy loss counter Redis key
_STRATEGY_LOSSES_KEY = "strategy:consecutive_losses:{name}"
# Degraded flag key (TTL = cooldown duration)
_STRATEGY_DEGRADED_KEY = "strategy:degraded:{name}"
# Current preferred strategy hint
_ROTATION_PREFERRED_KEY = "strategy:rotation:preferred"

# REGIME_STRATEGY_MAP and ALL_STRATEGIES are imported from mezna_shared.regime_map
# (single source of truth shared with the executor) — see import above.

# Trigger rotation when this many consecutive losses accumulate per-strategy
ROTATION_THRESHOLD = 4


async def record_strategy_loss(redis: Redis, strategy_name: str) -> int:
    """
    Increment the per-strategy consecutive loss counter.
    Returns the NEW count.
    Resets automatically after a win via record_strategy_win().
    """
    key = _STRATEGY_LOSSES_KEY.format(name=strategy_name)
    count = await redis.incr(key)
    await redis.expire(key, 86400)  # 24h TTL — resets at end of trading day

    log.info(
        "rotation.loss_recorded",
        strategy=strategy_name,
        consecutive_losses=count,
        threshold=ROTATION_THRESHOLD,
    )

    if count >= ROTATION_THRESHOLD:
        await _handle_rotation_trigger(redis, strategy_name, count)

    return count


async def record_strategy_win(redis: Redis, strategy_name: str) -> None:
    """Reset the consecutive loss counter after a winning trade."""
    key = _STRATEGY_LOSSES_KEY.format(name=strategy_name)
    degraded_key = _STRATEGY_DEGRADED_KEY.format(name=strategy_name)

    await redis.delete(key)
    await redis.delete(degraded_key)

    log.info("rotation.win_reset", strategy=strategy_name)


async def get_strategy_loss_count(redis: Redis, strategy_name: str) -> int:
    """Read current consecutive loss count for a strategy."""
    key = _STRATEGY_LOSSES_KEY.format(name=strategy_name)
    val = await redis.get(key)
    return int(val) if val else 0


async def is_strategy_degraded(redis: Redis, strategy_name: str) -> bool:
    """True if strategy has been flagged as degraded."""
    return bool(await redis.exists(_STRATEGY_DEGRADED_KEY.format(name=strategy_name)))


async def get_preferred_strategy(redis: Redis) -> str | None:
    """
    Returns the currently rotation-preferred strategy, or None if no rotation active.
    The strategy runner uses this as a hint to prefer one strategy over others.
    """
    val = await redis.get(_ROTATION_PREFERRED_KEY)
    return val.decode() if val else None


async def get_rotation_status(redis: Redis) -> dict:
    """
    Return full rotation status for all strategies.
    Used by dashboard and health checks.
    """
    regime_raw = await redis.get("ai:regime:current") or b"unknown"
    regime = regime_raw.decode() if isinstance(regime_raw, bytes) else regime_raw
    preferred = await get_preferred_strategy(redis)

    strategies = {}
    for name in ALL_STRATEGIES:
        losses = await get_strategy_loss_count(redis, name)
        degraded = await is_strategy_degraded(redis, name)
        strategies[name] = {
            "consecutive_losses": losses,
            "degraded": degraded,
            "threshold": ROTATION_THRESHOLD,
        }

    return {
        "current_regime": regime,
        "preferred_strategy": preferred,
        "regime_strategy_map": REGIME_STRATEGY_MAP.get(regime, []),
        "strategies": strategies,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def _handle_rotation_trigger(
    redis: Redis, failed_strategy: str, loss_count: int
) -> None:
    """
    Called when a strategy hits the loss threshold.
    Marks it as degraded, selects the best alternative, sets preference key.
    """
    degraded_key = _STRATEGY_DEGRADED_KEY.format(name=failed_strategy)
    await redis.set(degraded_key, "1", ex=3600 * 4)  # 4h degraded state

    # ── Find best alternative ──────────────────────────────────────────────────
    regime_raw = await redis.get("ai:regime:current") or b"unknown"
    regime = regime_raw.decode() if isinstance(regime_raw, bytes) else regime_raw
    candidates = preferred_for_regime(regime)

    # Pick the first candidate that is:
    # - not the failed strategy
    # - not itself currently degraded
    # - enabled in the system
    best_alternative = None
    for candidate in candidates:
        if candidate == failed_strategy:
            continue
        if await is_strategy_degraded(redis, candidate):
            continue
        # Check if the strategy is enabled (strategy:state:{name}.enabled == "1")
        state = await redis.hgetall(f"strategy:state:{candidate}")
        if state.get(b"enabled", b"0") == b"1" or state.get("enabled", "0") == "1":
            best_alternative = candidate
            break

    if best_alternative:
        await redis.set(_ROTATION_PREFERRED_KEY, best_alternative, ex=3600 * 4)

    # ── Emit rotation event ────────────────────────────────────────────────────
    import json
    event = json.dumps({
        "event": "strategy.rotation.triggered",
        "failed_strategy": failed_strategy,
        "consecutive_losses": loss_count,
        "regime": regime,
        "rotating_to": best_alternative,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    await redis.rpush("notifications:queue", event)

    log.warning(
        "rotation.triggered",
        failed_strategy=failed_strategy,
        consecutive_losses=loss_count,
        regime=regime,
        rotating_to=best_alternative,
    )
