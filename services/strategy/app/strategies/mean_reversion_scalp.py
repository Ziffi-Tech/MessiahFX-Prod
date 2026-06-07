"""
Mean Reversion Scalp Strategy — RSI + Bollinger Band confluence.

Concept:
  In ranging markets, price oscillates between statistical extremes.
  When RSI is oversold AND price touches the lower Bollinger Band,
  the probability of a short-term bounce is elevated (and vice versa).
  This confluence of two independent signals improves accuracy.

Signal conditions (long):
  RSI(period) < oversold_threshold  (typically 30)
  AND current_price <= lower_band    (price at or below lower BB)

Signal conditions (short):
  RSI(period) > overbought_threshold (typically 70)
  AND current_price >= upper_band    (price at or above upper BB)

Bollinger Bands:
  middle = SMA(prices, bb_period)
  upper  = middle + bb_std_mult × σ
  lower  = middle - bb_std_mult × σ

Edge rationale:
  - RSI alone generates too many false signals in trending markets
  - Bollinger Band touch alone fires on trending breakouts (bad for MR)
  - Confluence of both = only fires when price is statistically extended
    AND momentum is exhausted — strong mean-reversion probability

Works best in: ranging, low_volatility regimes
Avoid in: trending_bull, trending_bear (mean reversion fights the trend)

Parameters:
  MR_RSI_PERIOD         : RSI lookback (default 14)
  MR_RSI_OVERSOLD       : oversold threshold (default 30)
  MR_RSI_OVERBOUGHT     : overbought threshold (default 70)
  MR_BB_PERIOD          : Bollinger Band lookback (default 20)
  MR_BB_STD_MULT        : Bollinger Band std multiplier (default 2.0)
  MR_MIN_EDGE_BPS       : minimum net edge to emit signal (default 3.0)
  MR_FEE_BPS            : round-trip fee estimate (default 8.0)
"""

from datetime import datetime, timezone
from typing import Optional, Tuple

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

STRATEGY_NAME = "mean_reversion_scalp"
MR_MIN_TICKS = 35  # Need enough ticks for both RSI and BB calculations


def _rsi(prices: np.ndarray, period: int) -> Optional[float]:
    """Compute RSI for the most recent price point."""
    if len(prices) < period + 2:
        return None
    deltas = np.diff(prices)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = float(np.mean(gains[-period:]))
    avg_loss = float(np.mean(losses[-period:]))

    if avg_loss < 1e-12:
        return 100.0  # No losses = maximum overbought
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _bollinger_bands(
    prices: np.ndarray, period: int, std_mult: float
) -> Optional[Tuple[float, float, float]]:
    """
    Compute Bollinger Bands.
    Returns (upper, middle, lower) for the most recent bar.
    """
    if len(prices) < period:
        return None
    window = prices[-period:]
    middle = float(np.mean(window))
    std = float(np.std(window))
    return middle + std_mult * std, middle, middle - std_mult * std


def _analyse(
    ticks: list[dict],
    rsi_period: int,
    rsi_oversold: float,
    rsi_overbought: float,
    bb_period: int,
    bb_std_mult: float,
) -> Optional[dict]:
    """
    Run RSI + BB confluence analysis on tick data.

    Returns signal dict or None.
    """
    n = min(len(ticks), max(rsi_period, bb_period) + 20)
    if n < MR_MIN_TICKS:
        return None

    try:
        # Prices: oldest first (ticks are most-recent-first, so reverse)
        prices = np.array([float(t["mid"]) for t in ticks[:n]], dtype=np.float64)[::-1]
    except (KeyError, ValueError, TypeError):
        return None

    current = float(prices[-1])

    rsi_val = _rsi(prices, rsi_period)
    bb = _bollinger_bands(prices, bb_period, bb_std_mult)

    if rsi_val is None or bb is None:
        return None

    upper, middle, lower = bb

    # ── Long signal: oversold + price at lower band ───────────────────────────
    if rsi_val < rsi_oversold and current <= lower:
        # Expected revert to middle band
        target_bps = ((middle - current) / current) * 10_000
        return {
            "direction": "buy",
            "rsi": round(rsi_val, 2),
            "upper_band": round(upper, 8),
            "middle_band": round(middle, 8),
            "lower_band": round(lower, 8),
            "current_price": current,
            "expected_revert_bps": round(target_bps, 4),
            "confluence": "rsi_oversold+lower_band",
        }

    # ── Short signal: overbought + price at upper band ────────────────────────
    if rsi_val > rsi_overbought and current >= upper:
        target_bps = ((current - middle) / current) * 10_000
        return {
            "direction": "sell",
            "rsi": round(rsi_val, 2),
            "upper_band": round(upper, 8),
            "middle_band": round(middle, 8),
            "lower_band": round(lower, 8),
            "current_price": current,
            "expected_revert_bps": round(target_bps, 4),
            "confluence": "rsi_overbought+upper_band",
        }

    return None


def _analyse_bars(
    bars: list[dict],
    rsi_period: int,
    rsi_oversold: float,
    rsi_overbought: float,
    bb_period: int,
    bb_std_mult: float,
) -> Optional[dict]:
    """
    Bar-based RSI + Bollinger confluence via pandas-ta on OHLCV closes.

    Same return shape as _analyse so run_once is unchanged. Returns None when
    pandas-ta is unavailable, there are too few bars, or indicators are invalid.
    """
    need = max(rsi_period, bb_period) + 2
    if not _HAS_PANDAS_TA or len(bars) < need:
        return None

    cols = ohlcv_columns(bars)
    df = pd.DataFrame({"close": [float(x) for x in cols["close"]]})

    rsi_series = ta.rsi(df["close"], length=rsi_period)
    bbands = ta.bbands(df["close"], length=bb_period, std=bb_std_mult)
    if rsi_series is None or bbands is None or bbands.empty:
        return None

    rsi_clean = rsi_series.dropna()
    if rsi_clean.empty:
        return None
    rsi_val = float(rsi_clean.iloc[-1])

    def _band(prefix: str) -> Optional[float]:
        matches = [c for c in bbands.columns if c.startswith(prefix)]
        if not matches:
            return None
        val = bbands[matches[0]].iloc[-1]
        return float(val) if val == val else None  # skip NaN

    upper, middle, lower = _band("BBU"), _band("BBM"), _band("BBL")
    if None in (upper, middle, lower) or not np.isfinite(rsi_val):
        return None

    current = float(df["close"].iloc[-1])

    # ── Long: oversold + price at/below lower band ────────────────────────────
    if rsi_val < rsi_oversold and current <= lower:
        target_bps = ((middle - current) / current) * 10_000
        return {
            "direction": "buy",
            "rsi": round(rsi_val, 2),
            "upper_band": round(upper, 8),
            "middle_band": round(middle, 8),
            "lower_band": round(lower, 8),
            "current_price": current,
            "expected_revert_bps": round(target_bps, 4),
            "confluence": "rsi_oversold+lower_band",
        }

    # ── Short: overbought + price at/above upper band ─────────────────────────
    if rsi_val > rsi_overbought and current >= upper:
        target_bps = ((current - middle) / current) * 10_000
        return {
            "direction": "sell",
            "rsi": round(rsi_val, 2),
            "upper_band": round(upper, 8),
            "middle_band": round(middle, 8),
            "lower_band": round(lower, 8),
            "current_price": current,
            "expected_revert_bps": round(target_bps, 4),
            "confluence": "rsi_overbought+upper_band",
        }

    return None


class MeanReversionScalpStrategy:
    """RSI + Bollinger Band confluence mean reversion for ranging markets."""

    # Best regimes for this strategy
    VALID_REGIMES = {"ranging", "low_volatility", "unknown"}

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def run_once(self, redis: Redis, state: dict) -> None:
        """Scan all configured symbols for MR confluence signals."""
        regime = await redis.get("ai:regime:current") or b"unknown"
        regime = regime.decode() if isinstance(regime, bytes) else regime

        # In strong trending markets, mean reversion fights momentum — skip
        if regime in ("trending_bull", "trending_bear", "high_volatility"):
            log.debug("mean_reversion.regime_skip", regime=regime)
            return

        if await is_halted(redis):
            return

        paper_mode = self._settings.TRADING_MODE != "live"
        now = datetime.now(timezone.utc)
        latency = state.get("latency_profile", "standard")
        fee_bps = self._settings.MR_FEE_BPS
        min_edge = self._settings.MR_MIN_EDGE_BPS

        use_bars = self._settings.MR_USE_BARS and _HAS_PANDAS_TA
        bar_seconds = self._settings.MR_BAR_SECONDS

        for symbol in self._settings.mean_reversion_symbol_list:
            venue = "binance" if "USDT" in symbol else "oanda"

            if use_bars:
                bars = await read_ohlcv(redis, venue, symbol, bar_seconds, max_ticks=500)
                signal = _analyse_bars(
                    bars,
                    rsi_period=self._settings.MR_RSI_PERIOD,
                    rsi_oversold=self._settings.MR_RSI_OVERSOLD,
                    rsi_overbought=self._settings.MR_RSI_OVERBOUGHT,
                    bb_period=self._settings.MR_BB_PERIOD,
                    bb_std_mult=self._settings.MR_BB_STD_MULT,
                )
            else:
                ticks = await read_tick_cache(redis, venue, symbol, 80)
                signal = _analyse(
                    ticks,
                    rsi_period=self._settings.MR_RSI_PERIOD,
                    rsi_oversold=self._settings.MR_RSI_OVERSOLD,
                    rsi_overbought=self._settings.MR_RSI_OVERBOUGHT,
                    bb_period=self._settings.MR_BB_PERIOD,
                    bb_std_mult=self._settings.MR_BB_STD_MULT,
                )

            if signal is None:
                continue

            net_edge = round(signal["expected_revert_bps"] - fee_bps, 4)
            if net_edge < min_edge:
                log.debug(
                    "mean_reversion.edge_too_small",
                    symbol=symbol,
                    net_edge=net_edge,
                    rsi=signal["rsi"],
                )
                continue

            # R:R: target = middle band, stop = 0.5 × band width
            band_width = signal["upper_band"] - signal["lower_band"]
            current = signal["current_price"]
            stop_bps = (band_width * 0.5 / current) * 10_000
            rr_ratio = round(net_edge / stop_bps, 2) if stop_bps > 0 else None

            # R:R gate
            min_rr = self._settings.STRATEGY_MIN_RR_RATIO
            if min_rr > 0 and (rr_ratio is None or rr_ratio < min_rr):
                log.debug(
                    "mean_reversion.rr_below_minimum",
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
                expected_return_bps=signal["expected_revert_bps"],
                fee_cost_bps=fee_bps,
                net_edge_bps=net_edge,
                paper_mode=paper_mode,
                raw_signal={
                    **signal,
                    "rr_ratio": rr_ratio,
                    "stop_bps": round(stop_bps, 4),
                    "regime": regime,
                },
            )
            await publish_opportunity(redis, opp)

            log.info(
                "mean_reversion.signal.published",
                symbol=symbol,
                direction=signal["direction"],
                rsi=signal["rsi"],
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
        """TradingView confirmation of a mean reversion setup."""
        if action not in ("buy", "sell"):
            return False

        ticks = await read_tick_cache(redis, venue, symbol, 80)
        if not ticks:
            return False

        tick = await read_latest_tick(redis, venue, symbol)
        current = float(tick.get("mid", tv_price or 0)) if tick else (tv_price or 0)
        if current <= 0:
            return False

        paper_mode = self._settings.TRADING_MODE != "live"
        now = datetime.now(timezone.utc)
        fee_bps = self._settings.MR_FEE_BPS

        opp = OpportunityCreate(
            strategy_type=STRATEGY_NAME,
            venue=venue,
            source="tradingview",
            symbol_primary=symbol,
            symbol_secondary=None,
            detected_at=now,
            latency_profile=state.get("latency_profile", "standard"),
            spread=None,
            expected_return_bps=self._settings.MR_MIN_EDGE_BPS + fee_bps,
            fee_cost_bps=fee_bps,
            net_edge_bps=self._settings.MR_MIN_EDGE_BPS,
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
