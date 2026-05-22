"""
Statistical Arbitrage Strategy — Spot vs Perpetual Z-Score.

Concept:
  The spot and perpetual price of the same asset are cointegrated — they track
  each other closely but can diverge temporarily due to funding mechanics or
  order flow imbalances. The spread (spot_mid − perp_mid) is mean-reverting.

  A z-score measures how many standard deviations the current spread has
  deviated from its recent mean. A high |z-score| signals an imminent reversion.

Signal conditions:
  |z_score| > STAT_ARB_ENTRY_Z
  AND net_edge_bps > STAT_ARB_MIN_EDGE_BPS
  AND at least STAT_ARB_MIN_TICKS ticks in each cache

Trade direction:
  z > +ENTRY_Z → spot is overpriced vs perp → short spot, long perp
  z < -ENTRY_Z → perp is overpriced vs spot → long spot, short perp

In OpportunityCreate:
  symbol_primary   = the overpriced leg (will be sold)
  symbol_secondary = the underpriced leg (will be bought)

Data used:
  - Tick caches from Redis (market-data ring buffers, max STAT_ARB_WINDOW ticks)
  - Most recent tick for mid-price reference (expected profit calculation)

Phase 2 limitations:
  - Rolling z-score only. No Engle-Granger cointegration test (deferred to Phase 3).
  - Tick alignment is by cache position, not timestamp. Ticks from the two
    symbols arrive at different times, so the spread is slightly asynchronous.
    This is acceptable for the standard/relaxed latency profile.
"""

from datetime import datetime, timezone

import numpy as np
import structlog
from redis.asyncio import Redis

from ..config import Settings
from ..publisher import publish_opportunity
from .base import read_tick_cache, read_latest_tick
from mezna_shared.schemas.opportunity import OpportunityCreate

log = structlog.get_logger()

STRATEGY_NAME = "stat_arb"
STAT_ARB_MIN_TICKS = 30  # Require at least this many ticks before computing z-score


def _spot_to_perp(spot: str) -> str:
    """'BTC/USDT'  →  'BTC/USDT:USDT'"""
    base, quote = spot.split("/")
    return f"{base}/{quote}:{quote}"


def _rolling_z_score(
    spot_ticks: list[dict],
    perp_ticks: list[dict],
) -> tuple[float, float, float] | None:
    """
    Compute the z-score of the current spread vs its rolling distribution.

    Args:
        spot_ticks: Most-recent-first list of tick dicts with a "mid" key.
        perp_ticks: Same, for the perpetual.

    Returns:
        (z_score, current_spread, spread_std) or None if data is insufficient
        or degenerate (std ≈ 0).
    """
    n = min(len(spot_ticks), len(perp_ticks))
    if n < STAT_ARB_MIN_TICKS:
        return None

    try:
        spot_mids = np.array([float(t["mid"]) for t in spot_ticks[:n]], dtype=np.float64)
        perp_mids = np.array([float(t["mid"]) for t in perp_ticks[:n]], dtype=np.float64)
    except (KeyError, ValueError, TypeError):
        return None

    spreads = spot_mids - perp_mids   # index 0 is the most recent
    mean = float(np.mean(spreads))
    std = float(np.std(spreads))

    if std < 1e-10:
        return None  # No variance — not a useful signal

    current_spread = float(spreads[0])
    z_score = (current_spread - mean) / std
    return z_score, current_spread, std


class StatArbStrategy:
    """Statistical arbitrage via spot/perp z-score divergence detection."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def run_once(self, redis: Redis, state: dict) -> None:
        """
        One iteration: check z-score for all configured symbol pairs.
        Publishes a signal for each pair that exceeds the entry threshold.
        """
        paper_mode = self._settings.TRADING_MODE != "live"
        latency = state.get("latency_profile", "standard")
        now = datetime.now(timezone.utc)
        window = self._settings.STAT_ARB_WINDOW

        for spot_symbol in self._settings.stat_arb_symbol_list:
            perp_symbol = _spot_to_perp(spot_symbol)

            # ── Read tick history ──────────────────────────────────────────────
            spot_ticks = await read_tick_cache(redis, "binance", spot_symbol, window)
            perp_ticks = await read_tick_cache(redis, "binance", perp_symbol, window)

            result = _rolling_z_score(spot_ticks, perp_ticks)
            if result is None:
                log.debug(
                    "stat_arb.insufficient_data",
                    spot=spot_symbol,
                    spot_n=len(spot_ticks),
                    perp_n=len(perp_ticks),
                    required=STAT_ARB_MIN_TICKS,
                )
                continue

            z_score, current_spread, spread_std = result

            log.debug(
                "stat_arb.z_score",
                symbol=spot_symbol,
                z_score=round(z_score, 4),
                spread=round(current_spread, 8),
                spread_std=round(spread_std, 8),
            )

            if abs(z_score) < self._settings.STAT_ARB_ENTRY_Z:
                continue

            # ── Estimate expected profit ───────────────────────────────────────
            # Profit if spread reverts fully to its mean (expressed in bps).
            spot_tick = await read_latest_tick(redis, "binance", spot_symbol)
            if not spot_tick:
                continue

            spot_mid = float(spot_tick.get("mid", 0))
            if spot_mid <= 0:
                continue

            # |current_spread| / spot_mid = fractional reversion potential
            reversion_bps = round(abs(current_spread) / spot_mid * 10_000, 4)
            fee_bps = self._settings.STAT_ARB_FEE_BPS
            net_edge_bps = round(reversion_bps - fee_bps, 4)

            if net_edge_bps < self._settings.STAT_ARB_MIN_EDGE_BPS:
                log.debug(
                    "stat_arb.edge_too_small",
                    symbol=spot_symbol,
                    reversion_bps=reversion_bps,
                    fee_bps=fee_bps,
                    net_edge_bps=net_edge_bps,
                )
                continue

            # ── Determine trade direction ──────────────────────────────────────
            # symbol_primary = the overpriced leg (will be SOLD)
            # symbol_secondary = the underpriced leg (will be BOUGHT)
            if z_score > 0:
                # Spot overpriced → sell spot, buy perp
                symbol_primary = spot_symbol
                symbol_secondary = perp_symbol
            else:
                # Perp overpriced → sell perp, buy spot
                symbol_primary = perp_symbol
                symbol_secondary = spot_symbol

            # ── Publish signal ─────────────────────────────────────────────────
            opp = OpportunityCreate(
                strategy_type=STRATEGY_NAME,
                venue="binance",
                source="internal",
                symbol_primary=symbol_primary,
                symbol_secondary=symbol_secondary,
                detected_at=now,
                latency_profile=latency,
                spread=round(current_spread, 8),
                z_score=round(z_score, 4),
                expected_return_bps=reversion_bps,
                fee_cost_bps=fee_bps,
                net_edge_bps=net_edge_bps,
                paper_mode=paper_mode,
                raw_signal={
                    "z_score": z_score,
                    "current_spread": current_spread,
                    "spread_std": spread_std,
                    "window_used": min(len(spot_ticks), len(perp_ticks)),
                    "spot_mid": spot_mid,
                    "direction": "sell_spot_buy_perp" if z_score > 0 else "buy_spot_sell_perp",
                },
            )
            await publish_opportunity(redis, opp)

    async def run_from_signal(
        self,
        redis: Redis,
        symbol: str,           # Spot symbol, internal format: "BTC/USDT"
        action: str,           # "buy" | "sell" | "alert"
        state: dict,
        tv_price: float | None = None,
    ) -> bool:
        """
        Process a TradingView signal for this spot/perp pair.

        Computes the live z-score for the given symbol and publishes
        an opportunity only if the divergence is statistically significant.

        Returns True if an opportunity was published, False otherwise.
        """
        perp_symbol = _spot_to_perp(symbol)
        paper_mode = self._settings.TRADING_MODE != "live"
        latency = state.get("latency_profile", "standard")
        now = datetime.now(timezone.utc)
        window = self._settings.STAT_ARB_WINDOW

        # ── Read tick history ──────────────────────────────────────────────
        spot_ticks = await read_tick_cache(redis, "binance", symbol, window)
        perp_ticks = await read_tick_cache(redis, "binance", perp_symbol, window)

        result = _rolling_z_score(spot_ticks, perp_ticks)
        if result is None:
            log.warning(
                "stat_arb.signal.insufficient_data",
                symbol=symbol,
                spot_n=len(spot_ticks),
                perp_n=len(perp_ticks),
                required=STAT_ARB_MIN_TICKS,
                hint="Not enough tick history yet — market-data feed may still be warming up",
            )
            return False

        z_score, current_spread, spread_std = result

        if abs(z_score) < self._settings.STAT_ARB_ENTRY_Z:
            log.info(
                "stat_arb.signal.z_score_below_threshold",
                symbol=symbol,
                z_score=round(z_score, 4),
                threshold=self._settings.STAT_ARB_ENTRY_Z,
                reason="Spread has not diverged enough to trade — holding off",
            )
            return False

        # ── Edge calculation ───────────────────────────────────────────────
        spot_tick = await read_latest_tick(redis, "binance", symbol)
        if not spot_tick:
            return False

        spot_mid = float(spot_tick.get("mid", 0))
        if spot_mid <= 0:
            return False

        reversion_bps = round(abs(current_spread) / spot_mid * 10_000, 4)
        fee_bps = self._settings.STAT_ARB_FEE_BPS
        net_edge_bps = round(reversion_bps - fee_bps, 4)

        if net_edge_bps < self._settings.STAT_ARB_MIN_EDGE_BPS:
            log.info(
                "stat_arb.signal.edge_insufficient",
                symbol=symbol,
                z_score=round(z_score, 4),
                net_edge_bps=net_edge_bps,
                min_required=self._settings.STAT_ARB_MIN_EDGE_BPS,
            )
            return False

        # ── Direction — override with TV action if explicit ────────────────
        if action == "buy":
            # TV says buy → treat spot as underpriced (buy spot, sell perp)
            symbol_primary = perp_symbol
            symbol_secondary = symbol
        elif action == "sell":
            # TV says sell → treat spot as overpriced (sell spot, buy perp)
            symbol_primary = symbol
            symbol_secondary = perp_symbol
        else:
            # "alert" or unspecified — use z-score direction
            if z_score > 0:
                symbol_primary = symbol
                symbol_secondary = perp_symbol
            else:
                symbol_primary = perp_symbol
                symbol_secondary = symbol

        # ── Publish opportunity ────────────────────────────────────────────
        opp = OpportunityCreate(
            strategy_type=STRATEGY_NAME,
            venue="binance",
            source="tradingview",
            symbol_primary=symbol_primary,
            symbol_secondary=symbol_secondary,
            detected_at=now,
            latency_profile=latency,
            spread=round(current_spread, 8),
            z_score=round(z_score, 4),
            expected_return_bps=reversion_bps,
            fee_cost_bps=fee_bps,
            net_edge_bps=net_edge_bps,
            paper_mode=paper_mode,
            raw_signal={
                "trigger": "tradingview",
                "tv_action": action,
                "tv_price": tv_price,
                "z_score": z_score,
                "current_spread": current_spread,
                "spread_std": spread_std,
                "window_used": min(len(spot_ticks), len(perp_ticks)),
                "spot_mid": spot_mid,
            },
        )
        await publish_opportunity(redis, opp)
        log.info(
            "stat_arb.signal.published",
            symbol=symbol,
            z_score=round(z_score, 4),
            net_edge_bps=net_edge_bps,
            source="tradingview",
        )
        return True
