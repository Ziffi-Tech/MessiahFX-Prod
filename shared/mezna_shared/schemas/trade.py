"""Pydantic schemas for Trade — inter-service API contracts."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TradeCreate(BaseModel):
    """Schema for creating a new trade record (submitted by executor)."""

    opportunity_id: uuid.UUID | None = None
    venue: str = Field(..., pattern="^(binance|oanda)$")
    client_order_id: str = Field(
        ...,
        description="Locally generated idempotency key. Generate with uuid4.",
    )
    symbol: str
    side: str = Field(..., pattern="^(buy|sell)$")
    order_type: str = Field(..., pattern="^(market|limit|stop_limit)$")
    quantity: float = Field(..., gt=0)
    limit_price: float | None = None
    strategy_type: str | None = None
    paper_mode: bool = True


class TradeRead(BaseModel):
    """Full trade record as returned by journal service."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    opportunity_id: uuid.UUID | None
    venue: str
    exchange_order_id: str | None
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    limit_price: float | None
    filled_qty: float
    average_fill_price: float | None
    fee: float | None
    fee_currency: str | None
    slippage_bps: float | None
    status: str
    strategy_type: str | None
    paper_mode: bool
    rejection_reason: str | None
    realized_pnl: float | None
    realized_pnl_currency: str | None
    opened_at: datetime
    filled_at: datetime | None
    closed_at: datetime | None
    updated_at: datetime


class TradeStatusUpdate(BaseModel):
    """Status update from executor after exchange response or reconciliation."""

    exchange_order_id: str | None = None
    status: str = Field(
        ...,
        pattern="^(pending|open|partially_filled|filled|cancelled|rejected|error)$",
    )
    filled_qty: float | None = None
    average_fill_price: float | None = None
    fee: float | None = None
    fee_currency: str | None = None
    slippage_bps: float | None = None
    filled_at: datetime | None = None
    closed_at: datetime | None = None
    realized_pnl: float | None = None
    realized_pnl_currency: str | None = None
    rejection_reason: str | None = None
    raw_response: dict[str, Any] | None = None
