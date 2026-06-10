#!/usr/bin/env bash
# Redis backup — trigger a point-in-time RDB snapshot and copy it out. Redis runs
# with AOF (the durable source of truth); this RDB is a portable restore artifact
# for risk:halt / risk:state and the rest.
#
#   bash scripts/backup-redis.sh
set -euo pipefail
cd "$(dirname "$0")/.."

CONTAINER="${REDIS_CONTAINER:-mezna-redis}"
RUNTIME="${CONTAINER_RUNTIME:-podman}"
RETENTION="${BACKUP_RETENTION:-14}"

DIR="backups/redis"
mkdir -p "$DIR"
TS="$(date -u +%Y%m%d-%H%M%S)"
OUT="$DIR/dump-${TS}.rdb"

echo "→ BGSAVE on $CONTAINER"
"$RUNTIME" exec "$CONTAINER" redis-cli BGSAVE >/dev/null

# Wait for the background save to finish (up to ~30s).
for _ in $(seq 1 30); do
  inprog="$("$RUNTIME" exec "$CONTAINER" redis-cli INFO persistence | tr -d '\r' \
            | grep -E '^rdb_bgsave_in_progress:' | cut -d: -f2 || echo 0)"
  [ "$inprog" = "0" ] && break
  sleep 1
done

"$RUNTIME" cp "$CONTAINER:/data/dump.rdb" "$OUT"
echo "✓ wrote $OUT ($(du -h "$OUT" | cut -f1))"

ls -1t "$DIR"/*.rdb 2>/dev/null | tail -n +"$((RETENTION + 1))" | xargs -r rm -f
echo "✓ retention: kept newest $RETENTION snapshot(s)"
