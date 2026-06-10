"""
Deterministic client order IDs — the foundation of order idempotency.

The executor previously generated a fresh uuid4 per execution attempt, so a
redelivered message (consumer crash before ACK) produced a NEW client_order_id —
defeating every idempotency layer at once: the DB unique constraint, the
exchange's clientOrderId dedup, and our own replay guard.

A deterministic id derived from (opportunity, leg, side, symbol) makes all three
effective: the same logical leg always maps to the same id, so a replay is a
no-op at the DB (ON CONFLICT) and at clientOrderId-idempotent venues.

Format: "mx-<24 hex>" (27 chars) — within exchange clientOrderId length limits.
"""

from __future__ import annotations

import hashlib
import uuid

_PREFIX = "mx-"


def make_client_order_id(
    opportunity_id: str | None,
    leg_index: int,
    side: str,
    symbol: str,
) -> str:
    """
    Stable client_order_id for one logical leg. Same inputs → same id.

    Falls back to a random (non-idempotent) id only when opportunity_id is
    missing, since there is then nothing stable to key on.
    """
    if not opportunity_id:
        return _PREFIX + uuid.uuid4().hex[:24]
    raw = f"{opportunity_id}:{leg_index}:{side}:{symbol}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return _PREFIX + digest
