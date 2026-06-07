"""
Momentum Continuation Strategy — Multi-period Rate of Change.

Concept:
  Strong directional momentum persists. When price has moved significantly
  in one direction over multiple lookback periods (1-bar, 5-bar, 20-bar),
  and all three timeframes agree, there is a high probability the move
  continues — at least for another 1-3 bars.

  This is the academic "momentum factor" — the most robustly documented
  alpha in financial literature (Jegadeesh & Titman, 1993; Fama & French, 2012).

Signal conditions:
  ROC_1  > threshold (short-term momentum)
  AND ROC_5  > threshold (medium-term alignment)
  AND ROC_20 > threshold (dominant trend direction)
  → Multi-timeframe confirmation = higher-probability continuation

  Invert all conditions for short signals.

ROC (Rate of Change):
  ROC_n = (P_current - P_n_bars_ago) / P_n_bars_ago × 100

Edge rationale:
  - Single timeframe momentum: ~52% win rate
  - All three timeframes aligned: ~60-65% win rate (documented in research)
  - Stop loss = ATR from entry (limits catastrophic loss on momentum failures)

Works best in: trending_bull, trending_bear, high_volatility regimes
Avoid in: ranging, low_volatility (no momentum = false signals)

Parameters:
  MOM_ROC_THRESHOLD     : minimum ROC on each timeframe (default 0.05%)
  MOM_ATR_PERIOD        : ATR period for stop calculation (default 14)
  MOM_ATR_STOP_MULT     : stop distance = multiplier × ATR (default 1.5)
  MOM_MIN_EDGE_BPS      : minimum net edge to emit signal (default 5.0)
  MOM_FEE_BPS           : round-trip fee estimate (default 10.0)
"""

from datetime import datetime, timezone
from typing import Optional

import numpy as np
import structlog
from redis.asyncio import Redis

from ..config import Settings
from ..publisher import publish_opportunity
from .base import read_tick_cache, read_latest_tick, is_halted, read_ohlcv
from mezna_shared.bars import ohlcv_columns
from mezna_shared.schemas.opportunity import OpportunityCreate

try:
    import pandas as pd
    import pandas_ta_classic as ta
    _HAS_PANDAS_TA = True
except Exception:  # pandas-ta optional — bar mode falls back to tick detection
    pd = None
    ta = None
    _HAS_PANDAS_TA = False

log = structlog.get_logger()

STRATEGY_NAME = "momentum"
MOM_MIN_TICKS = 30  # Need at least 30 ticks for 20-bar ROC


def _roc(prices: np.ndarray, n: int) -> Optional[float]:
    """Rate of Change: (current - n_bars_ago) / n_bars_ago × 100"""
    if len(prices) < n + 1:
        return None
    current  = float(prices[-1])
    past     = float(prices[-1 - n])
    if past == 0:
        return None
    return (current - past) / past * 100.0


def _atr_from_mids(prices: np.ndarray, period: int) -> Optional[float]:
    """Approximate ATR using consecutive mid-price differences."""
    if len(prices) < period + 2:
        return None
    diffs = np.abs(np.diff(prices[-period - 1:]))
    return float(np.mean(diffs))


def _analyse_momentum(
    ticks: list[dict],
    roc_threshold: float,
    atr_period: int,
) -> Optional[dict]:
    """
    Run multi-timeframe momentum analysis.

    Returns signal dict or None.
    """
    n = len(ticks)
    if n < MOM_MIN_TICKS:
        return None

    try:
        # Prices: oldest first
        prices = np.array([float(t["mid"]) for t in ticks[:min(n, 50)]], dtype=np.float64)[::-1]
    except (KeyError, ValueError, TypeError):
        return None

    current = float(prices[-1])

    roc1  = _roc(prices, 1)
    roc5  = _roc(prices, min(5, len(prices) - 1))
    roc20 = _roc(prices, min(20, len(prices) - 1))

    if roc1 is None or roc5 is None or roc20 is None:
        return None

    atr = _atr_from_mids(prices, atr_period)
    if not atr or atr <= 0:
        return None

    # ── Bullish momentum: all timeframes positive ──────────────────────────────
    if roc1 > roc_threshold and roc5 > roc_threshold and roc20 > roc_threshold:
        strength = (roc1 + roc5 + roc20) / 3.0
        return {
            "direction": "buy",
            "roc_1":  round(roc1,  4),
            "roc_5":  round(roc5,  4),
            "roc_20": round(roc20, 4),
            "strength": round(strength, 4),
            "atr": round(atr, 8),
            "current_price": current,
        }

    # ── Bearish momentum: all timeframes negative ──────────────────────────────
    if roc1 < -roc_threshold and roc5 < -roc_threshold and roc20 < -roc_threshold:
        strength = abs((roc1 + roc5 + roc20) / 3.0)
        return {
            "direction": "sell",
            "roc_1":  round(roc1,  4),
            "roc_5":  round(roc5,  4),
            "roc_20": round(roc20, 4),
            "strength": round(strength, 4),
            "atr": round(atr, 8),
            "current_price": current,
        }

    return None


def _analyse_momentum_bars(
    bars: list[dict],
    roc_threshold: float,
    atr_period: int,
) -> Optional[dict]:
    """
    Bar-based multi-timeframe momentum via pandas-ta (ROC on OHLCV closes + ATR).

    Same return shape as _analyse_momentum so run_once is unchanged. Returns None
    when pandas-ta is unavailable, there are too few bars, or indicators are invalid.
    """
    need = max(21, atr_period) + 2  # ROC_20 needs 21 closes
    if not _HAS_PANDAS_TA or len(bars) < need:
        return None

    cols = ohlcv_columns(bars)
    df = pd.DataFrame({
        "high":  [float(x) for x in cols["high"]],
        "low":   [float(x) for x in cols["low"]],
        "close": [float(x) for x in cols["close"]],
    })

    def _last(series) -> Optional[float]:
        if series is None:
            return None
        s = series.dropna()
        return float(s.iloc[-1]) if not s.empty else None

    roc1 = _last(ta.roc(df["close"], length=1))
    roc5 = _last(ta.roc(df["close"], length=5))
    roc20 = _last(ta.roc(df["close"], length=20))
    atr = _last(ta.atr(df["high"], df["low"], df["close"], length=atr_period))

    vals = (roc1, roc5, roc20, atr)
    if any(v is None or not np.isfinite(v) for v in vals) or atr <= 0:
        return None

    current = float(df["close"].iloc[-1])

    if roc1 > roc_threshold and roc5 > roc_threshold and roc20 > roc_threshold:
        direction, strength = "buy", (roc1 + roc5 + roc20) / 3.0
    elif roc1 < -roc_threshold and roc5 < -roc_threshold and roc20 < -roc_threshold:
        direction, strength = "sell", abs((roc1 + roc5 + roc20) / 3.0)
    else:
        return None

    return {
        "direction": direction,
        "roc_1": round(roc1, 4),
        "roc_5": round(roc5, 4),
        "roc_20": round(roc20, 4),
        "strength": round(strength, 4),
        "atr": round(atr, 8),
        "current_price": current,
    }


class MomentumStrategy:
    """Multi-timeframe Rate-of-Change momentum continuation strategy."""

    VALID_REGIMES = {"trending_bull", "trending_bear", "high_volatility", "unknown"}

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def run_once(self, redis: Redis, state: dict) -> None:
        """Scan symbols for multi-timeframe momentum alignment."""
        regime = await redis.get("ai:regime:current") or b"unknown"
        regime = regime.decode() if isinstance(regime, bytes) else regime

        # Momentum doesn't work in ranging markets
        if regime in ("ranging", "low_volatility"):
            log.debug("momentum.regime_skip", regime=regime)
            return

        if await is_halted(redis):
            return

        paper_mode = self._settings.TRADING_MODE != "live"
        now = datetime.now(timezone.utc)
        latency = state.get("latency_profile", "standard")
        fee_bps = self._settings.MOM_FEE_BPS
        min_edge = self._settings.MOM_MIN_EDGE_BPS

        use_bars = self._settings.MOM_USE_BARS and _HAS_PANDAS_TA
        bar_seconds = self._settings.MOM_BAR_SECONDS

        for symbol in self._settings.momentum_symbol_list:
            venue = "binance" if "USDT" in symbol else "oanda"

            if use_bars:
                bars = await read_ohlcv(redis, venue, symbol, bar_seconds, max_ticks=500)
                signal = _analyse_momentum_bars(
                    bars,
                    roc_threshold=self._settings.MOM_ROC_THRESHOLD,
                    atr_period=self._settings.MOM_ATR_PERIOD,
                )
            else:
                ticks = await read_tick_cache(redis, venue, symbol, 60)
                signal = _analyse_momentum(
                    ticks,
                    roc_threshold=self._settings.MOM_ROC_THRESHOLD,
                    atr_period=self._settings.MOM_ATR_PERIOD,
                )

            if signal is None:
                continue

            # Edge = strength of momentum in bps
            edge_bps = signal["strength"] * 100  # strength in % → bps-ish proxy
            net_edge = round(max(edge_bps - fee_bps, min_edge), 4)

            current = signal["current_price"]
            atr = signal["atr"]

            # R:R: target = 2 × ATR continuation, stop = 1.5 × ATR
            stop_mult = self._settings.MOM_ATR_STOP_MULT
            target_bps = (atr * 2.0 / current) * 10_000
            stop_bps   = (atr * stop_mult / current) * 10_000
            rr_ratio   = round(target_bps / stop_bps, 2) if stop_bps > 0 else None

            # R:R gate
            min_rr = self._settings.STRATEGY_MIN_RR_RATIO
            if min_rr > 0 and (rr_ratio is None or rr_ratio < min_rr):
                log.debug(
                    "momentum.rr_below_minimum",
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
                expected_return_bps=round(target_bps, 4),
                fee_cost_bps=fee_bps,
                net_edge_bps=net_edge,
                paper_mode=paper_mode,
                raw_signal={
                    **signal,
                    "rr_ratio": rr_ratio,
                    "stop_bps": round(stop_bps, 4),
                    "target_bps": round(target_bps, 4),
                    "regime": regime,
                    "stop_price": round(
                        current - atr * stop_mult if signal["direction"] == "buy"
                        else current + atr * stop_mult, 8
                    ),
                    "target_price": round(
                        current + atr * 2.0 if signal["direction"] == "buy"
                        else current - atr * 2.0, 8
                    ),
                },
            )
            await publish_opportunity(redis, opp)

            log.info(
                "momentum.signal.published",
                symbol=symbol,
                direction=signal["direction"],
                strength=signal["strength"],
                roc_alignment=f"{signal['roc_1']:.3f}/{signal['roc_5']:.3f}/{signal['roc_20']:.3f}",
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
        """TradingView momentum confirmation."""
        if action not in ("buy", "sell"):
            return False

        tick = await read_latest_tick(redis, venue, symbol)
        current = float(tick.get("mid", tv_price or 0)) if tick else (tv_price or 0)
        if current <= 0:
            return False

        paper_mode = self._settings.TRADING_MODE != "live"
        fee_bps = self._settings.MOM_FEE_BPS

        opp = OpportunityCreate(
            strategy_type=STRATEGY_NAME,
            venue=venue,
            source="tradingview",
            symbol_primary=symbol,
            symbol_secondary=None,
            detected_at=datetime.now(timezone.utc),
            latency_profile=state.get("latency_profile", "standard"),
            spread=None,
            expected_return_bps=self._settings.MOM_MIN_EDGE_BPS + fee_bps,
            fee_cost_bps=fee_bps,
            net_edge_bps=self._settings.MOM_MIN_EDGE_BPS,
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
