#!/usr/bin/env bash
# Postgres/TimescaleDB backup — timestamped, gzipped pg_dump with --clean so the
# dump restores idempotently. Run from host cron or Coolify on a schedule.
#
#   bash scripts/backup-postgres.sh
#
# Overridable via env (defaults match podman-compose):
#   POSTGRES_USER / POSTGRES_DB, POSTGRES_CONTAINER, CONTAINER_RUNTIME, BACKUP_RETENTION
set -euo pipefail
cd "$(dirname "$0")/.."

PGUSER="${POSTGRES_USER:-mezna}"
PGDB="${POSTGRES_DB:-mezna_trading}"
CONTAINER="${POSTGRES_CONTAINER:-mezna-postgres}"
RUNTIME="${CONTAINER_RUNTIME:-podman}"
RETENTION="${BACKUP_RETENTION:-14}"

DIR="backups/postgres"
mkdir -p "$DIR"
TS="$(date -u +%Y%m%d-%H%M%S)"
OUT="$DIR/${PGDB}-${TS}.sql.gz"

echo "→ pg_dump $PGDB from $CONTAINER"
"$RUNTIME" exec "$CONTAINER" pg_dump -U "$PGUSER" -d "$PGDB" --clean --if-exists | gzip > "$OUT"
echo "✓ wrote $OUT ($(du -h "$OUT" | cut -f1))"

# Retention — keep the newest $RETENTION dumps.
ls -1t "$DIR"/*.sql.gz 2>/dev/null | tail -n +"$((RETENTION + 1))" | xargs -r rm -f
echo "✓ retention: kept newest $RETENTION dump(s)"
