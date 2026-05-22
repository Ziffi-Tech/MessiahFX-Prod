"""
AI News Sentiment Cache — background task.

Fetches crypto and FX headlines every 5 minutes, scores them with
Claude Haiku, and caches the result in Redis.

Keys:
  ai:sentiment:crypto   — overall crypto market sentiment score (-100 to +100)
  ai:sentiment:fx       — overall FX market sentiment score (-100 to +100)

The scorer reads these keys inline; lookup cost is < 1 ms.
TTL is 8 minutes (slightly longer than the fetch interval) so the key
never expires between update cycles.

Data sources (all free, no auth required):
  - CoinDesk RSS:      https://www.coindesk.com/arc/outboundfeeds/rss/
  - CoinTelegraph RSS: https://cointelegraph.com/rss
  - ForexLive RSS:     https://www.forexlive.com/feed/news/
"""

import asyncio
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import anthropic
import httpx
import structlog
from redis.asyncio import Redis

from .config import Settings

log = structlog.get_logger()

# Redis key TTL: 8 min — slightly > fetch interval so keys never expire mid-cycle
_SENTIMENT_TTL_SECONDS = 480

# Feed definitions: (url, asset_class)
_FEEDS = [
    ("https://www.coindesk.com/arc/outboundfeeds/rss/", "crypto"),
    ("https://cointelegraph.com/rss", "crypto"),
    ("https://www.forexlive.com/feed/news/", "fx"),
]

# Headlines per feed sent to Claude (keep context short for Haiku)
_MAX_HEADLINES_PER_FEED = 8

# ── Claude tool — forces structured output ────────────────────────────────────

_SENTIMENT_TOOL = {
    "name": "score_news_sentiment",
    "description": (
        "Score the overall market sentiment from the provided headlines "
        "and assess the impact on active trading conditions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "integer",
                "minimum": -100,
                "maximum": 100,
                "description": (
                    "Sentiment score: "
                    "+100 strongly bullish, +50 mild bullish, 0 neutral, "
                    "-50 mild bearish, -100 strongly bearish."
                ),
            },
            "urgency": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": (
                    "low = routine news; "
                    "medium = monitor conditions; "
                    "high = significant market-moving event."
                ),
            },
            "key_themes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 major themes from the headlines.",
                "maxItems": 4,
            },
            "summary": {
                "type": "string",
                "description": "One sentence summary of the news environment (max 25 words).",
            },
        },
        "required": ["score", "urgency", "key_themes", "summary"],
    },
}

_SENTIMENT_SYSTEM = """\
You are a market news analyst for MeznaQuantFX. Given a set of recent headlines,
assess the overall sentiment and market impact for active trading strategies.

Focus on:
- Regulatory news (SEC, CFTC, major bans)    → strongly bearish, high urgency
- Macro events (Fed decisions, CPI, geopolitical) → high urgency
- Exchange outages, hacks, major liquidations → bearish, high urgency
- ETF approvals, institutional adoption       → bullish
- Routine price updates                       → low urgency, neutral

Your output feeds directly into live signal scoring. Be conservative — prefer
neutral over extreme scores unless headlines clearly justify the direction.
"""


# ── RSS parsing ───────────────────────────────────────────────────────────────

def _parse_rss_headlines(xml_text: str, max_items: int) -> list[str]:
    """
    Extract titles from an RSS 2.0 or Atom feed.
    Returns up to max_items titles. Never raises.
    """
    if max_items <= 0:
        return []
    headlines: list[str] = []
    try:
        root = ET.fromstring(xml_text)

        # RSS 2.0: <channel><item><title>
        for item in root.iter("item"):
            title = item.find("title")
            if title is not None and title.text:
                headlines.append(title.text.strip())
                if len(headlines) >= max_items:
                    return headlines

        # Atom fallback: <feed><entry><title>
        if not headlines:
            for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                title = entry.find("{http://www.w3.org/2005/Atom}title")
                if title is not None and title.text:
                    headlines.append(title.text.strip())
                    if len(headlines) >= max_items:
                        return headlines

    except ET.ParseError as exc:
        log.warning("news_sentinel.rss_parse_error", error=str(exc)[:80])
    return headlines


async def _fetch_headlines(
    http: httpx.AsyncClient, url: str, max_items: int
) -> list[str]:
    """Fetch one RSS feed and return headline strings. Never raises."""
    try:
        resp = await http.get(url, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        return _parse_rss_headlines(resp.text, max_items)
    except Exception as exc:
        log.warning("news_sentinel.feed_fetch_error", url=url, error=str(exc)[:80])
        return []


# ── Claude scoring ────────────────────────────────────────────────────────────

async def _score_headlines(
    client: anthropic.AsyncAnthropic,
    model: str,
    asset_class: str,
    headlines: list[str],
) -> Optional[dict]:
    """
    Score a list of headlines with Claude Haiku.
    Uses a 15-second timeout — this runs in the background, not the hot path.
    Returns the tool input dict, or None on error/timeout.
    """
    if not headlines:
        return None

    prompt = (
        f"Asset class: {asset_class.upper()}\n\n"
        f"Recent headlines ({len(headlines)} items):\n"
        + "\n".join(f"- {h}" for h in headlines)
    )

    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=model,
                max_tokens=300,
                temperature=0.0,
                # Prompt caching on system: 5-min TTL, saves tokens at every cycle
                system=[
                    {
                        "type": "text",
                        "text": _SENTIMENT_SYSTEM,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[_SENTIMENT_TOOL],
                tool_choice={"type": "tool", "name": "score_news_sentiment"},
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=15.0,
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "score_news_sentiment":
                return block.input

    except asyncio.TimeoutError:
        log.warning("news_sentinel.score_timeout", asset_class=asset_class)
    except anthropic.APIError as exc:
        log.error("news_sentinel.api_error", asset_class=asset_class, error=str(exc)[:100])
    except Exception as exc:
        log.error("news_sentinel.score_error", asset_class=asset_class, error=str(exc)[:100])
    return None


# ── Health state written to Redis so the health endpoint can expose it ────────
# Key: ai:sentinel:health
# TTL: 3× the fetch interval so it outlasts a missed cycle
_HEALTH_KEY = "ai:sentinel:health"
_HEALTH_TTL_MULTIPLIER = 3


# ── One fetch+score cycle ─────────────────────────────────────────────────────

async def _run_cycle(
    http: httpx.AsyncClient,
    redis: Redis,
    client: anthropic.AsyncAnthropic,
    settings: Settings,
) -> dict[str, bool]:
    """
    Fetch all feeds, score per asset class, cache results in Redis.
    Returns {asset_class: success_bool} for health tracking.
    """
    headlines_by_class: dict[str, list[str]] = {"crypto": [], "fx": []}
    feed_failures: list[str] = []

    for url, asset_class in _FEEDS:
        items = await _fetch_headlines(http, url, _MAX_HEADLINES_PER_FEED)
        headlines_by_class[asset_class].extend(items)
        if not items:
            feed_failures.append(url)
        log.debug("news_sentinel.fetched", url=url, count=len(items))

    if feed_failures:
        log.warning(
            "news_sentinel.feed_failures",
            failed_feeds=feed_failures,
            count=len(feed_failures),
            total_feeds=len(_FEEDS),
        )

    cycle_results: dict[str, bool] = {}
    now = datetime.now(timezone.utc)

    for asset_class, headlines in headlines_by_class.items():
        result = await _score_headlines(
            client, settings.AI_SCORING_MODEL, asset_class, headlines
        )
        if result is not None:
            cache_payload = {
                **result,
                "headline_count": len(headlines),
                "updated_at": now.isoformat(),
                "asset_class": asset_class,
            }
            await redis.set(
                f"ai:sentiment:{asset_class}",
                json.dumps(cache_payload),
                ex=_SENTIMENT_TTL_SECONDS,
            )
            # Track last-success timestamp separately — survives beyond sentiment TTL
            await redis.set(
                f"ai:sentinel:last_ok:{asset_class}",
                now.isoformat(),
                ex=settings.NEWS_FETCH_INTERVAL_SECONDS * _HEALTH_TTL_MULTIPLIER,
            )
            cycle_results[asset_class] = True
            log.info(
                "news_sentinel.cached",
                asset_class=asset_class,
                score=result.get("score"),
                urgency=result.get("urgency"),
                headlines=len(headlines),
            )
        else:
            cycle_results[asset_class] = False
            log.warning(
                "news_sentinel.score_failed",
                asset_class=asset_class,
                headlines=len(headlines),
                hint="Check ANTHROPIC_API_KEY and network — sentiment will be stale",
            )

    # Write overall health payload
    health_payload = {
        "last_cycle_at": now.isoformat(),
        "feed_failures": len(feed_failures),
        "total_feeds": len(_FEEDS),
        "results": cycle_results,
        "all_ok": all(cycle_results.values()),
    }
    await redis.set(
        _HEALTH_KEY,
        json.dumps(health_payload),
        ex=settings.NEWS_FETCH_INTERVAL_SECONDS * _HEALTH_TTL_MULTIPLIER,
    )

    if not any(cycle_results.values()):
        log.error(
            "news_sentinel.total_failure",
            message="All asset classes failed to score — sentiment cache will expire",
            feed_failures=len(feed_failures),
        )

    return cycle_results


# ── Main entry point (launched as background task) ────────────────────────────

async def run_news_sentinel(settings: Settings, redis: Redis) -> None:
    """
    Background task: fetch + score news every NEWS_FETCH_INTERVAL_SECONDS.
    Runs until cancelled (service shutdown). Never raises.
    """
    if not settings.NEWS_FETCH_ENABLED:
        log.info("news_sentinel.disabled", reason="NEWS_FETCH_ENABLED=false")
        return

    if not settings.ai_configured:
        log.warning(
            "news_sentinel.no_api_key",
            reason="ANTHROPIC_API_KEY not set — news sentinel idle",
        )
        return

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    interval = settings.NEWS_FETCH_INTERVAL_SECONDS

    log.info("news_sentinel.started", interval_seconds=interval, feeds=len(_FEEDS))

    async with httpx.AsyncClient(
        headers={"User-Agent": "MeznaQuantFX-NewsSentinel/1.0"},
        follow_redirects=True,
    ) as http:
        # Run one cycle immediately on start, then sleep between subsequent cycles
        while True:
            try:
                await _run_cycle(http, redis, client, settings)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("news_sentinel.cycle_error", error=str(exc))

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    await client.close()
    log.info("news_sentinel.stopped")
