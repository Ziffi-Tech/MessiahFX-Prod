"""
Alpha / Edge Decay Monitor.

Detects gradual win-rate deterioration per strategy — complementing the rotation
engine which only reacts to *consecutive* losses.

A strategy can have alternating wins and losses and never trigger the rotation
threshold, yet its underlying win rate can silently drop from 60% → 40%,
signalling the edge is eroding (regime shift, parameter drift, crowded trade).

Detection model:
  - Rolling deque of outcomes per strategy (window = EDGE_MONITOR_WINDOW)
  - Decay alert when: rolling_win_rate < baseline_win_rate − decay_threshold
  - Recovery alert when: rolling_win_rate recovers above baseline

Redis keys:
  edge:outcomes:{strategy}   — capped list of outcome bits (1=win, 0=loss)
  edge:win_rate:{strategy}   — current rolling win rate as float string
  edge:decayed:{strategy}    — set with 4h TTL when decay is active

Both the executor and the strategy runner write outcomes via direct Redis ops
(cross-service boundary — no Python import required).
"""

import asyncio
import json
from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis

log = structlog.get_logger()

_OUTCOMES_KEY = "edge:outcomes:{name}"
_WIN_RATE_KEY  = "edge:win_rate:{name}"
_DECAYED_KEY   = "edge:decayed:{name}"

_MIN_SAMPLE    = 10    # Minimum trades before triggering alerts
_DECAY_TTL     = 14400  # 4 h TTL on decayed flag


async def record_outcome(
    redis: Redis,
    strategy: str,
    won: bool,
    window: int = 20,
    baseline_win_rate: float = 0.55,
    decay_threshold: float = 0.15,
) -> dict:
    """
    Record one trade outcome and check for edge decay or recovery.

    Args:
        redis: async Redis connection
        strategy: strategy name string
        won: True if the trade / execution was successful
        window: rolling window size (number of recent trades)
        baseline_win_rate: expected healthy win rate (default 55%)
        decay_threshold: alert when win_rate < baseline − threshold (default 15pp)

    Returns status dict suitable for logging.
    """
    key = _OUTCOMES_KEY.format(name=strategy)

    await redis.rpush(key, "1" if won else "0")
    await redis.ltrim(key, -window, -1)
    await redis.expire(key, 86400)

    raw = await redis.lrange(key, 0, -1)
    if not raw:
        return {"strategy": strategy, "win_rate": None, "window_size": 0, "decayed": False}

    outcomes    = [int(o) for o in raw]
    window_size = len(outcomes)
    win_rate    = sum(outcomes) / window_size

    await redis.set(_WIN_RATE_KEY.format(name=strategy), str(round(win_rate, 4)), ex=86400)

    already_decayed = bool(await redis.exists(_DECAYED_KEY.format(name=strategy)))
    decayed = already_decayed

    if window_size >= _MIN_SAMPLE:
        below_threshold = win_rate < (baseline_win_rate - decay_threshold)

        if below_threshold and not already_decayed:
            decayed = True
            await redis.set(_DECAYED_KEY.format(name=strategy), "1", ex=_DECAY_TTL)
            await _notify(redis, {
                "event": "edge.decay_detected",
                "strategy": strategy,
                "win_rate": round(win_rate, 4),
                "baseline": baseline_win_rate,
                "window_size": window_size,
                "message": (
                    f"Edge decay on {strategy}: {window_size}-trade win rate "
                    f"{win_rate*100:.1f}% (baseline {baseline_win_rate*100:.0f}%). "
                    "Review parameters or regime fit."
                ),
            })
            log.warning(
                "edge_monitor.decay_detected",
                strategy=strategy,
                win_rate=round(win_rate, 4),
                baseline=baseline_win_rate,
                window_size=window_size,
            )

        elif not below_threshold and already_decayed:
            decayed = False
            await redis.delete(_DECAYED_KEY.format(name=strategy))
            await _notify(redis, {
                "event": "edge.recovery",
                "strategy": strategy,
                "win_rate": round(win_rate, 4),
                "window_size": window_size,
                "message": f"{strategy} edge recovered: win rate {win_rate*100:.1f}%.",
            })
            log.info(
                "edge_monitor.recovery",
                strategy=strategy,
                win_rate=round(win_rate, 4),
                window_size=window_size,
            )

    log.debug(
        "edge_monitor.outcome",
        strategy=strategy,
        won=won,
        win_rate=round(win_rate, 4),
        window_size=window_size,
    )

    return {
        "strategy": strategy,
        "win_rate": round(win_rate, 4),
        "window_size": window_size,
        "decayed": decayed,
    }


async def get_status(redis: Redis, strategy: str) -> dict:
    """Return current edge status for one strategy."""
    win_rate_raw = await redis.get(_WIN_RATE_KEY.format(name=strategy))
    decayed      = bool(await redis.exists(_DECAYED_KEY.format(name=strategy)))
    raw          = await redis.lrange(_OUTCOMES_KEY.format(name=strategy), 0, -1)

    return {
        "strategy":        strategy,
        "win_rate":        float(win_rate_raw) if win_rate_raw else None,
        "window_size":     len(raw),
        "decayed":         decayed,
        "recent_outcomes": [int(o) for o in raw[-10:]],
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }


async def get_all_status(redis: Redis, strategies: list[str]) -> dict:
    """Return edge status for all strategies — for dashboard / health endpoints."""
    return {
        "strategies": {s: await get_status(redis, s) for s in strategies},
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }


async def _notify(redis: Redis, payload: dict) -> None:
    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    try:
        await redis.rpush("notifications:queue", json.dumps(payload))
    except Exception:
        pass
