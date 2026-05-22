"""
Executor consumer — reads risk-approved signals and submits orders.

Reads from signals:execution_queue (Redis Stream, consumer group "executor").
For each approved opportunity:
  1. Deserialise the full opportunity payload from the PAYLOAD field
  2. Build an order plan: 1 or 2 legs depending on strategy type
  3. Read current tick from Redis for position sizing (base currency units)
  4. Route each leg to the correct adapter (paper / binance / oanda)
  5. Persist every fill (or rejection) to the trades table
  6. ACK the stream message

Leg direction rules (hard-coded by strategy type):
  funding_arb  →  BUY primary (spot), SELL secondary (perp)
                  Rationale: harvest funding premium by holding spot long
                  and perp short until funding payment.
  stat_arb     →  SELL primary (overpriced), BUY secondary (underpriced)
                  Rationale: bet on spread reversion to mean.
  swing        →  Not yet implemented (needs OHLCV — placeholder skipped).

Position sizing:
  quantity = settings.position_usd / current_price
  price source = latest_tick from Redis (same tick cache written by market-data)
  Oanda: rounded to nearest integer (exchange requirement)
  Binance: rounded to 8 decimal places (CCXT validates precision)

Persistence guarantee:
  All fills are written to the DB before the stream message is ACK'd.
  If the DB write fails, the message is NOT ACK'd so it will be
  redelivered on the next XREADGROUP call. The INSERT uses
  ON CONFLICT DO NOTHING so replays are idempotent.

Consumer group start offset:
  id="$" — executor only processes NEW signals after startup.
  Rationale: stale signals (e.g., from 10 minutes ago) must not be
  executed on outdated market conditions. Lost signals are preferable
  to executing on stale risk approvals.

CRITICAL: This consumer runs in a single asyncio task (no parallelism).
  Order submission is intentionally serialised to prevent two legs of the
  same opportunity from racing, and to prevent exceeding rate limits.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import httpx
import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from mezna_shared.redis_client import RedisKeys, StreamNames


async def _notify(redis: Redis, event: str, **kwargs) -> None:
    """
    Push a notification to the notifications queue (fire-and-forget).
    Notification failures MUST NOT interrupt execution — exceptions are silently swallowed.
    """
    try:
        payload = json.dumps({
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        })
        await redis.rpush(RedisKeys.NOTIFICATION_QUEUE, payload)
    except Exception:
        pass  # Never let notification failures affect order execution
from .adapters import OrderRequest, OrderResult
from .adapters import paper as paper_adapter
from .adapters import binance as binance_adapter
from .adapters import oanda as oanda_adapter
from .adapters import mt5_adapter
from . import db as trade_db
from .config import Settings

log = structlog.get_logger()

_CONSUMER_GROUP = "executor"
_CONSUMER_NAME = "executor-1"
_BLOCK_MS = 100          # How long XREADGROUP blocks waiting for new entries
_READ_COUNT = 1          # Process one signal at a time — intentional serialisation

# Hard-coded leg directions per strategy type.
# Primary = symbol_primary from opportunity payload.
# Secondary = symbol_secondary from opportunity payload.
_LEG_DIRECTIONS: dict[str, list[dict]] = {
    "funding_arb": [
        {"leg": "primary",   "side": "buy"},    # buy spot → long exposure
        {"leg": "secondary", "side": "sell"},   # sell perp → short hedges spot
    ],
    "stat_arb": [
        {"leg": "primary",   "side": "sell"},   # sell overpriced leg
        {"leg": "secondary", "side": "buy"},    # buy underpriced leg
    ],
    # swing is handled separately — single-leg, direction from raw_signal
}


# ── Consumer group management ─────────────────────────────────────────────────

async def _ensure_group(redis: Redis) -> None:
    """Create the consumer group if it doesn't already exist."""
    try:
        await redis.xgroup_create(
            RedisKeys.SIGNALS_EXECUTION_QUEUE,
            _CONSUMER_GROUP,
            id="$",         # Only new signals after startup
            mkstream=True,
        )
        log.info("executor.consumer_group_created", group=_CONSUMER_GROUP)
    except Exception as exc:
        if "BUSYGROUP" in str(exc):
            log.debug("executor.consumer_group_exists", group=_CONSUMER_GROUP)
        else:
            raise


# ── Position sizing ───────────────────────────────────────────────────────────

async def _calc_quantity(
    redis: Redis,
    venue: str,
    symbol: str,
    side: str,
    position_usd: float,
) -> float | None:
    """
    Read the current tick and calculate order quantity in base currency.

    Buy  → use ask price (we pay the offer)
    Sell → use bid price (we receive the bid)

    Returns None if tick data is unavailable (feed not running).
    """
    # MT5: lot sizing is handled inside the bridge using live symbol info.
    # Return position_usd directly — no Redis tick lookup needed.
    if venue == "mt5":
        return position_usd

    tick_key = RedisKeys.latest_tick(venue, symbol)
    tick = await redis.hgetall(tick_key)
    if not tick:
        return None

    try:
        price = float(tick["ask"]) if side == "buy" else float(tick["bid"])
        if price <= 0:
            return None
        quantity = position_usd / price
        # Oanda requires integer units (minimum 1)
        if venue == "oanda":
            return max(1.0, round(quantity))
        # Binance accepts fractional units; 8 dp is safe for most pairs
        return round(quantity, 8)
    except (KeyError, ValueError, TypeError):
        log.error("executor.tick_parse_error", tick=dict(tick))
        return None


# ── Order plan builder ────────────────────────────────────────────────────────

def _build_order_plan(payload: dict) -> list[dict]:
    """
    Return an ordered list of leg descriptors for this opportunity.

    Each descriptor: {venue, symbol, side}
    Returns [] for unknown or deferred strategy types.
    """
    strategy_type = payload.get("strategy_type", "")
    venue = payload.get("venue", "binance")
    symbol_primary = payload.get("symbol_primary", "")
    symbol_secondary = payload.get("symbol_secondary")

    # ── Swing: single-leg directional trade, direction from TradingView signal ──
    if strategy_type == "swing":
        raw_signal = payload.get("raw_signal") or {}
        tv_action = raw_signal.get("tv_action", "")
        direction = raw_signal.get("direction", "")

        # Determine side from tv_action or direction field
        if tv_action in ("buy",) or direction == "long":
            side = "buy"
        elif tv_action in ("sell",) or direction == "short":
            side = "sell"
        else:
            log.warning(
                "executor.swing_unknown_direction",
                strategy_type=strategy_type,
                tv_action=tv_action,
                direction=direction,
                hint="Cannot determine trade direction — skipping",
            )
            return []

        if not symbol_primary:
            log.error("executor.swing_missing_symbol", strategy_type=strategy_type)
            return []

        return [{"venue": venue, "symbol": symbol_primary, "side": side}]

    # ── Spread strategies: two-leg from _LEG_DIRECTIONS ───────────────────────
    leg_spec = _LEG_DIRECTIONS.get(strategy_type)
    if leg_spec is None:
        log.warning("executor.unknown_strategy", strategy_type=strategy_type)
        return []

    legs = []
    for spec in leg_spec:
        symbol = symbol_primary if spec["leg"] == "primary" else symbol_secondary
        if not symbol:
            log.error(
                "executor.missing_symbol",
                leg=spec["leg"],
                strategy_type=strategy_type,
            )
            return []
        legs.append({"venue": venue, "symbol": symbol, "side": spec["side"]})

    return legs


# ── Adapter routing ───────────────────────────────────────────────────────────

def _fee_bps(venue: str, settings: Settings) -> float:
    """Return the taker fee in basis points for the given venue."""
    if venue == "oanda":
        return settings.OANDA_SPREAD_BPS
    return settings.BINANCE_TAKER_FEE_BPS


async def _execute_leg(
    leg: dict,
    quantity: float,
    opportunity_id: str | None,
    payload: dict,
    settings: Settings,
    redis: Redis,
    spot_exchange,
    perp_exchange,
    oanda_client: httpx.AsyncClient | None,
    mt5_client: httpx.AsyncClient | None,
) -> tuple[OrderRequest, OrderResult]:
    """
    Build an OrderRequest and route it to the correct adapter.
    Returns (order, result) — never raises.
    """
    client_order_id = str(uuid.uuid4())
    venue = leg["venue"]
    symbol = leg["symbol"]
    side = leg["side"]

    order = OrderRequest(
        client_order_id=client_order_id,
        venue=venue,
        symbol=symbol,
        side=side,
        order_type="market",
        quantity=quantity,
        strategy_type=payload.get("strategy_type", "unknown"),
        opportunity_id=opportunity_id,
        paper_mode=settings.is_paper,
    )

    try:
        if settings.is_paper:
            result = await paper_adapter.execute(
                order=order,
                redis=redis,
                fee_bps=_fee_bps(venue, settings),
            )

        elif venue == "binance":
            if spot_exchange is None or perp_exchange is None:
                raise RuntimeError("Binance exchange instances not initialised")
            result = await binance_adapter.execute(
                order=order,
                spot_exchange=spot_exchange,
                perp_exchange=perp_exchange,
                fee_bps=settings.BINANCE_TAKER_FEE_BPS,
            )

        elif venue == "oanda":
            if oanda_client is None:
                raise RuntimeError("Oanda HTTP client not initialised")
            result = await oanda_adapter.execute(
                order=order,
                client=oanda_client,
                api_key=settings.OANDA_API_KEY,
                account_id=settings.OANDA_ACCOUNT_ID,
                base_url=settings.oanda_rest_url,
            )

        elif venue == "mt5":
            if mt5_client is None:
                raise RuntimeError("MT5 HTTP client not initialised — check MT5_BRIDGE_URL")
            result = await mt5_adapter.execute(
                order=order,
                client=mt5_client,
                bridge_url=settings.MT5_BRIDGE_URL,
                api_key=settings.MT5_BRIDGE_API_KEY,
                spread_bps=settings.MT5_SPREAD_BPS,
            )

        else:
            raise ValueError(f"Unknown venue: {venue!r}")

    except Exception as exc:
        log.error(
            "executor.adapter_error",
            venue=venue,
            symbol=symbol,
            side=side,
            error=str(exc),
        )
        result = OrderResult(
            client_order_id=client_order_id,
            exchange_order_id=None,
            status="error",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USD" if venue == "oanda" else "USDT",
            slippage_bps=0.0,
            rejection_reason=str(exc),
            raw_response={},
        )

    return order, result


# ── Main message processor ────────────────────────────────────────────────────

async def _process(
    msg_id: str,
    fields: dict,
    redis: Redis,
    db_engine: AsyncEngine,
    settings: Settings,
    spot_exchange,
    perp_exchange,
    oanda_client: httpx.AsyncClient | None,
    mt5_client: httpx.AsyncClient | None,
) -> None:
    """
    Process one execution-queue message end-to-end.

    This function is intentionally not concurrent — it processes one message
    fully before the consumer loop picks up the next one.
    """
    opportunity_id = fields.get(StreamNames.OPPORTUNITY_ID)

    # Deserialise opportunity payload
    payload_raw = fields.get(StreamNames.PAYLOAD, "{}")
    try:
        payload = json.loads(payload_raw)
    except (json.JSONDecodeError, TypeError) as exc:
        log.error(
            "executor.bad_payload",
            msg_id=msg_id,
            error=str(exc),
            raw_preview=str(payload_raw)[:200],
        )
        return  # ACK in caller's finally — malformed payload won't self-correct

    strategy_type = payload.get("strategy_type", "unknown")
    venue = payload.get("venue", "unknown")
    ai_score = payload.get("ai_score")

    log.info(
        "executor.processing",
        msg_id=msg_id,
        opportunity_id=opportunity_id,
        strategy_type=strategy_type,
        venue=venue,
        ai_score=ai_score,
        paper=settings.is_paper,
    )

    # Build leg plan
    legs = _build_order_plan(payload)
    if not legs:
        log.info(
            "executor.skipped",
            strategy_type=strategy_type,
            opportunity_id=opportunity_id,
        )
        return

    # Execute each leg sequentially — preserves ordering and rate limits
    fills_attempted = 0
    fills_ok = 0

    for i, leg in enumerate(legs):
        leg_label = f"leg{i+1}/{len(legs)}"
        log.info(
            "executor.leg_start",
            label=leg_label,
            venue=leg["venue"],
            symbol=leg["symbol"],
            side=leg["side"],
        )

        # Position sizing from current tick
        quantity = await _calc_quantity(
            redis,
            venue=leg["venue"],
            symbol=leg["symbol"],
            side=leg["side"],
            position_usd=settings.position_usd,
        )

        if quantity is None or quantity <= 0:
            log.error(
                "executor.no_tick_for_sizing",
                venue=leg["venue"],
                symbol=leg["symbol"],
                side=leg["side"],
                opportunity_id=opportunity_id,
            )
            # Persist a rejected trade so the audit trail is complete
            dummy_id = str(uuid.uuid4())
            dummy_order = OrderRequest(
                client_order_id=dummy_id,
                venue=leg["venue"],
                symbol=leg["symbol"],
                side=leg["side"],
                order_type="market",
                quantity=0.0,
                strategy_type=strategy_type,
                opportunity_id=opportunity_id,
                paper_mode=settings.is_paper,
            )
            dummy_result = OrderResult(
                client_order_id=dummy_id,
                exchange_order_id=None,
                status="rejected",
                filled_qty=0.0,
                average_fill_price=0.0,
                fee=0.0,
                fee_currency="USD" if leg["venue"] == "oanda" else "USDT",
                slippage_bps=0.0,
                rejection_reason="no_tick_data_for_position_sizing",
                raw_response={},
            )
            try:
                await trade_db.persist_trade(db_engine, dummy_order, dummy_result, opportunity_id)
            except Exception as db_exc:
                log.error("executor.persist_failed_no_tick", error=str(db_exc))
            fills_attempted += 1
            continue

        # Submit order via adapter
        order, result = await _execute_leg(
            leg=leg,
            quantity=quantity,
            opportunity_id=opportunity_id,
            payload=payload,
            settings=settings,
            redis=redis,
            spot_exchange=spot_exchange,
            perp_exchange=perp_exchange,
            oanda_client=oanda_client,
            mt5_client=mt5_client,
        )
        fills_attempted += 1

        log.info(
            "executor.leg_result",
            label=leg_label,
            symbol=leg["symbol"],
            side=leg["side"],
            status=result.status,
            filled_qty=result.filled_qty,
            avg_price=result.average_fill_price,
            fee=result.fee,
            slippage_bps=result.slippage_bps,
            paper=settings.is_paper,
        )

        if result.status == "filled":
            fills_ok += 1

        # Publish fill alert (fire-and-forget — never blocks execution)
        await _notify(
            redis,
            "trade.fill",
            symbol=leg["symbol"],
            side=leg["side"],
            status=result.status,
            filled_qty=result.filled_qty,
            avg_price=result.average_fill_price,
            fee=result.fee,
            fee_currency=result.fee_currency,
            slippage_bps=result.slippage_bps,
            strategy_type=strategy_type,
            paper=settings.is_paper,
            opportunity_id=opportunity_id,
            reason=result.rejection_reason,
        )

        # Persist fill — must succeed before ACK
        try:
            await trade_db.persist_trade(db_engine, order, result, opportunity_id)
        except Exception as db_exc:
            # Re-raise so the caller does NOT ACK — message will be redelivered
            log.error(
                "executor.persist_failed",
                client_order_id=order.client_order_id,
                error=str(db_exc),
            )
            raise

    log.info(
        "executor.opportunity_done",
        opportunity_id=opportunity_id,
        fills_attempted=fills_attempted,
        fills_ok=fills_ok,
        all_filled=fills_ok == fills_attempted,
    )


# ── Consumer loop ─────────────────────────────────────────────────────────────

async def run(
    settings: Settings,
    redis: Redis,
    db_engine: AsyncEngine,
    spot_exchange,
    perp_exchange,
    oanda_client: httpx.AsyncClient | None,
    mt5_client: httpx.AsyncClient | None,
) -> None:
    """
    Main consumer loop — runs for the lifetime of the service.

    Reads one message at a time from the execution queue, processes it fully,
    then ACKs. On DB errors, message is NOT ACK'd (redelivered on restart).
    On parse or logic errors, message IS ACK'd (won't self-correct on replay).
    """
    await _ensure_group(redis)
    log.info(
        "executor.consumer_started",
        group=_CONSUMER_GROUP,
        consumer=_CONSUMER_NAME,
        trading_mode=settings.TRADING_MODE,
        position_usd=settings.position_usd,
    )

    while True:
        try:
            entries = await redis.xreadgroup(
                groupname=_CONSUMER_GROUP,
                consumername=_CONSUMER_NAME,
                streams={RedisKeys.SIGNALS_EXECUTION_QUEUE: ">"},
                count=_READ_COUNT,
                block=_BLOCK_MS,
            )
        except asyncio.CancelledError:
            log.info("executor.consumer_cancelled")
            break
        except Exception as exc:
            log.error("executor.xreadgroup_error", error=str(exc))
            await asyncio.sleep(1.0)
            continue

        if not entries:
            continue  # Timeout — loop and block again

        for _stream_key, messages in entries:
            for msg_id, fields in messages:
                ack = False
                try:
                    await _process(
                        msg_id=msg_id,
                        fields=fields,
                        redis=redis,
                        db_engine=db_engine,
                        settings=settings,
                        spot_exchange=spot_exchange,
                        perp_exchange=perp_exchange,
                        oanda_client=oanda_client,
                        mt5_client=mt5_client,
                    )
                    ack = True  # Safe to ACK — processing complete or irrecoverable error

                except asyncio.CancelledError:
                    log.warning(
                        "executor.cancelled_mid_message",
                        msg_id=msg_id,
                        hint="message NOT acked — will be redelivered on next start",
                    )
                    raise  # Propagate cancellation

                except Exception as exc:
                    # DB errors: do NOT ACK → message redelivered on restart
                    log.error(
                        "executor.processing_error",
                        msg_id=msg_id,
                        error=str(exc),
                        hint="message NOT acked — will retry on restart",
                    )

                finally:
                    if ack:
                        try:
                            await redis.xack(
                                RedisKeys.SIGNALS_EXECUTION_QUEUE,
                                _CONSUMER_GROUP,
                                msg_id,
                            )
                        except Exception as exc:
                            log.error("executor.xack_failed", msg_id=msg_id, error=str(exc))
