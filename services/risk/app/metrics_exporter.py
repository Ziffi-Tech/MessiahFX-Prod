"""
Risk-state metrics exporter.

Publishes Prometheus gauges from the live risk state (drawdown, halt, consecutive
losses, open positions) so Prometheus can alert on them, and pushes a warning
notification when the daily drawdown reaches 80% of the limit.
"""

import asyncio
import json
from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis

from mezna_shared.redis_client import RedisKeys
from mezna_shared.metrics_gauges import make_gauge, drawdown_breaching
from .config import settings as _settings

log = structlog.get_logger()

DRAWDOWN = make_gauge("mezna_daily_drawdown_pct", "Daily drawdown (percent)")
HALTED = make_gauge("mezna_trading_halted", "1 if the kill switch is active, else 0")
CONSEC_LOSSES = make_gauge("mezna_consecutive_losses", "Consecutive losing trades")
OPEN_POSITIONS = make_gauge("mezna_open_positions", "Open position count")


async def _notify(redis: Redis, event: str, **kwargs) -> None:
    try:
        await redis.rpush(RedisKeys.NOTIFICATION_QUEUE, json.dumps({
            "event": event, "timestamp": datetime.now(timezone.utc).isoformat(), **kwargs,
        }))
    except Exception:
        pass


async def run(settings, redis: Redis, interval: int = 15) -> None:
    """Poll risk:state → gauges + drawdown warning. Runs until cancelled."""
    # Config limit is a fraction (0.03); risk:state stores percent (3.0) — align.
    max_dd_pct = getattr(settings, "RISK_MAX_DAILY_DRAWDOWN_PCT", _settings.RISK_MAX_DAILY_DRAWDOWN_PCT) * 100.0
    alerted = False
    log.info("risk.metrics_exporter.started", max_drawdown_pct=max_dd_pct, interval=interval)

    while True:
        try:
            state = await redis.hgetall(RedisKeys.RISK_STATE)
            halt = await redis.get(RedisKeys.HALT)

            def _num(key: str) -> float:
                try:
                    return float(state.get(key) or 0)
                except (TypeError, ValueError):
                    return 0.0

            dd = _num("daily_drawdown_pct")
            DRAWDOWN.set(dd)
            HALTED.set(1 if halt == "1" else 0)
            CONSEC_LOSSES.set(_num("consecutive_losses"))
            OPEN_POSITIONS.set(_num("open_position_count"))

            breaching = drawdown_breaching(dd, max_dd_pct)
            if breaching and not alerted:
                log.warning("risk.drawdown_warning", drawdown_pct=round(dd, 4), max_pct=max_dd_pct)
                await _notify(redis, "risk.drawdown_warning", drawdown_pct=round(dd, 4), max_pct=max_dd_pct)
                alerted = True
            elif not breaching and alerted:
                alerted = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("risk.metrics_exporter.error", error=str(exc))
        await asyncio.sleep(interval)
