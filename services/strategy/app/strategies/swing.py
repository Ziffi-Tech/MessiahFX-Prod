"""
Swing / Medium-Frequency Strategy — TradingView signal-native.

Design:
  Swing trading is fully TV-signal-driven. The Pine Script strategy on
  TradingView runs the technical analysis (EMA, ATR, RSI, etc.) and fires
  a webhook when it sees an entry condition. This service trusts that
  signal and converts it directly into an OpportunityCreate.

  Unlike funding_arb and stat_arb, swing does NOT independently scan
  market data. TradingView IS the signal source.

Signal semantics:
  action="buy"   → enter a long position in symbol
  action="sell"  → enter a short position in symbol
  action="close" → signal to close an existing position (logged, not traded)
  action="alert" → informational only (logged, not traded)

Venue routing:
  venue="binance" → crypto spot/perp (symbol format: BTC/USDT)
  venue="oanda"   → forex/CFD (symbol format: EUR_USD)
"""

from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis

from ..config import Settings
from ..publisher import publish_opportunity
from .base import read_latest_tick
from mezna_shared.schemas.opportunity import OpportunityCreate

log = structlog.get_logger()

STRATEGY_NAME = "swing"


class SwingStrategy:
    """
    Swing strategy — converts TradingView webhooks into OpportunityCreate signals.

    run_once() is a no-op (swing is TV-native, not scan-based).
    run_from_signal() is the real entry point.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def run_once(self, redis: Redis, state: dict) -> None:
        """
        No-op for autonomous scanning — swing waits for TradingView signals.
        The runner still calls this but it does nothing.
        """
        pass

    async def run_from_signal(
        self,
        redis: Redis,
        symbol: str,           # Internal format: "BTC/USDT" or "EUR_USD"
        action: str,           # "buy" | "sell" | "close" | "alert"
        venue: str,            # "binance" | "oanda"
        state: dict,
        tv_price: float | None = None,
        note: str | None = None,
    ) -> bool:
        """
        Convert a TradingView signal into a swing trade opportunity.

        For buy/sell: creates an OpportunityCreate and publishes it.
        For close/alert: logs only — no opportunity emitted.

        Returns True if an opportunity was published.
        """
        if action in ("close", "alert"):
            log.info(
                "swing.signal.informational",
                symbol=symbol,
                action=action,
                note=note,
                reason="close/alert signals are logged but do not generate trade opportunities",
            )
            return False

        if action not in ("buy", "sell"):
            log.warning(
                "swing.signal.unknown_action",
                symbol=symbol,
                action=action,
            )
            return False

        paper_mode = self._settings.TRADING_MODE != "live"
        latency = state.get("latency_profile", "standard")
        now = datetime.now(timezone.utc)

        # ── Fetch live reference price from Redis ──────────────────────────
        live_tick = await read_latest_tick(redis, venue, symbol)
        if live_tick:
            ref_price = float(live_tick.get("mid", tv_price or 0))
        else:
            ref_price = tv_price or 0
            if ref_price == 0:
                log.warning(
                    "swing.signal.no_price",
                    symbol=symbol,
                    venue=venue,
                    hint="No live tick and no TV price — opportunity published without price reference",
                )

        # ── Publish opportunity ────────────────────────────────────────────
        # symbol_primary = the asset being traded directionally (no secondary for swing)
        # net_edge_bps = configured minimum for swing (TV has done the TA)
        opp = OpportunityCreate(
            strategy_type=STRATEGY_NAME,
            venue=venue,
            source="tradingview",
            symbol_primary=symbol,
            symbol_secondary=None,
            detected_at=now,
            latency_profile=latency,
            spread=float(live_tick.get("spread_bps", 0)) if live_tick else None,
            expected_return_bps=self._settings.SWING_MIN_EDGE_BPS,
            fee_cost_bps=0.0,
            net_edge_bps=self._settings.SWING_MIN_EDGE_BPS,
            paper_mode=paper_mode,
            raw_signal={
                "trigger": "tradingview",
                "tv_action": action,
                "tv_price": tv_price,
                "live_ref_price": ref_price,
                "note": note,
                "direction": "long" if action == "buy" else "short",
            },
        )
        await publish_opportunity(redis, opp)
        log.info(
            "swing.signal.published",
            symbol=symbol,
            venue=venue,
            action=action,
            ref_price=ref_price,
            paper_mode=paper_mode,
            source="tradingview",
        )
        return True
