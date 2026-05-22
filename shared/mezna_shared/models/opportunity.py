"""
Opportunity model — a candidate trade signal before risk approval.

Lifecycle:
    detected → ai_scored (async) → risk_checked → executed | expired | rejected

This table is a TimescaleDB hypertable on detected_at.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, Boolean, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Signal origin ─────────────────────────────────────────────────────────
    strategy_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True,
        comment="funding_arb | stat_arb | swing"
    )
    venue: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True,
        comment="binance | oanda | cross"
    )
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="internal",
        comment="internal | tradingview"
    )
    symbol_primary: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    symbol_secondary: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Timing ────────────────────────────────────────────────────────────────
    # TimescaleDB partitions on this column
    detected_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, index=True
    )

    # ── Signal metrics ────────────────────────────────────────────────────────
    latency_profile: Mapped[str] = mapped_column(
        String(20), nullable=False, default="standard",
        comment="relaxed | standard | fast"
    )
    spread: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    z_score: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    funding_rate: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    expected_return_bps: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    fee_cost_bps: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    net_edge_bps: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)

    # ── AI filter (Phase 2) ───────────────────────────────────────────────────
    # Null = AI not yet run or timed out
    ai_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_timeout: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_scored_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # ── Risk gate ─────────────────────────────────────────────────────────────
    risk_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    risk_rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_checked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # ── Outcome ───────────────────────────────────────────────────────────────
    executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    paper_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── Raw data ──────────────────────────────────────────────────────────────
    raw_signal: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<Opportunity id={self.id} strategy={self.strategy_type} "
            f"venue={self.venue} edge={self.net_edge_bps}bps>"
        )
