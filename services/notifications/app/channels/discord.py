"""
Discord webhook sender — sends rich embeds to a configured webhook URL.

Uses Discord's embed format for colour-coded, structured alerts.
Colour coding:
  Green  (0x00C851) — filled orders
  Red    (0xFF4444) — halts, errors, rejections
  Orange (0xFF8800) — warnings (cooldowns, rejected orders)
  Blue   (0x33B5E5) — informational
"""

import time

import httpx
import structlog

log = structlog.get_logger()

_LAST_SENT: float = 0.0

COLOR_GREEN  = 0x00C851
COLOR_RED    = 0xFF4444
COLOR_ORANGE = 0xFF8800
COLOR_BLUE   = 0x33B5E5


async def send_embed(
    title: str,
    description: str,
    color: int,
    webhook_url: str,
    fields: list[dict] | None = None,
    min_interval_seconds: float = 1.0,
) -> bool:
    """
    Send a rich embed to the Discord webhook.

    Returns True on success, False on failure. Never raises.
    """
    global _LAST_SENT

    now = time.monotonic()
    elapsed = now - _LAST_SENT
    if elapsed < min_interval_seconds:
        import asyncio
        await asyncio.sleep(min_interval_seconds - elapsed)

    embed: dict = {
        "title": title,
        "description": description,
        "color": color,
    }
    if fields:
        embed["fields"] = fields

    payload = {
        "username": "MeznaQuantFX",
        "embeds": [embed],
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(webhook_url, json=payload)
        _LAST_SENT = time.monotonic()

        if resp.status_code in (200, 204):
            log.debug("discord.sent")
            return True
        else:
            log.error(
                "discord.send_failed",
                status=resp.status_code,
                body=resp.text[:200],
            )
            return False

    except Exception as exc:
        log.error("discord.error", error=str(exc))
        return False


async def send(
    text: str,
    webhook_url: str,
    color: int = COLOR_BLUE,
    min_interval_seconds: float = 1.0,
) -> bool:
    """Send a plain message as a Discord embed description."""
    return await send_embed(
        title="MeznaQuantFX Alert",
        description=text,
        color=color,
        webhook_url=webhook_url,
        min_interval_seconds=min_interval_seconds,
    )
