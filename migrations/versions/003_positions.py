"""Positions table — net exposure + realized P&L per trading key.

Revision ID: 003
Revises: 002
Create Date: 2026-06-07

Adds the `positions` table used by the executor to track net exposure per
(venue, symbol, strategy_type, paper_mode) with average-cost accounting, and to
populate trades.realized_pnl on each fill that reduces or closes a position.

The trades.realized_pnl / realized_pnl_currency columns already exist (001);
this migration only adds the positions ledger that drives them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "positions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("venue", sa.String(50), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("strategy_type", sa.String(50), nullable=False),
        sa.Column("paper_mode", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("net_qty", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("avg_price", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("open_fees", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("fee_currency", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="flat"),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_unique_constraint(
        "uq_positions_key", "positions",
        ["venue", "symbol", "strategy_type", "paper_mode"],
    )
    op.create_index("ix_positions_venue", "positions", ["venue"])
    op.create_index("ix_positions_symbol", "positions", ["symbol"])
    op.create_index("ix_positions_strategy_type", "positions", ["strategy_type"])
    op.create_index("ix_positions_paper_mode", "positions", ["paper_mode"])
    op.create_index("ix_positions_status", "positions", ["status"])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS positions CASCADE;")
