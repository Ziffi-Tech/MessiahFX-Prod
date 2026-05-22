"""
Notification queue consumer — reads from notifications:queue and dispatches.

Queue pattern:
  Producers (executor, risk) → RPUSH notifications:queue <json>
  This consumer  → BLPOP notifications:queue <timeout>

BLPOP blocks up to BLPOP_TIMEOUT_SECONDS, returns immediately when a
message arrives, or None on timeout (then loops). This gives sub-second
alert delivery under normal conditions.

Dispatch rules:
  - Telegram: if TELEGRAM_ENABLED and bot_token + chat_id are set
  - Discord:  if DISCORD_ENABLED and webhook_url is set
  - Both channels receive every alert (fan-out)
  - Failed dispatch is logged but does NOT block the consumer loop
  - Messages are consumed (popped) even if all channels fail

Rate limiting:
  Each channel enforces MIN_INTERVAL_SECONDS between sends.
  Alerts that arrive faster than the rate limit are queued and sent
  in order — they are NOT dropped.

CRITICAL: Notification failures must NEVER affect order execution.
  The executor and risk engine use fire-and-forget RPUSH. The
  notifications queue is best-effort — if the notifications service
  is down, alerts are buffered in Redis up to NOTIFICATION_QUEUE_MAX_LEN.
"""

import asyncio
import json

import structlog
from redis.asyncio import Redis

from mezna_shared.redis_client import RedisKeys
from .channels import NotificationPayload
from .channels import telegram as tg_channel
from .channels import discord as dc_channel
from . import formatter
from .config import Settings

log = structlog.get_logger()

_BLPOP_TIMEOUT = 5  # seconds; consumer wakes at least this often


async def _dispatch(
    payload: NotificationPayload,
    settings: Settings,
) -> None:
    """Send to all configured channels. Errors are logged, not raised."""
    text = formatter.format_text(payload)
    color = formatter.discord_color(payload)

    tasks = []

    if settings.TELEGRAM_ENABLED and settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        tasks.append(
            tg_channel.send(
                text=text,
                bot_token=settings.TELEGRAM_BOT_TOKEN,
                chat_id=settings.TELEGRAM_CHAT_ID,
                min_interval_seconds=settings.ALERT_MIN_INTERVAL_SECONDS,
            )
        )

    if settings.DISCORD_ENABLED and settings.DISCORD_WEBHOOK_URL:
        tasks.append(
            dc_channel.send_embed(
                title=_event_title(payload.event),
                description=text,
                color=color,
                webhook_url=settings.DISCORD_WEBHOOK_URL,
                min_interval_seconds=settings.ALERT_MIN_INTERVAL_SECONDS,
            )
        )

    if not tasks:
        # No channels configured — log only
        log.info("notifications.no_channels", event=payload.event, text=text[:120])
        return

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            log.error("notifications.dispatch_error", error=str(result))


def _event_title(event: str) -> str:
    return {
        "trade.fill":    "Trade Alert",
        "risk.halt":     "🛑 TRADING HALTED",
        "risk.cooldown": "Strategy Cooldown",
        "risk.rejected": "Signal Rejected",
    }.get(event, "System Alert")


async def run(settings: Settings, redis: Redis) -> None:
    """
    Main consumer loop — runs for the lifetime of the service.

    Pops one message at a time from notifications:queue, parses it,
    dispatches to all configured channels, then loops.
    """
    log.info(
        "notifications.consumer_started",
        telegram=settings.TELEGRAM_ENABLED,
        discord=settings.DISCORD_ENABLED,
        queue=RedisKeys.NOTIFICATION_QUEUE,
    )

    processed = 0
    errors = 0

    while True:
        try:
            # BLPOP returns (key, value) or None on timeout
            result = await redis.blpop(
                RedisKeys.NOTIFICATION_QUEUE,
                timeout=_BLPOP_TIMEOUT,
            )
        except asyncio.CancelledError:
            log.info(
                "notifications.consumer_stopped",
                processed=processed,
                errors=errors,
            )
            break
        except Exception as exc:
            log.error("notifications.blpop_error", error=str(exc))
            await asyncio.sleep(1.0)
            continue

        if result is None:
            continue  # Timeout — loop and block again

        _key, raw = result

        try:
            data = json.loads(raw)
            payload = NotificationPayload.from_dict(data)
        except (json.JSONDecodeError, TypeError) as exc:
            log.error("notifications.parse_error", error=str(exc), raw=str(raw)[:200])
            errors += 1
            continue

        try:
            await _dispatch(payload, settings)
            processed += 1
            log.debug(
                "notifications.dispatched",
                event=payload.event,
                total=processed,
            )
        except Exception as exc:
            log.error("notifications.dispatch_failed", error=str(exc))
            errors += 1
