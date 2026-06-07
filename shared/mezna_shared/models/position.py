"""
Position model — net exposure per (venue, symbol, strategy, paper_mode).

One row per trading key. The executor maintains it with average-cost accounting
as fills settle (see mezna_shared.pnl.apply_fill): each fill VWAPs the entry
price, carries entry-side fees, and realizes net P&L when the position is reduced
or closed. realized_pnl on the row is the cumulative NET realized P&L for the
key's lifetime; the authoritative per-fill realized P&L lives on the trades table.

status:
  open — net_qty != 0 (live exposure; avg_price is the VWAP entry)
  flat — net_qty == 0 (no exposure; row retained for history/cumulative P&L)
"""

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint(
            "venue", "symbol", "strategy_type", "paper_mode",
            name="uq_positions_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Position key ──────────────────────────────────────────────────────────
    venue: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    strategy_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    paper_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    # ── Net exposure (average-cost) ───────────────────────────────────────────
    net_qty: Mapped[float] = mapped_column(
        Numeric(20, 8), nullable=False, default=0,
        comment="Signed net quantity: >0 long, <0 short, 0 flat",
    )
    avg_price: Mapped[float] = mapped_column(
        Numeric(20, 8), nullable=False, default=0,
        comment="VWAP entry price of the currently open position (0 when flat)",
    )
    open_fees: Mapped[float] = mapped_column(
        Numeric(20, 8), nullable=False, default=0,
        comment="Entry-side fees carried until the position is closed",
    )

    # ── Realized P&L (cumulative, net of fees) ────────────────────────────────
    realized_pnl: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False, default=0)
    fee_currency: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Status / timestamps ───────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="flat", index=True,
        comment="open | flat",
    )
    opened_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
        comment="When the current open position was first established (flat→open)",
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
        comment="When the position last went flat (open→flat)",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<Position {self.venue}:{self.symbol} {self.strategy_type} "
            f"net_qty={self.net_qty} avg={self.avg_price} status={self.status} "
            f"paper={self.paper_mode}>"
        )
