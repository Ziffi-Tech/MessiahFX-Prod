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

## Horizontal scaling (all delivered)

### Per-venue feed sharding — `FEED_VENUES`

Run N market-data replicas, each with a disjoint venue allowlist:

```yaml
market-data-crypto:   # binance + bybit + okx + kraken
  environment: { FEED_VENUES: "binance,bybit,okx,kraken" }
market-data-fx:       # oanda only
  environment: { FEED_VENUES: "oanda" }
```

Feeds, the bar writer, the order-book feed, `/health/feeds`, and the feed-health
metrics all respect the filter — a replica neither runs nor alerts on venues
outside its shard. Empty `FEED_VENUES` (the default) = run everything.

### Service scaling matrix

| Service | Scale horizontally? | Why |
|---|---|---|
| gateway, journal, backtest, rag, notifications | **Yes** | Stateless per request (notifications pops a shared list — disjoint by nature) |
| ai-filter | **Yes** | Scoring is stateless per message; consumer name is hostname-unique so the group splits the stream across replicas |
| market-data | **Shard by venue** | One WebSocket per venue — use `FEED_VENUES`, not naive replicas (duplicate feeds would double-write ticks) |
| risk | **No — single instance** | Risk checks are serialised against shared state (position counts); parallel checks could approve past the limits |
| executor | **No — single instance** | Order submission is intentionally serialised (two legs must not race; idempotency assumes one submitter) |

### Continuous aggregates (dashboard rollups)

Migration `006` adds `opportunities_funnel_daily` — a TimescaleDB continuous
aggregate (per day × strategy: detected / ai_scored / risk_approved / executed /
risk_rejected / expired), refreshed hourly with real-time aggregation for the
unmaterialized tail. `GET /journal/opportunities/funnel/daily?days=N` serves it
(falls back to direct aggregation when 006 isn't applied; the response's
`source` field says which path served it). Rollup cost stays flat as history
grows. The same pattern is ready for `ohlcv_bars` (hourly/daily candles) when
needed — `trades` stays a plain table by design (idempotency), so P&L rollups
remain query-time.
