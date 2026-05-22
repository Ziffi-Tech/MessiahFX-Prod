"""
Order adapters — translate normalised OrderRequest into exchange-specific calls.

OrderRequest  →  paper.execute()  →  OrderResult  (simulated fill, no exchange)
              →  binance.execute() →  OrderResult  (CCXT async, real exchange)
              →  oanda.execute()   →  OrderResult  (v20 REST, real exchange)

All adapters return the same OrderResult dataclass so the consumer doesn't
need to know which exchange was used.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OrderRequest:
    """
    Normalised order ready for execution.
    Built from the opportunity payload by the consumer before routing to an adapter.
    """
    client_order_id: str       # UUID4 — idempotency key, unique constraint in DB
    venue: str                 # "binance" | "oanda"
    symbol: str                # Exchange-native symbol ("BTC/USDT", "EUR_USD")
    side: str                  # "buy" | "sell"
    order_type: str            # "market" (Phase 5 — limit orders in Phase 6+)
    quantity: float            # Units of base currency
    strategy_type: str
    opportunity_id: str | None
    paper_mode: bool


@dataclass
class OrderResult:
    """
    Normalised result from any adapter.
    Maps directly to the trades table columns.
    """
    client_order_id: str
    exchange_order_id: str | None
    status: str                # "filled" | "rejected" | "error"
    filled_qty: float
    average_fill_price: float
    fee: float
    fee_currency: str
    slippage_bps: float
    rejection_reason: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)
