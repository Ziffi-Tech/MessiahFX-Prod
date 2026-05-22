"""
MT5 execution adapter.

Translates a normalised OrderRequest into an HTTP call to the MT5 Bridge
service and converts the response back to an OrderResult.

The bridge (services/mt5-bridge) runs natively on Windows alongside the
MT5 terminal. This adapter connects to it over HTTP from inside the container.

Bridge URL:
  Default: http://host.containers.internal:8010  (Podman on Windows)
  Override: MT5_BRIDGE_URL env var

Position sizing contract:
  OrderRequest.quantity carries position_usd for MT5 orders.
  The bridge handles USD → lot conversion using live symbol info from MT5.
  This keeps lot math accurate to the broker's exact contract specification.

Slippage calculation:
  Measured as (fill_price - reference_price) / reference_price × 10000 bps.
  Reference price = bid (sell) or ask (buy) at order submission time.
  Bridge returns the actual fill_price from MT5.
"""

import structlog
import httpx

from . import OrderRequest, OrderResult

log = structlog.get_logger()

# Timeout for bridge calls — MT5 order submission is fast but can stall
# if the terminal is frozen. Hard-cap at 15s.
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class MT5AdapterError(RuntimeError):
    """Raised when the bridge is unreachable or returns an unexpected error."""


async def execute(
    order: OrderRequest,
    client: httpx.AsyncClient,
    bridge_url: str,
    api_key: str = "",
    spread_bps: float = 10.0,
) -> OrderResult:
    """
    Submit a market order via the MT5 bridge.

    Args:
        order:       Normalised OrderRequest (quantity = position_usd for MT5).
        client:      Shared httpx.AsyncClient from the executor lifespan.
        bridge_url:  Base URL of the MT5 bridge (e.g. http://host.containers.internal:8010).
        api_key:     Bearer token for bridge authentication.
        spread_bps:  Estimated spread cost in bps (used as fee proxy for paper fills).

    Returns:
        OrderResult with status "filled" | "simulated" | "rejected" | "error".
    """
    headers = {"X-Api-Key": api_key} if api_key else {}
    payload = {
        "symbol": order.symbol,
        "side": order.side,
        "position_usd": order.quantity,   # quantity = position_usd for MT5
        "client_order_id": order.client_order_id,
        "strategy_type": order.strategy_type,
        "paper_mode": order.paper_mode,
        "comment": f"MZQ-{order.strategy_type[:8]}",
    }

    try:
        resp = await client.post(
            f"{bridge_url}/order/place",
            json=payload,
            headers=headers,
            timeout=_TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        log.error(
            "mt5_adapter.bridge_timeout",
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            error=str(exc),
        )
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="error",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USD",
            slippage_bps=0.0,
            rejection_reason=f"MT5 bridge timeout: {exc}",
            raw_response={},
        )
    except httpx.RequestError as exc:
        log.error(
            "mt5_adapter.bridge_unreachable",
            client_order_id=order.client_order_id,
            bridge_url=bridge_url,
            error=str(exc),
        )
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="error",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USD",
            slippage_bps=0.0,
            rejection_reason=f"MT5 bridge unreachable: {exc}",
            raw_response={},
        )

    if resp.status_code == 503:
        body = resp.json() if resp.content else {}
        reason = body.get("detail", "MT5 bridge service unavailable")
        log.error(
            "mt5_adapter.bridge_503",
            client_order_id=order.client_order_id,
            reason=reason,
        )
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="error",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USD",
            slippage_bps=0.0,
            rejection_reason=reason,
            raw_response={},
        )

    if resp.status_code not in (200, 201):
        log.error(
            "mt5_adapter.bridge_error",
            client_order_id=order.client_order_id,
            status_code=resp.status_code,
            body=resp.text[:200],
        )
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=None,
            status="error",
            filled_qty=0.0,
            average_fill_price=0.0,
            fee=0.0,
            fee_currency="USD",
            slippage_bps=0.0,
            rejection_reason=f"Bridge HTTP {resp.status_code}: {resp.text[:100]}",
            raw_response={},
        )

    data = resp.json()
    bridge_status = data.get("status", "error")
    fill_price = float(data.get("fill_price", 0.0))
    lots = float(data.get("lots", 0.0))
    rejection_reason = data.get("rejection_reason")
    mt5_order_id = data.get("mt5_order_id")

    # Map bridge status to our OrderResult status
    if bridge_status in ("filled", "simulated"):
        status = bridge_status
        filled_qty = lots
        # Estimate fee from spread_bps on notional
        notional_usd = order.quantity   # position_usd
        fee = round(notional_usd * spread_bps / 10_000, 6)
        slippage_bps = 0.0  # Market orders: slippage is spread cost, already captured in fee
    else:
        # "rejected" or "error"
        status = bridge_status
        filled_qty = 0.0
        fee = 0.0
        slippage_bps = 0.0

    log.info(
        "mt5_adapter.result",
        client_order_id=order.client_order_id,
        symbol=order.symbol,
        side=order.side,
        lots=lots,
        fill_price=fill_price,
        status=status,
        mt5_order_id=mt5_order_id,
    )

    return OrderResult(
        client_order_id=order.client_order_id,
        exchange_order_id=str(mt5_order_id) if mt5_order_id else None,
        status=status,
        filled_qty=filled_qty,
        average_fill_price=fill_price,
        fee=fee,
        fee_currency="USD",
        slippage_bps=slippage_bps,
        rejection_reason=rejection_reason,
        raw_response=data,
    )
