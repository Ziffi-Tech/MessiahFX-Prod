"""
Audit log model — immutable append-only record of every system event.

Every state transition, risk decision, AI score, kill switch activation,
and order lifecycle event is recorded here. This is the source of truth
for post-incident investigation and regulatory audit.

CRITICAL: Do not add UPDATE or DELETE operations against this table.
It is append-only by design. TimescaleDB hypertable on created_at.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Event classification ──────────────────────────────────────────────────
    event_type: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
        comment=(
            "e.g. opportunity.detected, risk.approved, risk.rejected, "
            "trade.submitted, trade.filled, kill_switch.activated, "
            "ai.scored, strategy.toggled, system.startup"
        )
    )
    service: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True,
        comment="Originating service name"
    )

    # ── Optional entity linkage ───────────────────────────────────────────────
    entity_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="opportunity | trade | risk_event | strategy"
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # ── Event data ────────────────────────────────────────────────────────────
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="Full event payload. Schema varies by event_type."
    )
    metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="Runtime context: hostname, version, correlation_id, etc."
    )

    # ── Timing ────────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
        server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} event={self.event_type} "
            f"service={self.service} at={self.created_at}>"
        )
