"""
Kraken live order adapter — CCXT async REST (spot).

Kraken is used for spot crypto / FX pairs (e.g. stat-arb). ccxt.kraken maps its
native asset codes (XBT→BTC) to unified symbols, so strategies emit standard
"BTC/USD" / "BTC/USDT" and no symbol transform is needed here (spot, not perp).

Kraken spot has no public sandbox, so set_sandbox_mode is attempted only when
testnet is requested and ignored (logged) if unsupported — keep TRADING_MODE=paper
for simulation.

CRITICAL:
  Submits REAL orders to Kraken when TRADING_MODE=live; paper mode uses paper.py
  via the registry's paper-mode override.
"""

import structlog
import ccxt.async_support as ccxt

from . import OrderRequest, OrderResult

log = structlog.get_logger()


def make_exchange(api_key: str, secret: str, testnet: bool) -> "ccxt.kraken":
    """Create a Kraken spot CCXT exchange instance."""
    ex = ccxt.kraken({
        "apiKey": api_key,
        "secret": secret,
        "enableRateLimit": True,
    })
    if testnet:
        try:
            ex.set_sandbox_mode(True)
        except Exception:
            log.warning(
                "kraken.no_sandbox",
                hint="Kraken spot has no sandbox — keep TRADING_MODE=paper for simulation",
            )
    return ex


async def execute(
    order: OrderRequest,
    exchange: "ccxt.kraken",
    fee_bps: float,
) -> OrderResult:
    """Submit a spot market order to Kraken and return a normalised result."""
    log.info(
        "kraken.submitting",
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        client_order_id=order.client_order_id,
    )

    try:
        response = await exchange.create_order(
            symbol=order.symbol,
            type="market",
            side=order.side,
            amount=order.quantity,
            params={"clientOrderId": order.client_order_id},
        )

        filled_qty = float(response.get("filled", 0) or response.get("amount", 0))
        avg_price = float(response.get("average") or response.get("price") or 0)
        fee_info = response.get("fee") or {}
        fee_cost = float(fee_info.get("cost", 0) or (filled_qty * avg_price * fee_bps / 10_000))
        fee_currency = fee_info.get("currency", "USD")
        exchange_order_id = str(response.get("id", ""))

        log.info(
            "kraken.filled",
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
        log.warning("kraken.order_not_found", client_order_id=order.client_order_id)
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="error",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USD",
            slippage_bps=0.0,
            rejection_reason="order_not_found",
            raw_response={},
        )

    except ccxt.InsufficientFunds as exc:
        log.error("kraken.insufficient_funds", error=str(exc))
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="rejected",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USD",
            slippage_bps=0.0,
            rejection_reason=f"insufficient_funds: {exc}",
            raw_response={},
        )

    except ccxt.ExchangeError as exc:
        log.error("kraken.exchange_error", error=str(exc))
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
