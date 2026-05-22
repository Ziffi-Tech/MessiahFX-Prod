"""
Oanda v20 live order adapter — httpx REST.

Submits MARKET orders to Oanda via the v20 REST API.

Unit convention:
  Positive units = buy (long)
  Negative units = sell (short)
  Units are in the base currency of the instrument.

  For EUR_USD: 1 unit = 1 EUR
  For USD_JPY: 1 unit = 1 USD

Position sizing:
  quantity_usd is the dollar value to trade.
  units = quantity_usd / ask_price  (buy)
         or = -quantity_usd / bid_price (sell, negative units)
  Oanda requires integer units for most instruments.

Idempotency:
  Oanda does not support client order IDs for market orders natively.
  We truncate the UUID to 32 chars for the clientExtensions.id field.

CRITICAL:
  This adapter submits REAL orders to Oanda.
  Only called when TRADING_MODE=live. Paper mode uses paper.py.
"""

import json

import httpx
import structlog

from . import OrderRequest, OrderResult

log = structlog.get_logger()

_ORDER_PATH = "/v3/accounts/{account_id}/orders"
_TRANSACTION_PATH = "/v3/accounts/{account_id}/transactions/{transaction_id}"


async def execute(
    order: OrderRequest,
    client: httpx.AsyncClient,
    api_key: str,
    account_id: str,
    base_url: str,
) -> OrderResult:
    """
    Submit a MARKET order to Oanda v20 REST API.
    Returns a normalised OrderResult.
    """
    # Oanda units: positive = buy, negative = sell
    units_abs = round(order.quantity)
    units = units_abs if order.side == "buy" else -units_abs

    body = {
        "order": {
            "type": "MARKET",
            "instrument": order.symbol,   # e.g., "EUR_USD"
            "units": str(units),
            "timeInForce": "FOK",         # Fill-or-Kill — no partial fills
            "clientExtensions": {
                "id": order.client_order_id[:32],
                "comment": f"mezna:{order.strategy_type}",
            },
        }
    }

    url = base_url + _ORDER_PATH.format(account_id=account_id)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept-Datetime-Format": "RFC3339",
    }

    log.info(
        "oanda.submitting",
        symbol=order.symbol,
        side=order.side,
        units=units,
        client_order_id=order.client_order_id[:32],
    )

    try:
        resp = await client.post(url, headers=headers, content=json.dumps(body), timeout=10.0)
        data = resp.json()

        if resp.status_code not in (200, 201):
            error_msg = data.get("errorMessage", str(data))
            log.error("oanda.order_rejected", status=resp.status_code, error=error_msg)
            return OrderResult(
                client_order_id=order.client_order_id,
                exchange_order_id=None,
                status="rejected",
                filled_qty=0.0,
                average_fill_price=0.0,
                fee=0.0,
                fee_currency="USD",
                slippage_bps=0.0,
                rejection_reason=error_msg,
                raw_response=data,
            )

        # Parse fill from response
        fill = data.get("orderFillTransaction", {})
        trade_id = fill.get("tradeOpened", {}).get("tradeID", "")
        avg_price = float(fill.get("price", 0))
        filled_units = abs(float(fill.get("units", units)))
        fee_usd = abs(float(fill.get("commission", 0)))
        transaction_id = fill.get("id", "")

        log.info(
            "oanda.filled",
            trade_id=trade_id,
            avg_price=avg_price,
            filled_units=filled_units,
            fee_usd=fee_usd,
        )

        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=transaction_id or trade_id,
            status="filled",
            filled_qty=filled_units,
            average_fill_price=avg_price,
            fee=fee_usd,
            fee_currency="USD",
            slippage_bps=0.0,
            raw_response=data,
        )

    except httpx.TimeoutException as exc:
        log.error("oanda.timeout", error=str(exc))
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="error",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USD",
            slippage_bps=0.0,
            rejection_reason=f"timeout: {exc}",
            raw_response={},
        )

    except Exception as exc:
        log.error("oanda.unexpected_error", error=str(exc))
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="error",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USD",
            slippage_bps=0.0,
            rejection_reason=str(exc),
            raw_response={},
        )
