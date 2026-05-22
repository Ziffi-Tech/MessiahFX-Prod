"""
Oanda v20 market data feed — HTTP persistent streaming.

Uses the Oanda v20 Pricing Stream endpoint, which delivers a persistent
NDJSON (newline-delimited JSON) response containing live bid/ask quotes
for all configured instruments over a single TCP connection.

Endpoint:
  GET {stream_url}/v3/accounts/{account_id}/pricing/stream
       ?instruments=EUR_USD,GBP_USD,USD_JPY

Message types (each line of the response):
  {"type": "PRICE", "instrument": "EUR_USD", "bids": [...], "asks": [...], ...}
  {"type": "HEARTBEAT", "time": "..."}  — keep-alive every ~5 seconds

Connection strategy:
  - Single aiohttp session, persistent TCP connection to stream endpoint.
  - If no data arrives for HEARTBEAT_TIMEOUT seconds → reconnect.
  - On any connection error → exponential backoff (2 s → 60 s).
  - On 401 / 403 → log clearly (bad API key or account ID) and back off slowly.

Retry policy:
  - CancelledError → propagated immediately (clean shutdown).
  - All other exceptions → backoff + reconnect forever.
  - Non-tradeable prices (tradeable=false) are silently dropped.

Configuration (from Settings):
  OANDA_API_KEY       — Personal Access Token from hub.oanda.com (practice or live)
  OANDA_ACCOUNT_ID    — Account ID (e.g., 101-004-12345678-001)
  OANDA_ENVIRONMENT   — "practice" | "live"  (controls stream URL)
  OANDA_INSTRUMENTS   — Comma-separated instrument list (e.g., EUR_USD,GBP_USD)
"""

import asyncio
import json
from datetime import datetime, timezone

import aiohttp
import structlog
from redis.asyncio import Redis

from ..config import Settings
from .normaliser import NormalisedTick, MARKET_TYPE_FOREX
from .publisher import publish_tick, update_heartbeat

log = structlog.get_logger()

VENUE = "oanda"
HEARTBEAT_TIMEOUT: int = 30   # seconds — reconnect if no data received in this window
INITIAL_RETRY: float = 2.0
MAX_RETRY: float = 60.0

# Oanda pricing stream path
_STREAM_PATH = "/v3/accounts/{account_id}/pricing/stream"


async def _stream_once(
    settings: Settings,
    redis: Redis,
    session: aiohttp.ClientSession,
) -> None:
    """
    Open one streaming connection and process messages until the connection drops
    or the heartbeat timeout fires.

    Raises on any error so the caller (run()) can implement retry.
    This function itself does not retry — it's a single connection lifecycle.
    """
    instruments = ",".join(settings.oanda_instrument_list)
    url = settings.oanda_stream_url + _STREAM_PATH.format(account_id=settings.OANDA_ACCOUNT_ID)
    headers = {
        "Authorization": f"Bearer {settings.OANDA_API_KEY}",
        "Accept-Datetime-Format": "RFC3339",
    }
    params = {"instruments": instruments, "snapshot": "true"}

    log.info(
        "oanda_feed.connecting",
        url=url,
        instruments=instruments,
        environment=settings.OANDA_ENVIRONMENT,
    )

    # total=None disables the overall request timeout (we're streaming indefinitely).
    # connect=10 gives 10 s to establish the initial TCP + TLS + HTTP handshake.
    timeout = aiohttp.ClientTimeout(total=None, connect=10)

    async with session.get(url, headers=headers, params=params, timeout=timeout) as resp:
        if resp.status == 401:
            raise aiohttp.ClientResponseError(
                resp.request_info,
                resp.history,
                status=401,
                message="Unauthorized — check OANDA_API_KEY",
            )
        if resp.status == 403:
            raise aiohttp.ClientResponseError(
                resp.request_info,
                resp.history,
                status=403,
                message="Forbidden — check OANDA_ACCOUNT_ID and key permissions",
            )
        if resp.status != 200:
            body = await resp.text()
            raise aiohttp.ClientResponseError(
                resp.request_info,
                resp.history,
                status=resp.status,
                message=f"Unexpected status {resp.status}: {body[:200]}",
            )

        log.info("oanda_feed.stream_connected", instruments=instruments)
        loop = asyncio.get_event_loop()
        last_data_at = loop.time()

        async for raw_line in resp.content:
            # Heartbeat timeout guard — reconnect if the stream goes silent
            now = loop.time()
            if now - last_data_at > HEARTBEAT_TIMEOUT:
                raise TimeoutError(
                    f"No data from Oanda stream for {HEARTBEAT_TIMEOUT}s — reconnecting"
                )
            last_data_at = now

            line = raw_line.decode("utf-8").strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                log.warning("oanda_feed.malformed_json", line=line[:120])
                continue

            msg_type = data.get("type")

            if msg_type == "HEARTBEAT":
                # Oanda sends these ~every 5 s to keep the connection alive
                await update_heartbeat(redis, VENUE)
                continue

            if msg_type != "PRICE":
                # Could be "TRANSACTION" or other event types — ignore
                continue

            if not data.get("tradeable", False):
                # Non-tradeable quote (market closed, weekend, etc.) — skip
                continue

            instrument = data.get("instrument", "")
            bids = data.get("bids", [])
            asks = data.get("asks", [])

            if not instrument or not bids or not asks:
                continue

            try:
                bid = float(bids[0]["price"])
                ask = float(asks[0]["price"])
            except (KeyError, ValueError, IndexError, TypeError):
                log.warning("oanda_feed.malformed_price", instrument=instrument)
                continue

            if bid <= 0 or ask <= 0 or bid >= ask:
                continue

            tick = NormalisedTick(
                timestamp=datetime.now(timezone.utc),
                venue=VENUE,
                symbol=instrument,  # e.g., "EUR_USD"
                market_type=MARKET_TYPE_FOREX,
                bid=bid,
                ask=ask,
            )
            await publish_tick(redis, tick, cache_max=settings.TICK_CACHE_MAX_SIZE)
            await update_heartbeat(redis, VENUE)


async def run(settings: Settings, redis: Redis) -> None:
    """
    Main entry point — connect to Oanda streaming and reconnect on any failure.

    Skips silently if OANDA_API_KEY or OANDA_ACCOUNT_ID is not set (operator
    hasn't configured Oanda yet — not an error at startup).

    Called from main.py lifespan as an asyncio.Task. Runs until cancelled.
    """
    if not settings.OANDA_API_KEY or not settings.OANDA_ACCOUNT_ID:
        log.warning(
            "oanda_feed.skipped",
            reason="OANDA_API_KEY or OANDA_ACCOUNT_ID not set — configure via dashboard or .env",
        )
        return

    if not settings.oanda_instrument_list:
        log.warning("oanda_feed.no_instruments", reason="OANDA_INSTRUMENTS is empty")
        return

    retry_delay = INITIAL_RETRY

    # Single session for all connection attempts — connection pooling is reused
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await _stream_once(settings, redis, session)
                # Clean exit from _stream_once (shouldn't happen — it loops forever
                # or raises). Reset delay and reconnect immediately.
                retry_delay = INITIAL_RETRY

            except asyncio.CancelledError:
                log.info("oanda_feed.cancelled")
                raise

            except aiohttp.ClientResponseError as exc:
                # 401/403 → config problem, back off slowly with a clear message
                log.error(
                    "oanda_feed.auth_error",
                    status=exc.status,
                    message=exc.message,
                    retry_in=retry_delay,
                    hint="Verify OANDA_API_KEY and OANDA_ACCOUNT_ID in dashboard or .env",
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, MAX_RETRY)

            except TimeoutError as exc:
                log.warning("oanda_feed.heartbeat_timeout", error=str(exc), retry_in=1.0)
                await asyncio.sleep(1.0)  # Brief pause before reconnect

            except (aiohttp.ClientError, OSError) as exc:
                log.warning(
                    "oanda_feed.connection_error",
                    error=str(exc),
                    retry_in=retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, MAX_RETRY)

            except Exception as exc:
                log.error(
                    "oanda_feed.unexpected_error",
                    error=str(exc),
                    retry_in=retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, MAX_RETRY)
