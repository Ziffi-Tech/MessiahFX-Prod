"""
Telegram Bot API sender — raw httpx, no heavy SDK dependency.

Sends messages to a configured chat_id via the Bot API sendMessage endpoint.
Uses MarkdownV2 formatting for structured, readable alerts.

Rate limit: Telegram allows ~30 messages/second to different chats,
and ~1 message/second to the same chat. We enforce MIN_INTERVAL_SECONDS
to stay well within limits.
"""

import re
import time

import httpx
import structlog

log = structlog.get_logger()

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_LAST_SENT: float = 0.0


def _escape_md(text: str) -> str:
    """Escape special MarkdownV2 characters."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(special)}])", r"\\\1", str(text))


async def send(
    text: str,
    bot_token: str,
    chat_id: str,
    min_interval_seconds: float = 1.0,
) -> bool:
    """
    Send a plain-text message to the configured Telegram chat.

    Returns True on success, False on failure. Never raises.
    Enforces min_interval_seconds between sends to avoid rate limits.
    """
    global _LAST_SENT

    now = time.monotonic()
    elapsed = now - _LAST_SENT
    if elapsed < min_interval_seconds:
        import asyncio
        await asyncio.sleep(min_interval_seconds - elapsed)

    url = _API_BASE.format(token=bot_token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, json=payload)
        _LAST_SENT = time.monotonic()

        if resp.status_code == 200:
            log.debug("telegram.sent", chat_id=chat_id)
            return True
        else:
            log.error(
                "telegram.send_failed",
                status=resp.status_code,
                body=resp.text[:200],
            )
            return False

    except Exception as exc:
        log.error("telegram.error", error=str(exc))
        return False
