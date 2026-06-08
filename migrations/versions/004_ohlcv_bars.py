"""OHLCV bars — persisted historical candles for backtesting + bar-mode history.

Revision ID: 004
Revises: 003
Create Date: 2026-06-08

Adds the `ohlcv_bars` table: a venue/symbol/interval-keyed store of OHLCV
candles. Two producers write to it (last-writer-wins on the natural key):

  * the market-data live bar writer — resamples the Redis tick cache into
    completed candles as the system runs (volume = tick count, a liquidity proxy
    for quote feeds), source='live_ticks';
  * the ccxt OHLCV backfill — seeds deep history straight from the exchange REST
    API (real traded volume), source='exchange_rest'.

The backtest service reads ranges from this table instead of (or before) hitting
the Binance public API, and it is the persisted-history prerequisite for the
directional (bar-based) strategies and a future vectorbt portfolio backtest
(see docs/decisions/0001-vectorbt-backtest-deferral.md).

Natural composite PK (venue, symbol, interval, bucket_start): the key IS the
dedup target, so upserts ON CONFLICT hit the PK directly with no surrogate-id
bloat on what is a high-row-count timeseries table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ohlcv_bars",
        sa.Column("venue", sa.String(50), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("interval", sa.String(10), nullable=False),
        sa.Column("bucket_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(20, 8), nullable=False),
        sa.Column("high", sa.Numeric(20, 8), nullable=False),
        sa.Column("low", sa.Numeric(20, 8), nullable=False),
        sa.Column("close", sa.Numeric(20, 8), nullable=False),
        sa.Column("volume", sa.Numeric(28, 8), nullable=False, server_default="0"),
        sa.Column("source", sa.String(20), nullable=False, server_default="live_ticks"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("venue", "symbol", "interval", "bucket_start", name="pk_ohlcv_bars"),
    )
    # Range-scan index for "give me venue/symbol/interval between t0 and t1
    # ordered by time" — the backtest read pattern. The PK already covers this
    # prefix, but an explicit index documents intent and helps the planner on
    # bucket_start-ordered scans within a key.
    op.create_index(
        "ix_ohlcv_bars_lookup",
        "ohlcv_bars",
        ["venue", "symbol", "interval", "bucket_start"],
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ohlcv_bars CASCADE;")
