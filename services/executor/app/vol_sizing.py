"""
Vol-aware sizing — a relative volatility multiplier for per-leg notional.

Reads recent persisted bars for the leg's symbol and returns a multiplier
(long-run vol / recent vol, clamped): smaller during a vol spike, larger when
calm. Best-effort — any error or thin history returns 1.0 (no scaling), so it can
never break order sizing. Opt-in via VOL_TARGET_ENABLED.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import structlog

from mezna_shared.volatility import returns_from_prices, relative_sizing_multiplier

log = structlog.get_logger()

_INTERVAL_MINUTES = {"15s": 0.25, "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


async def multiplier(db_engine, venue: str, symbol: str, settings) -> float:
    """Relative vol-sizing multiplier for (venue, symbol). 1.0 on any failure."""
    try:
        from mezna_shared.ohlcv import read_bars

        interval = settings.VOL_TARGET_INTERVAL
        lookback = settings.VOL_TARGET_LOOKBACK_BARS
        minutes = _INTERVAL_MINUTES.get(interval, 1)
        # Query a generous window (×3) so we reliably get `lookback` bars.
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes * lookback * 3)

        candles = await read_bars(
            db_engine, venue, symbol, interval,
            int(start.timestamp() * 1000), int(end.timestamp() * 1000),
        )
        prices = [c["close"] for c in candles[-lookback:] if c.get("close")]
        returns = returns_from_prices(prices)
        return relative_sizing_multiplier(
            returns, lam=settings.VOL_TARGET_LAM, lo=settings.VOL_TARGET_MIN, hi=settings.VOL_TARGET_MAX,
        )
    except Exception as exc:
        log.warning("vol_sizing.failed", venue=venue, symbol=symbol, error=str(exc))
        return 1.0
