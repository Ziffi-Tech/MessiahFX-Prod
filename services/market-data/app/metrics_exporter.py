"""
Feed-health metrics exporter.

Publishes a Prometheus gauge `mezna_feed_up{venue}` (1 fresh / 0 dead) from the
per-feed heartbeat keys, and pushes a notification when a configured feed drops —
so Prometheus can alert and operators get pinged even without an alertmanager.
"""

import asyncio
import json
from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis

from mezna_shared.redis_client import RedisKeys
from mezna_shared.metrics_gauges import make_gauge, feed_up_value
from .config import Settings

log = structlog.get_logger()

FEED_UP = make_gauge("mezna_feed_up", "1 if the venue feed heartbeat is fresh, else 0", ("venue",))


def _configured_venues(settings: Settings) -> list[str]:
    venues: list[str] = []
    if settings.binance_spot_list or settings.binance_perp_list:
        venues.append("binance")
    if settings.bybit_perp_list:
        venues.append("bybit")
    if settings.okx_perp_list:
        venues.append("okx")
    if settings.kraken_symbol_list:
        venues.append("kraken")
    if settings.OANDA_API_KEY and settings.OANDA_ACCOUNT_ID and settings.oanda_instrument_list:
        venues.append("oanda")
    return venues


async def _notify(redis: Redis, event: str, **kwargs) -> None:
    try:
        await redis.rpush(RedisKeys.NOTIFICATION_QUEUE, json.dumps({
            "event": event, "timestamp": datetime.now(timezone.utc).isoformat(), **kwargs,
        }))
    except Exception:
        pass


async def run(settings: Settings, redis: Redis, interval: int = 15) -> None:
    """Poll heartbeats → gauge + down-transition alerts. Runs until cancelled."""
    venues = _configured_venues(settings)
    if not venues:
        log.info("metrics_exporter.no_configured_venues")
        return

    last_up: dict[str, int] = {}
    log.info("metrics_exporter.started", venues=venues, interval=interval)
    while True:
        try:
            for venue in venues:
                hb = await redis.get(RedisKeys.feed_heartbeat(venue))
                up = feed_up_value(hb)
                FEED_UP.labels(venue=venue).set(up)
                if up == 0 and last_up.get(venue, 1) == 1:
                    log.warning("metrics_exporter.feed_down", venue=venue)
                    await _notify(redis, "feed.down", venue=venue)
                last_up[venue] = up
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("metrics_exporter.error", error=str(exc))
        await asyncio.sleep(interval)
