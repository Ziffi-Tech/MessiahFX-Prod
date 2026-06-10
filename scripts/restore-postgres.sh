#!/usr/bin/env bash
# Restore Postgres from a gzipped pg_dump produced by backup-postgres.sh.
# DESTRUCTIVE — overwrites the target database. Requires confirmation.
#
#   bash scripts/restore-postgres.sh backups/postgres/mezna_trading-YYYYMMDD-HHMMSS.sql.gz
#   BACKUP_FORCE=1 bash scripts/restore-postgres.sh <file>     # skip the prompt
set -euo pipefail
cd "$(dirname "$0")/.."

FILE="${1:-}"
if [ -z "$FILE" ]; then
  echo "usage: $0 <backup.sql.gz>"
  exit 1
fi
if [ ! -f "$FILE" ]; then
  echo "✗ not found: $FILE"
  exit 1
fi

PGUSER="${POSTGRES_USER:-mezna}"
PGDB="${POSTGRES_DB:-mezna_trading}"
CONTAINER="${POSTGRES_CONTAINER:-mezna-postgres}"
RUNTIME="${CONTAINER_RUNTIME:-podman}"

echo "!! This OVERWRITES database '$PGDB' in '$CONTAINER' from:"
echo "   $FILE"
if [ "${BACKUP_FORCE:-0}" != "1" ]; then
  read -r -p "Type 'restore' to continue: " confirm
  [ "$confirm" = "restore" ] || { echo "aborted"; exit 1; }
fi

echo "→ stopping writers is recommended (executor/journal) before a live restore"
gunzip -c "$FILE" | "$RUNTIME" exec -i "$CONTAINER" psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1
echo "✓ restored $PGDB from $FILE"
