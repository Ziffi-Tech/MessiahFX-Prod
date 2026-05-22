"""
TradingView Signal Consumer.

Reads from the signals:tradingview Redis stream (written by the gateway
when TradingView fires a webhook) and routes each signal to the correct
strategy handler.

Pipeline per signal:
  1. Read stream entry (XREADGROUP with 5s block timeout)
  2. Parse JSON payload from the PAYLOAD field
  3. Validate age — skip if signal is older than TV_SIGNAL_MAX_AGE_SECONDS
  4. Check system gate (halt, strategy enabled, cooldown)
  5. Normalise symbol format (BTCUSDT → BTC/USDT)
  6. Dispatch to strategy.run_from_signal()
  7. ACK message (regardless of whether an opportunity was published)

ACK policy:
  Always ACK after processing, even if the opportunity was skipped
  (insufficient edge, stale signal, etc.). We do NOT re-process stale
  TV signals — they are time-sensitive and replaying them on restart
  would trigger trades in yesterday's market conditions.

Consumer group: "strategy"   (created on startup, MKSTREAM=True)
Consumer name:  from settings.TV_CONSUMER_NAME
"""

import asyncio
import json
from datetime import datetime, timezone

import httpx
import structlog
from redis.asyncio import Redis

from .config import Settings
from .strategies.base import get_strategy_state, is_halted, is_on_cooldown
from .strategies.funding_arb import FundingArbStrategy
from .strategies.stat_arb import StatArbStrategy
from .strategies.swing import SwingStrategy
from mezna_shared.redis_client import RedisKeys, StreamNames

log = structlog.get_logger()


# ── Symbol normalisation ───────────────────────────────────────────────────────

def _normalise_symbol(tv_symbol: str, venue: str) -> str:
    """
    Convert TradingView symbol format to internal format.

    TradingView (Binance charts) uses condensed symbols: BTCUSDT, ETHUSDT
    Our internal format uses slashes:              BTC/USDT, ETH/USDT

    Oanda symbols use underscores:                 EUR_USD, GBP_USD
    TradingView Oanda/FX:                          EURUSD,  GBPUSD

    Examples:
        BTCUSDT  (binance) → BTC/USDT
        ETHUSDT  (binance) → ETH/USDT
        SOLUSDT  (binance) → SOL/USDT
        EURUSD   (oanda)   → EUR_USD
        BTC/USDT           → BTC/USDT  (pass-through)
        EUR_USD            → EUR_USD   (pass-through)
    """
    sym = tv_symbol.strip().upper()

    # Already in internal format
    if "/" in sym or "_" in sym:
        return sym

    if venue == "oanda":
        for quote in ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"]:
            if sym.endswith(quote) and len(sym) > len(quote):
                base = sym[: -len(quote)]
                return f"{base}_{quote}"
        return sym

    # Binance / crypto
    for quote in ["USDT", "USDC", "BUSD", "BTC", "ETH", "BNB", "USD"]:
        if sym.endswith(quote) and len(sym) > len(quote):
            base = sym[: -len(quote)]
            return f"{base}/{quote}"
    return sym


# ── Consumer group setup ───────────────────────────────────────────────────────

async def _ensure_consumer_group(redis: Redis, settings: Settings) -> None:
    """
    Create the consumer group on signals:tradingview if it doesn't exist.

    Uses MKSTREAM so the stream is created if it doesn't exist yet.
    id="$" means we only consume signals received AFTER the service started —
    we never replay old TV signals (they are time-sensitive).
    """
    try:
        await redis.xgroup_create(
            RedisKeys.SIGNALS_TV,
            settings.TV_CONSUMER_GROUP,
            id="$",
            mkstream=True,
        )
        log.info(
            "signal_consumer.group_created",
            stream=RedisKeys.SIGNALS_TV,
            group=settings.TV_CONSUMER_GROUP,
        )
    except Exception as exc:
        if "BUSYGROUP" in str(exc):
            log.debug(
                "signal_consumer.group_exists",
                stream=RedisKeys.SIGNALS_TV,
                group=settings.TV_CONSUMER_GROUP,
            )
        else:
            log.error("signal_consumer.group_create_error", error=str(exc))
            raise


# ── Signal dispatcher ──────────────────────────────────────────────────────────

async def _dispatch(
    msg_id: str,
    fields: dict,
    redis: Redis,
    settings: Settings,
    funding_arb: FundingArbStrategy,
    stat_arb: StatArbStrategy,
    swing: SwingStrategy,
    http_client: httpx.AsyncClient,
) -> None:
    """
    Parse one stream entry and route it to the correct strategy.
    Logs the outcome. Never raises — errors are logged and swallowed.
    """
    # ── Parse payload ──────────────────────────────────────────────────────
    raw = fields.get(StreamNames.PAYLOAD, "{}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("signal_consumer.parse_error", msg_id=msg_id, error=str(exc))
        return

    strategy_type = data.get("strategy", "").lower()
    venue         = data.get("venue", "binance").lower()
    tv_symbol     = data.get("symbol", "")
    action        = data.get("action", "alert").lower()
    price         = data.get("price")
    note          = data.get("note")
    received_at   = data.get("received_at", "")

    # ── Staleness check ────────────────────────────────────────────────────
    if received_at:
        try:
            ts = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age > settings.TV_SIGNAL_MAX_AGE_SECONDS:
                log.warning(
                    "signal_consumer.stale_signal_skipped",
                    msg_id=msg_id,
                    age_seconds=round(age, 1),
                    max_age=settings.TV_SIGNAL_MAX_AGE_SECONDS,
                    strategy=strategy_type,
                    symbol=tv_symbol,
                )
                return
        except (ValueError, TypeError):
            pass  # Can't parse timestamp — proceed anyway

    # ── System halt check ──────────────────────────────────────────────────
    if await is_halted(redis):
        log.info("signal_consumer.skipped_halted", msg_id=msg_id, symbol=tv_symbol)
        return

    # ── Strategy gate: enabled + not on cooldown ───────────────────────────
    if strategy_type not in ("funding_arb", "stat_arb", "swing"):
        log.warning(
            "signal_consumer.unknown_strategy",
            msg_id=msg_id,
            strategy=strategy_type,
            hint="strategy must be one of: funding_arb, stat_arb, swing",
        )
        return

    state = await get_strategy_state(redis, strategy_type)
    if state.get("enabled") != "1":
        log.info(
            "signal_consumer.strategy_disabled",
            msg_id=msg_id,
            strategy=strategy_type,
            symbol=tv_symbol,
        )
        return

    if await is_on_cooldown(redis, strategy_type):
        log.info(
            "signal_consumer.strategy_on_cooldown",
            msg_id=msg_id,
            strategy=strategy_type,
        )
        return

    # ── Normalise symbol ───────────────────────────────────────────────────
    symbol = _normalise_symbol(tv_symbol, venue)

    log.info(
        "signal_consumer.dispatching",
        msg_id=msg_id,
        strategy=strategy_type,
        symbol=symbol,
        action=action,
        venue=venue,
    )

    # ── Route to strategy ──────────────────────────────────────────────────
    try:
        if strategy_type == "funding_arb":
            await funding_arb.run_from_signal(
                redis=redis,
                symbol=symbol,
                action=action,
                state=state,
                client=http_client,
                tv_price=price,
            )
        elif strategy_type == "stat_arb":
            await stat_arb.run_from_signal(
                redis=redis,
                symbol=symbol,
                action=action,
                state=state,
                tv_price=price,
            )
        elif strategy_type == "swing":
            await swing.run_from_signal(
                redis=redis,
                symbol=symbol,
                action=action,
                venue=venue,
                state=state,
                tv_price=price,
                note=note,
            )
    except Exception as exc:
        log.error(
            "signal_consumer.strategy_error",
            msg_id=msg_id,
            strategy=strategy_type,
            symbol=symbol,
            error=str(exc),
        )


# ── Main consumer loop ─────────────────────────────────────────────────────────

async def run(
    settings: Settings,
    redis: Redis,
    funding_arb: FundingArbStrategy,
    stat_arb: StatArbStrategy,
    swing: SwingStrategy,
    http_client: httpx.AsyncClient,
) -> None:
    """
    Main TradingView signal consumer loop.

    Blocks for up to 5 seconds waiting for new signals.
    Processes one signal at a time (TV signals are low-frequency).
    ACKs every message after processing regardless of outcome.
    """
    await _ensure_consumer_group(redis, settings)

    log.info(
        "signal_consumer.started",
        stream=RedisKeys.SIGNALS_TV,
        group=settings.TV_CONSUMER_GROUP,
        consumer=settings.TV_CONSUMER_NAME,
    )

    while True:
        try:
            messages = await redis.xreadgroup(
                groupname=settings.TV_CONSUMER_GROUP,
                consumername=settings.TV_CONSUMER_NAME,
                streams={RedisKeys.SIGNALS_TV: ">"},
                count=1,
                block=5000,   # 5s block — keeps the loop responsive to cancellation
            )
        except asyncio.CancelledError:
            log.info("signal_consumer.cancelled")
            raise
        except Exception as exc:
            log.error("signal_consumer.xreadgroup_error", error=str(exc))
            await asyncio.sleep(2.0)
            continue

        if not messages:
            continue

        for _stream_name, entries in messages:
            for msg_id, fields in entries:
                try:
                    await _dispatch(
                        msg_id=msg_id,
                        fields=fields,
                        redis=redis,
                        settings=settings,
                        funding_arb=funding_arb,
                        stat_arb=stat_arb,
                        swing=swing,
                        http_client=http_client,
                    )
                finally:
                    # Always ACK — TV signals are not replayed on restart
                    try:
                        await redis.xack(
                            RedisKeys.SIGNALS_TV,
                            settings.TV_CONSUMER_GROUP,
                            msg_id,
                        )
                    except Exception as ack_exc:
                        log.error("signal_consumer.ack_failed", msg_id=msg_id, error=str(ack_exc))
