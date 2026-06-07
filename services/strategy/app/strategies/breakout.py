"""
Breakout Strategy — ATR-based volatility breakout detection.

Concept:
  Markets spend ~70% of time ranging, then make sharp directional moves.
  A breakout occurs when price closes above/below a consolidated range
  defined by the highest high / lowest low over the lookback window,
  filtered by ATR to distinguish genuine moves from noise.

Signal conditions:
  current_price > highest_high(lookback) + ATR_MULTIPLIER * atr    → BUY breakout
  current_price < lowest_low(lookback)  - ATR_MULTIPLIER * atr     → SELL breakout

Edge rationale:
  - ATR filter removes low-conviction gaps and thin-liquidity spikes
  - Range-breakouts capture the start of trend moves after consolidation
  - Works best in: trending_bull, trending_bear regimes

Parameters (from config):
  BREAKOUT_LOOKBACK     : bars to define the consolidation range (default 20)
  BREAKOUT_ATR_PERIOD   : bars for ATR calculation (default 14)
  BREAKOUT_ATR_MULT     : multiplier on ATR for noise filter (default 0.5)
  BREAKOUT_MIN_EDGE_BPS : minimum net edge to emit a signal (default 4.0)
  BREAKOUT_FEE_BPS      : round-trip fee estimate (default 10.0)

Regime awareness:
  Prefers trending_bull / trending_bear regimes.
  Skips signals in 'ranging' or 'low_volatility' regimes (false breakouts).
"""

from datetime import datetime, timezone
from typing import Optional

import numpy as np
import structlog
from redis.asyncio import Redis

from ..config import Settings
from ..publisher import publish_opportunity
from .base import read_tick_cache, read_latest_tick, is_halted
from mezna_shared.schemas.opportunity import OpportunityCreate

log = structlog.get_logger()

STRATEGY_NAME = "breakout"
BREAKOUT_MIN_TICKS = 25  # Require at least this many ticks before computing


def _compute_atr(ticks: list[dict], period: int) -> Optional[float]:
    """
    Estimate ATR from bid/ask ticks.
    True Range ≈ high - low for each tick interval, using spread as a proxy.
    """
    n = min(len(ticks), period + 1)
    if n < 5:
        return None

    try:
        mids = [float(t["mid"]) for t in ticks[:n]]
        # Simulate OHLC-style TR: |current - previous|
        trs = [abs(mids[i] - mids[i + 1]) for i in range(len(mids) - 1)]
        return float(np.mean(trs[-period:])) if trs else None
    except (KeyError, ValueError, TypeError):
        return None


def _detect_breakout(
    ticks: list[dict],
    lookback: int,
    atr_period: int,
    atr_mult: float,
) -> Optional[dict]:
    """
    Detect whether the latest price has broken out of its recent range.

    Returns:
        {"direction": "buy" | "sell", "range_high": float, "range_low": float,
         "atr": float, "current_price": float}
        or None if no breakout.
    """
    if len(ticks) < max(lookback, atr_period) + 5:
        return None

    try:
        current = float(ticks[0]["mid"])
        # Range = highest high / lowest low over lookback window (ticks[1:lookback+1])
        range_ticks = ticks[1: lookback + 1]
        highs = [float(t.get("ask", t["mid"])) for t in range_ticks]
        lows  = [float(t.get("bid", t["mid"])) for t in range_ticks]

        range_high = max(highs)
        range_low  = min(lows)
    except (KeyError, ValueError, IndexError, TypeError):
        return None

    atr = _compute_atr(ticks, atr_period)
    if not atr or atr <= 0:
        return None

    threshold = atr * atr_mult

    if current > range_high + threshold:
        return {
            "direction": "buy",
            "range_high": range_high,
            "range_low": range_low,
            "atr": atr,
            "current_price": current,
            "breakout_distance": current - range_high,
        }
    if current < range_low - threshold:
        return {
            "direction": "sell",
            "range_high": range_high,
            "range_low": range_low,
            "atr": atr,
            "current_price": current,
            "breakout_distance": range_low - current,
        }
    return None


class BreakoutStrategy:
    """ATR-filtered breakout strategy for trending market regimes."""

    # Regimes where breakout signals are valid
    VALID_REGIMES = {"trending_bull", "trending_bear", "unknown"}

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def run_once(self, redis: Redis, state: dict) -> None:
        """
        Scan all configured symbols for breakout conditions.
        Skips ranging and low-volatility regimes to avoid false breakouts.
        """
        # ── Regime gate ────────────────────────────────────────────────────────
        regime = await redis.get("ai:regime:current") or b"unknown"
        regime = regime.decode() if isinstance(regime, bytes) else regime
        if regime in ("ranging", "low_volatility"):
            log.debug("breakout.regime_skip", regime=regime)
            return

        if await is_halted(redis):
            return

        paper_mode = self._settings.TRADING_MODE != "live"
        latency = state.get("latency_profile", "standard")
        now = datetime.now(timezone.utc)

        lookback  = self._settings.BREAKOUT_LOOKBACK
        atr_period = self._settings.BREAKOUT_ATR_PERIOD
        atr_mult  = self._settings.BREAKOUT_ATR_MULT
        min_edge  = self._settings.BREAKOUT_MIN_EDGE_BPS
        fee_bps   = self._settings.BREAKOUT_FEE_BPS

        for symbol in self._settings.breakout_symbol_list:
            venue = "binance" if "USDT" in symbol else "oanda"
            ticks = await read_tick_cache(redis, venue, symbol, lookback + atr_period + 10)

            if len(ticks) < BREAKOUT_MIN_TICKS:
                continue

            signal = _detect_breakout(ticks, lookback, atr_period, atr_mult)
            if signal is None:
                continue

            current = signal["current_price"]
            atr = signal["atr"]

            # Edge = breakout distance in bps (momentum profit potential)
            edge_bps = (signal["breakout_distance"] / current) * 10_000
            net_edge = round(edge_bps - fee_bps, 4)

            if net_edge < min_edge:
                log.debug("breakout.edge_too_small", symbol=symbol, net_edge=net_edge)
                continue

            # Risk/reward: target 2×ATR, stop 1×ATR from breakout level
            stop_distance = atr
            target_distance = atr * 2.0
            rr_ratio = round(target_distance / stop_distance, 2) if stop_distance > 0 else None

            # R:R gate — discard low-quality setups before they reach the risk engine
            min_rr = self._settings.STRATEGY_MIN_RR_RATIO
            if min_rr > 0 and (rr_ratio is None or rr_ratio < min_rr):
                log.debug(
                    "breakout.rr_below_minimum",
                    symbol=symbol,
                    rr_ratio=rr_ratio,
                    min_rr=min_rr,
                )
                continue

            opp = OpportunityCreate(
                strategy_type=STRATEGY_NAME,
                venue=venue,
                source="internal",
                symbol_primary=symbol,
                symbol_secondary=None,
                detected_at=now,
                latency_profile=latency,
                spread=None,
                expected_return_bps=round(edge_bps, 4),
                fee_cost_bps=fee_bps,
                net_edge_bps=net_edge,
                paper_mode=paper_mode,
                raw_signal={
                    "direction": signal["direction"],
                    "range_high": signal["range_high"],
                    "range_low": signal["range_low"],
                    "current_price": current,
                    "atr": round(atr, 8),
                    "breakout_distance": round(signal["breakout_distance"], 8),
                    "rr_ratio": rr_ratio,
                    "stop_price": round(
                        current - stop_distance if signal["direction"] == "buy"
                        else current + stop_distance, 8
                    ),
                    "target_price": round(
                        current + target_distance if signal["direction"] == "buy"
                        else current - target_distance, 8
                    ),
                    "regime": regime,
                    "lookback_bars": lookback,
                },
            )
            await publish_opportunity(redis, opp)

            log.info(
                "breakout.signal.published",
                symbol=symbol,
                direction=signal["direction"],
                net_edge_bps=net_edge,
                rr_ratio=rr_ratio,
                regime=regime,
            )

    async def run_from_signal(
        self,
        redis: Redis,
        symbol: str,
        action: str,
        venue: str,
        state: dict,
        tv_price: float | None = None,
        note: str | None = None,
    ) -> bool:
        """Process a TradingView signal as a breakout confirmation."""
        if action not in ("buy", "sell"):
            return False

        ticks = await read_tick_cache(redis, venue, symbol,
                                      self._settings.BREAKOUT_LOOKBACK + 20)
        if not ticks:
            return False

        tick = await read_latest_tick(redis, venue, symbol)
        current = float(tick.get("mid", tv_price or 0)) if tick else (tv_price or 0)
        if current <= 0:
            return False

        paper_mode = self._settings.TRADING_MODE != "live"
        now = datetime.now(timezone.utc)
        fee_bps = self._settings.BREAKOUT_FEE_BPS
        net_edge = self._settings.BREAKOUT_MIN_EDGE_BPS  # TV signal = pre-qualified

        opp = OpportunityCreate(
            strategy_type=STRATEGY_NAME,
            venue=venue,
            source="tradingview",
            symbol_primary=symbol,
            symbol_secondary=None,
            detected_at=now,
            latency_profile=state.get("latency_profile", "standard"),
            spread=None,
            expected_return_bps=net_edge + fee_bps,
            fee_cost_bps=fee_bps,
            net_edge_bps=net_edge,
            paper_mode=paper_mode,
            raw_signal={
                "trigger": "tradingview",
                "tv_action": action,
                "tv_price": tv_price,
                "current_price": current,
                "note": note,
            },
        )
        await publish_opportunity(redis, opp)
        return True
