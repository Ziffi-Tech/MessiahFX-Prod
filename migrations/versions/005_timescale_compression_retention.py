"""TimescaleDB hypertables + compression + retention (Phase 5 — scale).

Migration 001 created the time-series tables as plain Postgres tables and DEFERRED
hypertable conversion ("once the schema is stable") because a hypertable needs its
time column inside the primary key. This is that deferred migration: it converts the
safe high-volume tables to hypertables and adds compression + retention so they stay
small and fast as data accumulates.

Converted (PK widened to include the time column; no inbound FKs, so safe):
  - opportunities   → hypertable on detected_at, PK (id, detected_at)
  - audit_log       → hypertable on created_at,  PK (id, created_at)
  - market_snapshots→ hypertable on time         (no PK to change)

NOT converted:
  - trades — keeps UNIQUE(client_order_id) for order idempotency (Phase 1.5); a
    hypertable would force that into (client_order_id, opened_at) and break global
    uniqueness. Low volume anyway, so little is lost.
  - ohlcv_bars — plain table with a composite PK; kept as-is (backtest history).

Policies (all if_not_exists → re-runnable):
  - COMPRESSION (non-destructive, chunks stay queryable, ~10-20x smaller): market
    snapshots after 2d (append-only, highest volume), audit_log + opportunities after
    7d (effectively append-only).
  - RETENTION (drops old chunks) ONLY on market_snapshots (90d) — raw, regenerable
    from the feeds. opportunities + audit_log are kept FOREVER (analytics + compliance).

See docs/scale.md to tune any window.
"""

from typing import Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels = None
depends_on = None

# (table, time_col, pk_after | None, compress_segmentby, compress_after, retention_after | None)
_HYPERTABLES = [
    ("opportunities",    "detected_at", "id, detected_at", "strategy_type", "7 days",  None),
    ("audit_log",        "created_at",  "id, created_at",  "event_type",    "7 days",  None),
    ("market_snapshots", "time",        None,              "venue, symbol", "2 days",  "90 days"),
]


def upgrade() -> None:
    for table, timecol, pk_after, segmentby, compress_after, retention_after in _HYPERTABLES:
        # The partitioning column must be part of the PK before create_hypertable.
        if pk_after:
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_pkey;")
            op.execute(f"ALTER TABLE {table} ADD PRIMARY KEY ({pk_after});")
        op.execute(
            f"SELECT create_hypertable('{table}', '{timecol}', if_not_exists => TRUE, migrate_data => TRUE);"
        )
        op.execute(
            f"ALTER TABLE {table} SET ("
            f"  timescaledb.compress,"
            f"  timescaledb.compress_segmentby = '{segmentby}'"
            f");"
        )
        op.execute(
            f"SELECT add_compression_policy('{table}', INTERVAL '{compress_after}', if_not_exists => TRUE);"
        )
        if retention_after:
            op.execute(
                f"SELECT add_retention_policy('{table}', INTERVAL '{retention_after}', if_not_exists => TRUE);"
            )


def downgrade() -> None:
    # Remove the background jobs + compression setting. The table stays a hypertable
    # (TimescaleDB has no in-place revert), which is harmless; the PK widening is
    # reverted so the schema matches 004's shape.
    for table, _timecol, pk_after, _segmentby, _compress_after, retention_after in reversed(_HYPERTABLES):
        if retention_after:
            op.execute(f"SELECT remove_retention_policy('{table}', if_exists => TRUE);")
        op.execute(f"SELECT remove_compression_policy('{table}', if_exists => TRUE);")
        op.execute(f"ALTER TABLE {table} SET (timescaledb.compress = false);")
        if pk_after:
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_pkey;")
            op.execute(f"ALTER TABLE {table} ADD PRIMARY KEY (id);")
