"""
TradingView Signal Consumer — routes signals to all 6 strategies.

Handles: funding_arb, stat_arb, swing, breakout, mean_reversion_scalp, momentum.
Wires in the strategy rotation engine to track per-strategy outcome counts.
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
from .strategies.breakout import BreakoutStrategy
from .strategies.mean_reversion_scalp import MeanReversionScalpStrategy
from .strategies.momentum import MomentumStrategy
from mezna_shared.redis_client import RedisKeys, StreamNames

log = structlog.get_logger()

KNOWN_STRATEGIES = frozenset({
    "funding_arb", "stat_arb", "swing",
    "breakout", "mean_reversion_scalp", "momentum",
})


def _normalise_symbol(tv_symbol: str, venue: str) -> str:
    """Convert TradingView condensed symbol to internal format."""
    sym = tv_symbol.strip().upper()
    if "/" in sym or "_" in sym:
        return sym
    if venue == "oanda":
        for quote in ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"]:
            if sym.endswith(quote) and len(sym) > len(quote):
                return f"{sym[: -len(quote)]}_{quote}"
        return sym
    for quote in ["USDT", "USDC", "BUSD", "BTC", "ETH", "BNB", "USD"]:
        if sym.endswith(quote) and len(sym) > len(quote):
            return f"{sym[: -len(quote)]}/{quote}"
    return sym


async def _ensure_consumer_group(redis: Redis, settings: Settings) -> None:
    try:
        await redis.xgroup_create(
            RedisKeys.SIGNALS_TV, settings.TV_CONSUMER_GROUP, id="$", mkstream=True,
        )
        log.info("signal_consumer.group_created", group=settings.TV_CONSUMER_GROUP)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def _dispatch(
    msg_id: str,
    fields: dict,
    redis: Redis,
    settings: Settings,
    funding_arb: FundingArbStrategy,
    stat_arb: StatArbStrategy,
    swing: SwingStrategy,
    breakout: BreakoutStrategy,
    mean_reversion: MeanReversionScalpStrategy,
    momentum: MomentumStrategy,
    http_client: httpx.AsyncClient,
) -> bool:
    """
    Parse one stream entry and dispatch to the correct strategy.

    Returns True  → safe to ACK (successfully processed, or non-retryable error)
    Returns False → do NOT ACK (transient failure; message stays in PEL for retry)
    """
    raw = fields.get(StreamNames.PAYLOAD, "{}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("signal_consumer.parse_error", msg_id=msg_id, error=str(exc))
        return True  # parse error won't self-correct on retry — discard

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
                log.warning("signal_consumer.stale_signal_skipped",
                            age_seconds=round(age, 1), strategy=strategy_type)
                return True  # stale — opportunity has passed, no point retrying
        except (ValueError, TypeError):
            pass

    if await is_halted(redis):
        return True  # halted — discard; signals do not queue during halt

    if strategy_type not in KNOWN_STRATEGIES:
        log.warning("signal_consumer.unknown_strategy", strategy=strategy_type,
                    hint=f"must be one of: {', '.join(sorted(KNOWN_STRATEGIES))}")
        return True  # unknown strategy — won't change on retry

    state = await get_strategy_state(redis, strategy_type)
    if state.get("enabled") != "1":
        log.info("signal_consumer.strategy_disabled", strategy=strategy_type)
        return True  # disabled — won't change on retry

    if await is_on_cooldown(redis, strategy_type):
        log.info("signal_consumer.strategy_on_cooldown", strategy=strategy_type)
        return True  # on cooldown — opportunity too old by the time cooldown lifts

    symbol = _normalise_symbol(tv_symbol, venue)

    log.info("signal_consumer.dispatching",
             strategy=strategy_type, symbol=symbol, action=action, venue=venue)

    try:
        published = False
        if strategy_type == "funding_arb":
            published = await funding_arb.run_from_signal(
                redis=redis, symbol=symbol, action=action,
                state=state, client=http_client, tv_price=price,
            )
        elif strategy_type == "stat_arb":
            published = await stat_arb.run_from_signal(
                redis=redis, symbol=symbol, action=action,
                state=state, tv_price=price,
            )
        elif strategy_type == "swing":
            published = await swing.run_from_signal(
                redis=redis, symbol=symbol, action=action,
                venue=venue, state=state, tv_price=price, note=note,
            )
        elif strategy_type == "breakout":
            published = await breakout.run_from_signal(
                redis=redis, symbol=symbol, action=action,
                venue=venue, state=state, tv_price=price, note=note,
            )
        elif strategy_type == "mean_reversion_scalp":
            published = await mean_reversion.run_from_signal(
                redis=redis, symbol=symbol, action=action,
                venue=venue, state=state, tv_price=price, note=note,
            )
        elif strategy_type == "momentum":
            published = await momentum.run_from_signal(
                redis=redis, symbol=symbol, action=action,
                venue=venue, state=state, tv_price=price, note=note,
            )

        if published:
            log.info("signal_consumer.opportunity_published",
                     strategy=strategy_type, symbol=symbol, action=action)
        return True

    except Exception as exc:
        # Transient failure (exchange API, network, Redis) — leave in PEL for retry
        log.error("signal_consumer.strategy_error",
                  strategy=strategy_type, symbol=symbol, error=str(exc))
        return False


async def run(
    settings: Settings,
    redis: Redis,
    funding_arb: FundingArbStrategy,
    stat_arb: StatArbStrategy,
    swing: SwingStrategy,
    breakout: BreakoutStrategy,
    mean_reversion: MeanReversionScalpStrategy,
    momentum: MomentumStrategy,
    http_client: httpx.AsyncClient,
) -> None:
    """Main TradingView signal consumer loop."""
    await _ensure_consumer_group(redis, settings)

    log.info("signal_consumer.started",
             stream=RedisKeys.SIGNALS_TV,
             group=settings.TV_CONSUMER_GROUP,
             strategies=list(KNOWN_STRATEGIES))

    while True:
        try:
            messages = await redis.xreadgroup(
                groupname=settings.TV_CONSUMER_GROUP,
                consumername=settings.TV_CONSUMER_NAME,
                streams={RedisKeys.SIGNALS_TV: ">"},
                count=1,
                block=5000,
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
                should_ack = True
                try:
                    should_ack = await _dispatch(
                        msg_id=msg_id, fields=fields, redis=redis,
                        settings=settings,
                        funding_arb=funding_arb, stat_arb=stat_arb,
                        swing=swing, breakout=breakout,
                        mean_reversion=mean_reversion, momentum=momentum,
                        http_client=http_client,
                    )
                except Exception as exc:
                    log.error("signal_consumer.dispatch_error",
                              msg_id=msg_id, error=str(exc))
                    should_ack = False

                if should_ack:
                    try:
                        await redis.xack(
                            RedisKeys.SIGNALS_TV,
                            settings.TV_CONSUMER_GROUP, msg_id,
                        )
                    except Exception as ack_exc:
                        log.error("signal_consumer.ack_failed",
                                  msg_id=msg_id, error=str(ack_exc))
                else:
                    log.warning(
                        "signal_consumer.message_nacked",
                        msg_id=msg_id,
                        hint="transient error — stays in PEL, retry on next startup XCLAIM sweep",
                    )
