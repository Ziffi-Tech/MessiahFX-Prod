# Backups & restore drill

What's durable, how it's backed up, and how to restore — with a drill to prove it.

## What matters

| Store | Holds | Durability |
|---|---|---|
| Postgres / TimescaleDB | trades, positions, opportunities, audit_log, risk_events, ohlcv_bars | **System of record** — must be backed up |
| Redis | risk:halt, risk:state, strategy toggles, tick cache, streams | AOF on disk; RDB snapshot for portability |

The terminal/services are stateless and rebuilt from images — only these two stores carry state.

## Backups

```bash
bash scripts/backup-postgres.sh    # → backups/postgres/<db>-<ts>.sql.gz  (gzip pg_dump --clean)
bash scripts/backup-redis.sh       # → backups/redis/dump-<ts>.rdb        (BGSAVE + copy)
```

Both keep the newest `BACKUP_RETENTION` (default 14) and override container/runtime
via env (`POSTGRES_CONTAINER`, `REDIS_CONTAINER`, `CONTAINER_RUNTIME`).

**Schedule** (host cron example — daily 02:00 UTC):

```cron
0 2 * * * cd /opt/mezna && bash scripts/backup-postgres.sh >> /var/log/mezna-backup.log 2>&1
5 2 * * * cd /opt/mezna && bash scripts/backup-redis.sh    >> /var/log/mezna-backup.log 2>&1
```

Copy `backups/` off-host (object storage) — a backup on the same disk is not a backup.

## Restore

```bash
bash scripts/restore-postgres.sh backups/postgres/mezna_trading-YYYYMMDD-HHMMSS.sql.gz
# Redis: stop redis, drop in the .rdb as /data/dump.rdb, start redis.
```

Stop the writers (executor, journal) before a live Postgres restore.

## RTO / RPO

- **RPO** ≤ 24h with daily backups (tighten the cron for less). AOF makes Redis
  RPO near-zero on an unclean restart in place.
- **RTO**: minutes — restore the dump, rebuild images, `podman-compose up`.

## Restore drill (run quarterly — an untested backup is not a backup)

1. `bash scripts/backup-postgres.sh` on the live stack.
2. Bring up a throwaway Postgres (or a scratch DB), `POSTGRES_CONTAINER=<scratch>
   bash scripts/restore-postgres.sh <dump>`.
3. Sanity-check row counts: `SELECT count(*) FROM trades;`, latest `audit_log`.
4. Record the wall-clock restore time → that's your real RTO. File anything that
   surprised you.
