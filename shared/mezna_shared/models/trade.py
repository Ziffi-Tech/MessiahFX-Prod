"""
Trade model — a submitted or simulated order.

Lifecycle:
    pending → open → partially_filled → filled | cancelled | rejected

Key design decisions:
- client_order_id is generated locally for idempotency (prevents duplicate submissions)
- paper_mode flag separates simulation from real orders at the DB level
- All fills and fees are tracked for accurate P&L and reconciliation

This table is a TimescaleDB hypertable on opened_at.
"""

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Linkage ───────────────────────────────────────────────────────────────
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True,
        comment="FK to opportunities — nullable for manual/external trades"
    )

    # ── Exchange identifiers ──────────────────────────────────────────────────
    venue: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    exchange_order_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True,
        comment="Order ID returned by exchange. Null until order is placed."
    )
    client_order_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True,
        comment="Locally generated idempotency key. NEVER reuse."
    )

    # ── Order details ─────────────────────────────────────────────────────────
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    side: Mapped[str] = mapped_column(
        String(10), nullable=False,
        comment="buy | sell"
    )
    order_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="market | limit | stop_limit"
    )
    quantity: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)

    # ── Fill tracking ─────────────────────────────────────────────────────────
    filled_qty: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False, default=0)
    average_fill_price: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    fee: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    fee_currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    slippage_bps: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", index=True,
        comment="pending | open | partially_filled | filled | cancelled | rejected | error"
    )

    # ── Context ───────────────────────────────────────────────────────────────
    strategy_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    paper_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── P&L ──────────────────────────────────────────────────────────────────
    realized_pnl: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    realized_pnl_currency: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    opened_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    filled_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )

    # ── Raw exchange response ─────────────────────────────────────────────────
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Trade id={self.id} venue={self.venue} symbol={self.symbol} "
            f"side={self.side} qty={self.quantity} status={self.status} paper={self.paper_mode}>"
        )

    @property
    def is_open(self) -> bool:
        return self.status in ("pending", "open", "partially_filled")

    @property
    def is_complete(self) -> bool:
        return self.status in ("filled", "cancelled", "rejected", "error")
