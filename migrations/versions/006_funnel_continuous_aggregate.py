"""Continuous aggregate: daily opportunities funnel (Phase 5 — dashboard rollups).

The journal funnel endpoint aggregates the whole `opportunities` hypertable on
every call. Fine today; linear-in-history forever. This materializes the daily
rollup once per hour instead:

  opportunities_funnel_daily — per (day, strategy_type): detected / ai_scored /
  risk_approved / executed / risk_rejected / expired.

Notes:
  - Continuous aggregates cannot be created inside a transaction → autocommit_block.
  - `WITH NO DATA` + a policy with start_offset=NULL backfills the entire history
    on the policy's first run (no manual refresh needed).
  - materialized_only=false → real-time aggregation: the not-yet-materialized tail
    (the last hour) is computed on the fly, so reads are always current.
"""

from typing import Union

from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels = None
depends_on = None

_CAGG = "opportunities_funnel_daily"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"""
            CREATE MATERIALIZED VIEW IF NOT EXISTS {_CAGG}
            WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
            SELECT
                time_bucket('1 day', detected_at)                 AS day,
                strategy_type,
                COUNT(*)                                          AS detected,
                COUNT(*) FILTER (WHERE ai_scored_at IS NOT NULL)  AS ai_scored,
                COUNT(*) FILTER (WHERE risk_approved = true)      AS risk_approved,
                COUNT(*) FILTER (WHERE executed = true)           AS executed,
                COUNT(*) FILTER (WHERE risk_approved = false)     AS risk_rejected,
                COUNT(*) FILTER (WHERE expired = true)            AS expired
            FROM opportunities
            GROUP BY 1, 2
            WITH NO DATA;
        """)
        op.execute(f"""
            SELECT add_continuous_aggregate_policy('{_CAGG}',
                start_offset      => NULL,
                end_offset        => INTERVAL '1 hour',
                schedule_interval => INTERVAL '1 hour',
                if_not_exists     => TRUE);
        """)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {_CAGG} CASCADE;")
