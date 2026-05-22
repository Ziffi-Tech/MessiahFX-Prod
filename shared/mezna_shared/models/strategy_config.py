"""
Strategy configuration model — persisted strategy settings.

Each strategy has one row. Toggling a strategy on/off writes here
AND sets the Redis strategy state key for fast runtime reads.

The params JSONB field holds strategy-specific parameters.
"""

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Identity ──────────────────────────────────────────────────────────────
    strategy_type: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True,
        comment="funding_arb | stat_arb | swing"
    )

    # ── Mode ──────────────────────────────────────────────────────────────────
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    paper_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    latency_profile: Mapped[str] = mapped_column(
        String(20), nullable=False, default="standard",
        comment="relaxed | standard | fast"
    )

    # ── Strategy-specific parameters ──────────────────────────────────────────
    params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="Strategy-specific config (z-score thresholds, lookback windows, etc.)"
    )

    # ── Per-strategy risk overrides ───────────────────────────────────────────
    risk_overrides: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="Override global risk params for this strategy only (max_per_trade_pct, etc.)"
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str] = mapped_column(
        String(100), nullable=False, default="system"
    )

    def __repr__(self) -> str:
        return (
            f"<StrategyConfig type={self.strategy_type} "
            f"enabled={self.enabled} paper={self.paper_mode}>"
        )
