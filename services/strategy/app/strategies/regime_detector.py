"""
Local (deterministic) market regime detector.

Runs every REGIME_DETECTOR_INTERVAL_SECONDS and writes a regime string
to ai:regime:current in Redis.  Serves as a fast, always-available fallback
to the Claude-powered regime endpoint in ai-filter.

Detection logic — applied to BTC/USDT as the primary market proxy:

  1. ATR over ATR_PERIOD bars           → volatility level
  2. 20-bar Rate of Change              → trend direction and strength
  3. Relative volatility (ATR / price)  → vol regime classification

  Classification cascade (first match wins):
    |price_roc_20| > TREND_ROC_THRESHOLD      → trending_bull or trending_bear
    rel_vol > HIGH_VOL_THRESHOLD              → high_volatility
    rel_vol < LOW_VOL_THRESHOLD               → low_volatility
    else                                       → ranging
    insufficient data                          → unknown (does not overwrite key)

The Claude-based detector in ai-filter takes precedence: it sets the same key
with a longer TTL (15 min default).  This local detector fills the gap when
ai-filter is cold, restarting, or not yet called by the operator.
"""

import asyncio
from typing import Optional

import numpy as np
import structlog
from redis.asyncio import Redis

from .base import read_tick_cache

log = structlog.get_logger()

# Both detectors write to the same key; Claude has a longer TTL so it wins
_REGIME_KEY = "ai:regime:current"
# Local-only key for observability without overwriting Claude's result
_LOCAL_REGIME_KEY = "ai:regime:local"

_PRIMARY_VENUE = "binance"
_PRIMARY_SYMBOL = "BTC/USDT"

_ATR_PERIOD = 20
_LOOKBACK_BARS = 45         # Need 20-bar ROC + ATR buffer

# Trend: |20-bar ROC| > 0.05% signals a trending regime
_TREND_ROC_THRESHOLD = 0.05
# High vol: ATR/price > 0.15% (roughly: $90 move per $60k BTC bar)
_HIGH_VOL_THRESHOLD = 0.0015
# Low vol: ATR/price < 0.03%
_LOW_VOL_THRESHOLD = 0.0003

_LOCAL_FALLBACK_TTL = 120   # 2 min — local is short-lived, Claude is authoritative


def _classify(ticks: list[dict]) -> Optional[str]:
    """
    Classify regime from tick list (most-recent-first).
    Returns regime string or None if insufficient data.
    """
    if len(ticks) < _LOOKBACK_BARS:
        return None

    try:
        # Reverse to oldest-first for time-series math
        prices = np.array(
            [float(t["mid"]) for t in ticks[:_LOOKBACK_BARS]], dtype=np.float64
        )[::-1]
    except (KeyError, ValueError, TypeError):
        return None

    current = float(prices[-1])
    if current <= 0:
        return None

    # ATR approximation via consecutive absolute price differences
    diffs = np.abs(np.diff(prices[-(_ATR_PERIOD + 1):]))
    atr = float(np.mean(diffs)) if len(diffs) > 0 else 0.0
    rel_vol = atr / current

    # 20-bar Rate of Change (oldest to current)
    past_price = float(prices[-21])
    roc_20 = (current - past_price) / past_price * 100.0 if past_price > 0 else 0.0

    if abs(roc_20) > _TREND_ROC_THRESHOLD:
        return "trending_bull" if roc_20 > 0 else "trending_bear"
    if rel_vol > _HIGH_VOL_THRESHOLD:
        return "high_volatility"
    if rel_vol < _LOW_VOL_THRESHOLD:
        return "low_volatility"
    return "ranging"


async def detect_and_publish(redis: Redis) -> str:
    """
    Run detection and publish result.

    - Always writes ai:regime:local (observability).
    - Only writes ai:regime:current if Claude has not set it (key absent).
    Returns detected regime.
    """
    ticks = await read_tick_cache(redis, _PRIMARY_VENUE, _PRIMARY_SYMBOL, _LOOKBACK_BARS + 5)
    regime = _classify(ticks) or "unknown"

    await redis.set(_LOCAL_REGIME_KEY, regime, ex=300)

    if not await redis.exists(_REGIME_KEY):
        await redis.set(_REGIME_KEY, regime, ex=_LOCAL_FALLBACK_TTL)
        log.info(
            "regime_detector.fallback_published",
            regime=regime,
            ticks_available=len(ticks),
        )
    else:
        log.debug(
            "regime_detector.claude_key_active",
            local_detected=regime,
        )

    return regime


async def run(redis: Redis, interval_seconds: int = 60) -> None:
    """Background loop — detects and publishes regime every interval_seconds."""
    log.info("regime_detector.started", interval_seconds=interval_seconds)
    while True:
        try:
            regime = await detect_and_publish(redis)
            log.debug("regime_detector.cycle", detected=regime)
        except asyncio.CancelledError:
            log.info("regime_detector.cancelled")
            raise
        except Exception as exc:
            log.error("regime_detector.error", error=str(exc))
        await asyncio.sleep(interval_seconds)
