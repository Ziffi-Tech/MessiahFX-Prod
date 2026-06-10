# Database scale — TimescaleDB compression & retention

As the platform runs, the time-series tables grow fastest. Migration `005`
([migrations/versions/005_timescale_compression_retention.py](../migrations/versions/005_timescale_compression_retention.py))
keeps them small and fast — it's the hypertable conversion that migration 001
explicitly deferred ("once the schema is stable").

## What it does

| Table | Hypertable on | Compress after | Retention | Why |
|---|---|---|---|---|
| `market_snapshots` | `time` | 2 days | **90 days** | raw, high-volume, regenerable from feeds |
| `opportunities` | `detected_at` | 7 days | keep forever | analytics history |
| `audit_log` | `created_at` | 7 days | keep forever | compliance |
| `trades` | — (not converted) | — | keep forever | `UNIQUE(client_order_id)` idempotency must stay global |
| `ohlcv_bars` | — (plain table) | — | keep forever | backtest history |

- **Compression** is non-destructive: chunks stay fully queryable and shrink ~10–20×.
  Windows are set so a chunk is only compressed once it's no longer being written.
- **Retention** drops chunks older than the window — applied **only** to
  `market_snapshots` (raw market data you can re-fetch). Nothing else is ever dropped.
- `trades` is intentionally left plain: a hypertable would force its global
  `client_order_id` uniqueness into `(client_order_id, opened_at)`, weakening order
  idempotency. It's one row per order, so compression buys little.

## Verifying / tuning

```sql
-- policies in force
SELECT hypertable_name, proc_name, config->>'compress_after', config->>'drop_after'
FROM timescaledb_information.jobs WHERE hypertable_name IS NOT NULL;

-- change a window (example): keep raw market data 30d instead of 90d
SELECT remove_retention_policy('market_snapshots');
SELECT add_retention_policy('market_snapshots', INTERVAL '30 days');

-- compression effect, once there's data
SELECT * FROM hypertable_compression_stats('market_snapshots');
```

## Applying

Part of `alembic upgrade head` (the `migrate` service). In dev, the migrations dir is
bind-mounted (see `podman-compose.dev.yml`), so new migrations apply on
`podman compose ... up -d migrate` without rebuilding the image.

## Beyond this (future scale levers)

- Per-venue feed scaling (separate market-data replicas per exchange).
- Horizontal scaling of the stateless services (gateway, journal, backtest) behind the
  gateway; the risk/executor consumers stay single-instance by design (ordering).
- Continuous aggregates for the dashboard's rollups (e.g., daily P&L) instead of
  on-the-fly aggregation, once query volume justifies it.
