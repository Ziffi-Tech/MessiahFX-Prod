"""
OKX live order adapter — CCXT async REST (linear USDT perpetuals / swap).

Mirrors bybit.py: OKX uses the same CCXT model. Submits market orders to OKX's
linear USDT-margined swap market.

Symbol handling:
  OKX linear swaps use the unified symbol BASE/QUOTE:QUOTE (e.g. BTC/USDT:USDT).
  Strategies emit spot-style "BTC/USDT", so _to_linear_symbol() adds the
  settlement suffix; already-suffixed symbols pass through.

Idempotency:
  client_order_id is passed as CCXT's unified clientOrderId (mapped to OKX's
  clOrdId), so a crash-recovery replay is rejected as a duplicate.

CRITICAL:
  Submits REAL orders to OKX. Only used when TRADING_MODE=live; paper mode uses
  paper.py via the registry's paper-mode override.
"""

import structlog
import ccxt.async_support as ccxt

from . import OrderRequest, OrderResult

log = structlog.get_logger()


def make_exchange(api_key: str, secret: str, testnet: bool, password: str = "") -> "ccxt.okx":
    """Create an OKX linear-swap CCXT exchange instance.

    OKX requires an API passphrase in addition to key/secret; pass it via
    `password` (OKX_API_PASSWORD). set_sandbox_mode enables OKX demo trading.
    """
    ex = ccxt.okx({
        "apiKey": api_key,
        "secret": secret,
        "password": password or None,
        "options": {"defaultType": "swap"},  # linear USDT-margined perpetuals
        "enableRateLimit": True,
    })
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


def _to_linear_symbol(symbol: str) -> str:
    """Map a spot-style symbol to OKX's linear-swap unified form (BTC/USDT:USDT)."""
    if ":" in symbol or "/" not in symbol:
        return symbol
    quote = symbol.split("/", 1)[1]
    return f"{symbol}:{quote}"


async def execute(
    order: OrderRequest,
    exchange: "ccxt.okx",
    fee_bps: float,
) -> OrderResult:
    """Submit a market order to OKX (linear swap) and return a normalised result."""
    symbol = _to_linear_symbol(order.symbol)

    log.info(
        "okx.submitting",
        symbol=symbol,
        side=order.side,
        quantity=order.quantity,
        client_order_id=order.client_order_id,
    )

    try:
        response = await exchange.create_order(
            symbol=symbol,
            type="market",
            side=order.side,
            amount=order.quantity,
            params={"clientOrderId": order.client_order_id},
        )

        filled_qty = float(response.get("filled", 0) or response.get("amount", 0))
        avg_price = float(response.get("average") or response.get("price") or 0)
        fee_info = response.get("fee") or {}
        fee_cost = float(fee_info.get("cost", 0) or (filled_qty * avg_price * fee_bps / 10_000))
        fee_currency = fee_info.get("currency", "USDT")
        exchange_order_id = str(response.get("id", ""))

        log.info(
            "okx.filled",
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
            slippage_bps=0.0,
            raw_response=response,
        )

    except ccxt.OrderNotFound:
        log.warning("okx.order_not_found", client_order_id=order.client_order_id)
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
        log.error("okx.insufficient_funds", error=str(exc))
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
        log.error("okx.exchange_error", error=str(exc))
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
