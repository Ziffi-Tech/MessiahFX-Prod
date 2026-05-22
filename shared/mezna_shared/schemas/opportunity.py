"""Pydantic schemas for Opportunity — inter-service API contracts."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class OpportunityCreate(BaseModel):
    """Schema for creating a new opportunity from a strategy signal."""

    strategy_type: str = Field(..., pattern="^(funding_arb|stat_arb|swing)$")
    venue: str = Field(..., pattern="^(binance|oanda|cross|mt5)$")
    source: str = Field(default="internal", pattern="^(internal|tradingview)$")
    symbol_primary: str
    symbol_secondary: str | None = None
    detected_at: datetime
    latency_profile: str = Field(default="standard", pattern="^(relaxed|standard|fast)$")

    # Signal metrics — optional because not all strategies populate all fields
    spread: float | None = None
    z_score: float | None = None
    funding_rate: float | None = None
    expected_return_bps: float | None = None
    fee_cost_bps: float | None = None
    net_edge_bps: float | None = None

    paper_mode: bool = True
    raw_signal: dict[str, Any] | None = None

    @field_validator("net_edge_bps")
    @classmethod
    def net_edge_must_be_positive(cls, v: float | None) -> float | None:
        """Warn if net edge is negative — should not reach this schema if so."""
        if v is not None and v <= 0:
            raise ValueError("net_edge_bps must be positive for a valid opportunity")
        return v


class OpportunityRead(BaseModel):
    """Full opportunity as returned by journal/strategy services."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    strategy_type: str
    venue: str
    source: str
    symbol_primary: str
    symbol_secondary: str | None
    detected_at: datetime
    latency_profile: str

    spread: float | None
    z_score: float | None
    funding_rate: float | None
    expected_return_bps: float | None
    fee_cost_bps: float | None
    net_edge_bps: float | None

    ai_score: int | None
    ai_reasoning: str | None
    ai_timeout: bool

    risk_approved: bool | None
    risk_rejection_reason: str | None

    executed: bool
    expired: bool
    paper_mode: bool
    created_at: datetime


class OpportunityUpdate(BaseModel):
    """Partial update schema — used by AI filter and risk engine."""

    # AI filter writes these
    ai_score: int | None = Field(None, ge=0, le=100)
    ai_reasoning: str | None = None
    ai_timeout: bool | None = None
    ai_scored_at: datetime | None = None

    # Risk engine writes these
    risk_approved: bool | None = None
    risk_rejection_reason: str | None = None
    risk_checked_at: datetime | None = None

    # Executor writes these
    executed: bool | None = None
    expired: bool | None = None


class TradingViewSignal(BaseModel):
    """
    Schema for inbound TradingView webhook alerts.

    Pine Script alert messages must be formatted as JSON matching this schema.
    Non-conforming payloads are rejected at the gateway with HTTP 422.

    Example Pine Script alert message:
        {
          "strategy": "stat_arb",
          "venue": "binance",
          "symbol": "BTCUSDT",
          "action": "buy",
          "price": {{close}},
          "source": "tradingview"
        }
    """

    strategy: str
    venue: str
    symbol: str
    action: str = Field(..., pattern="^(buy|sell|close|alert)$")
    price: float | None = None
    source: str = "tradingview"
    note: str | None = None
    raw: dict[str, Any] | None = None
