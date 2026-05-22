"""Initial schema — all core tables with TimescaleDB hypertables.

Revision ID: 001
Revises:
Create Date: 2026-05-18

Tables created:
- opportunities      (TimescaleDB hypertable on detected_at)
- trades             (TimescaleDB hypertable on opened_at)
- audit_log          (TimescaleDB hypertable on created_at)
- risk_events
- strategy_configs   (seeded with default configs)
- reconciliation_log
- market_snapshots   (TimescaleDB hypertable on time)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── opportunities ─────────────────────────────────────────────────────────
    op.create_table(
        "opportunities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("strategy_type", sa.String(50), nullable=False),
        sa.Column("venue", sa.String(50), nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="internal"),
        sa.Column("symbol_primary", sa.String(50), nullable=False),
        sa.Column("symbol_secondary", sa.String(50), nullable=True),
        sa.Column("detected_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("latency_profile", sa.String(20), nullable=False, server_default="standard"),
        sa.Column("spread", sa.Numeric(20, 8), nullable=True),
        sa.Column("z_score", sa.Numeric(10, 4), nullable=True),
        sa.Column("funding_rate", sa.Numeric(10, 6), nullable=True),
        sa.Column("expected_return_bps", sa.Numeric(10, 4), nullable=True),
        sa.Column("fee_cost_bps", sa.Numeric(10, 4), nullable=True),
        sa.Column("net_edge_bps", sa.Numeric(10, 4), nullable=True),
        sa.Column("ai_score", sa.Integer, nullable=True),
        sa.Column("ai_reasoning", sa.Text, nullable=True),
        sa.Column("ai_timeout", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("ai_scored_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("risk_approved", sa.Boolean, nullable=True),
        sa.Column("risk_rejection_reason", sa.Text, nullable=True),
        sa.Column("risk_checked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("executed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("expired", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("paper_mode", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("raw_signal", JSONB, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_opportunities_detected_at", "opportunities", ["detected_at"])
    op.create_index("ix_opportunities_strategy_type", "opportunities", ["strategy_type"])
    op.create_index("ix_opportunities_venue", "opportunities", ["venue"])
    op.create_index("ix_opportunities_symbol_primary", "opportunities", ["symbol_primary"])
    # Convert to TimescaleDB hypertable (partitioned by detected_at, 7-day chunks)
    op.execute(
        "SELECT create_hypertable('opportunities', 'detected_at', chunk_time_interval => INTERVAL '7 days');"
    )

    # ── trades ────────────────────────────────────────────────────────────────
    op.create_table(
        "trades",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("opportunity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("venue", sa.String(50), nullable=False),
        sa.Column("exchange_order_id", sa.String(255), nullable=True),
        sa.Column("client_order_id", sa.String(255), nullable=False, unique=True),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("order_type", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("limit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("filled_qty", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("average_fill_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("fee", sa.Numeric(20, 8), nullable=True),
        sa.Column("fee_currency", sa.String(20), nullable=True),
        sa.Column("slippage_bps", sa.Numeric(10, 4), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("strategy_type", sa.String(50), nullable=True),
        sa.Column("paper_mode", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("realized_pnl", sa.Numeric(20, 8), nullable=True),
        sa.Column("realized_pnl_currency", sa.String(20), nullable=True),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("filled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("raw_response", JSONB, nullable=True),
    )
    op.create_index("ix_trades_opened_at", "trades", ["opened_at"])
    op.create_index("ix_trades_venue", "trades", ["venue"])
    op.create_index("ix_trades_symbol", "trades", ["symbol"])
    op.create_index("ix_trades_status", "trades", ["status"])
    op.create_index("ix_trades_strategy_type", "trades", ["strategy_type"])
    op.create_index("ix_trades_paper_mode", "trades", ["paper_mode"])
    op.create_index("ix_trades_exchange_order_id", "trades", ["exchange_order_id"])
    op.execute(
        "SELECT create_hypertable('trades', 'opened_at', chunk_time_interval => INTERVAL '7 days');"
    )

    # ── audit_log ─────────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("service", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_event_type", "audit_log", ["event_type"])
    op.create_index("ix_audit_log_service", "audit_log", ["service"])
    op.create_index("ix_audit_log_entity_id", "audit_log", ["entity_id"])
    op.execute(
        "SELECT create_hypertable('audit_log', 'created_at', chunk_time_interval => INTERVAL '1 day');"
    )

    # ── risk_events ───────────────────────────────────────────────────────────
    op.create_table(
        "risk_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("strategy_type", sa.String(50), nullable=True),
        sa.Column("venue", sa.String(50), nullable=True),
        sa.Column("symbol", sa.String(50), nullable=True),
        sa.Column("trigger_value", sa.Numeric(20, 8), nullable=True),
        sa.Column("threshold_value", sa.Numeric(20, 8), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("auto_resolved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_risk_events_created_at", "risk_events", ["created_at"])
    op.create_index("ix_risk_events_event_type", "risk_events", ["event_type"])

    # ── strategy_configs ──────────────────────────────────────────────────────
    op.create_table(
        "strategy_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("strategy_type", sa.String(50), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("paper_mode", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("latency_profile", sa.String(20), nullable=False, server_default="standard"),
        sa.Column("params", JSONB, nullable=False, server_default="{}"),
        sa.Column("risk_overrides", JSONB, nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", sa.String(100), nullable=False, server_default="system"),
    )

    # Seed default strategy configs (all disabled until explicitly enabled via dashboard)
    op.execute("""
        INSERT INTO strategy_configs (strategy_type, enabled, paper_mode, latency_profile, params, risk_overrides)
        VALUES
            ('funding_arb',  false, true, 'relaxed',  '{"min_funding_rate_bps": 5, "hedge_ratio": 1.0, "max_hold_hours": 8}'::jsonb,    '{}'::jsonb),
            ('stat_arb',     false, true, 'standard', '{"z_score_entry": 2.0, "z_score_exit": 0.5, "lookback_hours": 24, "max_pairs": 2}'::jsonb, '{}'::jsonb),
            ('swing',        false, true, 'relaxed',  '{"timeframe": "4h", "min_signal_strength": 0.7}'::jsonb, '{}'::jsonb)
        ON CONFLICT (strategy_type) DO NOTHING;
    """)

    # ── reconciliation_log ────────────────────────────────────────────────────
    op.create_table(
        "reconciliation_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("venue", sa.String(50), nullable=False),
        sa.Column("orders_checked", sa.Integer, nullable=False, server_default="0"),
        sa.Column("divergences_found", sa.Integer, nullable=False, server_default="0"),
        sa.Column("divergence_details", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="clean"),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_reconciliation_log_run_at", "reconciliation_log", ["run_at"])

    # ── market_snapshots ──────────────────────────────────────────────────────
    op.create_table(
        "market_snapshots",
        sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("venue", sa.String(50), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("open", sa.Numeric(20, 8), nullable=True),
        sa.Column("high", sa.Numeric(20, 8), nullable=True),
        sa.Column("low", sa.Numeric(20, 8), nullable=True),
        sa.Column("close", sa.Numeric(20, 8), nullable=True),
        sa.Column("volume", sa.Numeric(20, 8), nullable=True),
        sa.Column("bid", sa.Numeric(20, 8), nullable=True),
        sa.Column("ask", sa.Numeric(20, 8), nullable=True),
        sa.Column("spread_bps", sa.Numeric(10, 4), nullable=True),
    )
    op.execute(
        "SELECT create_hypertable('market_snapshots', 'time', chunk_time_interval => INTERVAL '1 day');"
    )
    op.create_index("ix_market_snapshots_venue_symbol", "market_snapshots", ["venue", "symbol"])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.execute("DROP TABLE IF EXISTS market_snapshots CASCADE;")
    op.execute("DROP TABLE IF EXISTS reconciliation_log CASCADE;")
    op.execute("DROP TABLE IF EXISTS strategy_configs CASCADE;")
    op.execute("DROP TABLE IF EXISTS risk_events CASCADE;")
    op.execute("DROP TABLE IF EXISTS audit_log CASCADE;")
    op.execute("DROP TABLE IF EXISTS trades CASCADE;")
    op.execute("DROP TABLE IF EXISTS opportunities CASCADE;")
