"""
Paper trading simulator.

Simulates an instant market fill at the current best bid or ask price
from the Redis tick cache. No exchange connection required.

Fill logic:
  BUY  → fills at ask (we pay the offer)
  SELL → fills at bid (we receive the bid)

Slippage calculation (versus mid):
  BUY  slippage = (ask - mid) / mid × 10 000 bps
  SELL slippage = (mid - bid) / mid × 10 000 bps

Fee:
  Binance taker fee applied on both legs (configured in settings).
  Oanda uses spread cost approximation.

If no tick is available (feed not yet running):
  Returns a rejected result so the operator knows the fill failed.
  This prevents phantom trades with zero price.
"""

import uuid
from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis

from mezna_shared.redis_client import RedisKeys
from . import OrderRequest, OrderResult

log = structlog.get_logger()


async def execute(
    order: OrderRequest,
    redis: Redis,
    fee_bps: float,
) -> OrderResult:
    """
    Simulate a market fill using the current Redis tick data.

    Args:
        order:   The normalised order to execute.
        redis:   Redis client for tick reads.
        fee_bps: Taker fee in basis points (from settings).

    Returns:
        OrderResult with status "filled" or "rejected" (no tick data).
    """
    # Read current best bid/ask from Redis (written by market-data service)
    tick_key = RedisKeys.latest_tick(order.venue, order.symbol)
    tick = await redis.hgetall(tick_key)

    if not tick:
        log.warning(
            "paper.no_tick",
            venue=order.venue,
            symbol=order.symbol,
            hint="market-data feed may not have started yet",
        )
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="rejected",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USDT",
            slippage_bps=0.0,
            rejection_reason="no_tick_data_available",
            raw_response={"venue": order.venue, "symbol": order.symbol},
        )

    try:
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        mid = float(tick["mid"])
    except (KeyError, ValueError, TypeError):
        log.error("paper.bad_tick", tick=tick)
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="error",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USDT",
            slippage_bps=0.0,
            rejection_reason="malformed_tick_data",
            raw_response=dict(tick),
        )

    # Fill at market (buyer pays ask, seller receives bid)
    if order.side == "buy":
        fill_price = ask
        slippage_bps = round((ask - mid) / mid * 10_000, 4) if mid > 0 else 0.0
    else:
        fill_price = bid
        slippage_bps = round((mid - bid) / mid * 10_000, 4) if mid > 0 else 0.0

    # Fee on notional value
    notional = order.quantity * fill_price
    fee_amount = notional * (fee_bps / 10_000)
    fee_currency = _infer_fee_currency(order.symbol, order.venue)

    # Synthetic exchange order ID for traceability in paper mode
    paper_order_id = f"paper_{uuid.uuid4().hex[:12]}"

    log.info(
        "paper.filled",
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        fill_price=fill_price,
        notional=round(notional, 4),
        fee=round(fee_amount, 6),
        slippage_bps=slippage_bps,
        client_order_id=order.client_order_id,
    )

    return OrderResult(
        client_order_id=order.client_order_id,
        exchange_order_id=paper_order_id,
        status="filled",
        filled_qty=order.quantity,
        average_fill_price=fill_price,
        fee=round(fee_amount, 8),
        fee_currency=fee_currency,
        slippage_bps=slippage_bps,
        raw_response={
            "paper_mode": True,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "fill_price": fill_price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def _infer_fee_currency(symbol: str, venue: str) -> str:
    """Best-effort fee currency inference from symbol format."""
    if venue == "oanda":
        return "USD"
    # CCXT format: BTC/USDT → USDT, BTC/USDT:USDT → USDT
    if "/" in symbol:
        quote = symbol.split("/")[1].split(":")[0]
        return quote
    return "USDT"
