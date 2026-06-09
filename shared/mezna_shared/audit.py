"""
Append-only audit log writer.

One shared helper so every service records durable, queryable events to the
audit_log table (immutable by design — see models/audit.py). Fire-and-forget:
failures are logged and swallowed so auditing never interrupts the trading path.

Replaces the per-service ad-hoc INSERTs (gateway control/credentials, risk state)
and gives the executor — which previously wrote nothing — a durable audit trail
for the order-lifecycle events the AuditLog model documents (trade.*, opportunity.*).
"""

import json
from datetime import datetime, timezone

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .db import get_async_session

log = structlog.get_logger()

_INSERT_AUDIT = text("""
    INSERT INTO audit_log (event_type, service, entity_type, entity_id, payload, metadata, created_at)
    VALUES (
        :event_type, :service, :entity_type, CAST(:entity_id AS uuid),
        CAST(:payload AS jsonb), CAST(:metadata AS jsonb), :created_at
    )
""")


async def write_audit_log(
    db_engine: AsyncEngine,
    *,
    event_type: str,
    service: str,
    payload: dict | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    """
    Append one event to audit_log. Never raises — audit must not break execution.

    entity_id may be a UUID string (cast to uuid) or None. A malformed entity_id
    or any DB error is caught and logged, not propagated.
    """
    try:
        async with get_async_session(db_engine) as session:
            await session.execute(_INSERT_AUDIT, {
                "event_type": event_type,
                "service": service,
                "entity_type": entity_type,
                "entity_id": entity_id or None,
                "payload": json.dumps(payload or {}),
                "metadata": json.dumps(metadata or {}),
                "created_at": datetime.now(timezone.utc),
            })
    except Exception as exc:
        log.error("audit.write_failed", event_type=event_type, service=service, error=str(exc))
