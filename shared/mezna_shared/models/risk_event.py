"""
Risk event model — records every risk limit breach, halt, and cooldown.

This provides an auditable history of all risk-related decisions.
Separate from audit_log because risk events have their own lifecycle
(they can be resolved) and are queried independently.
"""

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Classification ────────────────────────────────────────────────────────
    event_type: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
        comment=(
            "halt.auto | halt.manual | kill_switch.activated | kill_switch.reset | "
            "cooldown.triggered | cooldown.expired | limit.daily_drawdown | "
            "limit.consecutive_losses | limit.max_exposure | limit.volatility_spike"
        )
    )

    # ── Context ───────────────────────────────────────────────────────────────
    strategy_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    venue: Mapped[str | None] = mapped_column(String(50), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Trigger values ────────────────────────────────────────────────────────
    trigger_value: Mapped[float | None] = mapped_column(
        Numeric(20, 8), nullable=True,
        comment="The value that triggered the event (e.g. actual drawdown pct)"
    )
    threshold_value: Mapped[float | None] = mapped_column(
        Numeric(20, 8), nullable=True,
        comment="The configured limit that was breached"
    )

    # ── Description ───────────────────────────────────────────────────────────
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Resolution ────────────────────────────────────────────────────────────
    auto_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    resolved_by: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
        comment="'system' for auto-resolved, user identifier otherwise"
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
        server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return (
            f"<RiskEvent id={self.id} type={self.event_type} "
            f"strategy={self.strategy_type} at={self.created_at}>"
        )
