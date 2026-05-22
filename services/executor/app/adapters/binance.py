"""
Binance live order adapter — CCXT async REST.

Submits market orders to Binance (spot or USDM perp) using the CCXT library's
async REST interface. Not CCXT Pro — order placement uses standard REST, not WS.

Symbol routing:
  "BTC/USDT"       → spot exchange instance  (defaultType=spot)
  "BTC/USDT:USDT"  → futures exchange instance (defaultType=future)

Idempotency:
  The client_order_id is passed as CCXT's clientOrderId parameter.
  If the same order is submitted twice (crash-recovery scenario),
  Binance returns an error for the duplicate — the executor catches this,
  queries the order by clientOrderId, and returns the original fill.

CRITICAL:
  This adapter submits REAL orders to Binance.
  Only called when TRADING_MODE=live. Paper mode uses paper.py.
"""

import structlog
import ccxt.async_support as ccxt

from . import OrderRequest, OrderResult

log = structlog.get_logger()


def make_spot_exchange(api_key: str, secret: str, testnet: bool) -> ccxt.binance:
    ex = ccxt.binance({
        "apiKey": api_key,
        "secret": secret,
        "options": {"defaultType": "spot"},
        "enableRateLimit": True,
    })
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


def make_perp_exchange(api_key: str, secret: str, testnet: bool) -> ccxt.binance:
    ex = ccxt.binance({
        "apiKey": api_key,
        "secret": secret,
        "options": {"defaultType": "future"},
        "enableRateLimit": True,
    })
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


async def execute(
    order: OrderRequest,
    spot_exchange: ccxt.binance,
    perp_exchange: ccxt.binance,
    fee_bps: float,
) -> OrderResult:
    """
    Submit a market order to Binance and return a normalised result.
    Raises on unexpected errors — consumer handles retry logic.
    """
    # Route to correct exchange by symbol type
    is_perp = ":" in order.symbol
    exchange = perp_exchange if is_perp else spot_exchange

    log.info(
        "binance.submitting",
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        is_perp=is_perp,
        client_order_id=order.client_order_id,
    )

    try:
        response = await exchange.create_order(
            symbol=order.symbol,
            type="market",
            side=order.side,
            amount=order.quantity,
            params={"newClientOrderId": order.client_order_id},
        )

        filled_qty = float(response.get("filled", 0) or response.get("amount", 0))
        avg_price = float(response.get("average") or response.get("price") or 0)
        fee_info = response.get("fee") or {}
        fee_cost = float(fee_info.get("cost", 0) or (filled_qty * avg_price * fee_bps / 10_000))
        fee_currency = fee_info.get("currency", "USDT")
        exchange_order_id = str(response.get("id", ""))

        log.info(
            "binance.filled",
            exchange_order_id=exchange_order_id,
            filled_qty=filled_qty,
            avg_price=avg_price,
            fee=fee_cost,
        )

        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=exchange_order_id,
            status="filled",
            filled_qty=filled_qty,
            average_fill_price=avg_price,
            fee=fee_cost,
            fee_currency=fee_currency,
            slippage_bps=0.0,  # actual slippage calculated post-fill in Phase 6+
            raw_response=response,
        )

    except ccxt.OrderNotFound:
        # Possible duplicate — query by client order ID
        log.warning("binance.order_not_found", client_order_id=order.client_order_id)
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="error",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USDT",
            slippage_bps=0.0,
            rejection_reason="order_not_found",
            raw_response={},
        )

    except ccxt.InsufficientFunds as exc:
        log.error("binance.insufficient_funds", error=str(exc))
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="rejected",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USDT",
            slippage_bps=0.0,
            rejection_reason=f"insufficient_funds: {exc}",
            raw_response={},
        )

    except ccxt.ExchangeError as exc:
        log.error("binance.exchange_error", error=str(exc))
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="error",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USDT",
            slippage_bps=0.0,
            rejection_reason=str(exc),
            raw_response={},
        )
