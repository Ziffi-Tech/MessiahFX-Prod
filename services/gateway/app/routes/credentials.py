"""
Credential management endpoints.

Security design:
- SET operations accept plaintext (over HTTPS only in production)
- GET operations NEVER return plaintext or encrypted values — metadata only
- All changes are audit-logged
- Redis reload signal published after every update so services pick up new creds without restart

Endpoints:
  GET  /api/v1/credentials/            — list all credential metadata (no values)
  POST /api/v1/credentials/set         — create or update a credential
  POST /api/v1/credentials/delete      — soft-delete (mark inactive)
  GET  /api/v1/credentials/status      — per-service configuration status
  POST /api/v1/credentials/reload      — signal all services to reload credentials
"""

import json
from datetime import datetime, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from mezna_shared.credential_store import CREDENTIAL_RELOAD_CHANNEL
from mezna_shared.db import get_async_session
from mezna_shared.encryption import mask_credential

log = structlog.get_logger()
router = APIRouter()

# Valid service/key combinations — prevent arbitrary key injection
ALLOWED_CREDENTIALS: dict[str, list[str]] = {
    "binance": ["api_key", "secret_key", "testnet"],
    "oanda": ["api_key", "account_id", "environment"],
    "anthropic": ["api_key"],
    "telegram": ["bot_token", "chat_id"],
    "discord": ["webhook_url"],
}


class CredentialSetRequest(BaseModel):
    service_name: str = Field(..., description="binance | oanda | anthropic | telegram | discord")
    credential_key: str = Field(..., description="e.g. api_key, secret_key, account_id")
    value: str = Field(..., min_length=1, description="Plaintext credential value")
    updated_by: str = Field(default="dashboard")


class CredentialDeleteRequest(BaseModel):
    service_name: str
    credential_key: str


def _require_store(request: Request):
    """Raise 503 if credential store is not configured."""
    if request.app.state.credential_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Credential store not available. "
                "Set CREDENTIAL_ENCRYPTION_KEY in your .env file and restart the gateway. "
                "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            ),
        )
    return request.app.state.credential_store


def _validate_credential(service_name: str, credential_key: str) -> None:
    """Reject unknown service/key combinations."""
    allowed_keys = ALLOWED_CREDENTIALS.get(service_name)
    if allowed_keys is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown service '{service_name}'. Allowed: {list(ALLOWED_CREDENTIALS.keys())}",
        )
    if credential_key not in allowed_keys:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown key '{credential_key}' for service '{service_name}'. Allowed: {allowed_keys}",
        )


@router.get("/", summary="List credential metadata — no values returned")
async def list_credentials(request: Request) -> dict:
    """
    Return metadata for all configured credentials.
    Never returns plaintext or encrypted values — only status information.
    """
    store = _require_store(request)
    metadata = await store.list_metadata()

    # Build per-service summary
    by_service: dict[str, dict] = {}
    for item in metadata:
        svc = item["service_name"]
        if svc not in by_service:
            by_service[svc] = {"credentials": [], "fully_configured": False}
        by_service[svc]["credentials"].append({
            "key": item["credential_key"],
            "is_set": item["is_set"],
            "is_active": item["is_active"],
            "source": item["source"],
            "updated_at": item["updated_at"],
            "updated_by": item["updated_by"],
        })

    # Mark which services are fully configured
    configured_keys = {
        (item["service_name"], item["credential_key"])
        for item in metadata
        if item["is_set"] and item["is_active"]
    }
    for svc, required_keys in ALLOWED_CREDENTIALS.items():
        if svc in by_service:
            by_service[svc]["fully_configured"] = all(
                (svc, k) in configured_keys for k in required_keys
                if k not in ("testnet", "environment")  # optional toggles
            )

    return {
        "services": by_service,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/status", summary="Per-service credential configuration status")
async def credential_status(request: Request) -> dict:
    """Quick overview: which services have all required credentials set."""
    store = _require_store(request)
    metadata = await store.list_metadata()

    active_set = {
        (item["service_name"], item["credential_key"])
        for item in metadata
        if item["is_set"] and item["is_active"]
    }

    status_map = {}
    for svc, keys in ALLOWED_CREDENTIALS.items():
        required = [k for k in keys if k not in ("testnet", "environment")]
        configured = [k for k in required if (svc, k) in active_set]
        status_map[svc] = {
            "configured": len(configured) == len(required),
            "missing": [k for k in required if k not in [c for c in configured]],
        }

    return {"services": status_map, "store_active": True}


@router.post("/set", summary="Create or update a credential (write-only)")
async def set_credential(
    request: Request,
    body: CredentialSetRequest,
) -> dict:
    """
    Store a credential encrypted in the database.

    - Plaintext value is accepted, encrypted with Fernet, then discarded from memory.
    - Existing credential for the same service/key is overwritten.
    - A Redis reload signal is published so services pick up the change.
    - Audit record is written.
    - Response never contains the value or any derivative of it.
    """
    store = _require_store(request)
    _validate_credential(body.service_name, body.credential_key)

    await store.set(
        service_name=body.service_name,
        credential_key=body.credential_key,
        plaintext_value=body.value,
        updated_by=body.updated_by,
    )

    updated_at = datetime.now(timezone.utc)

    # Audit log
    async with get_async_session(request.app.state.db_engine) as session:
        await session.execute(
            text("""
                INSERT INTO audit_log (event_type, service, payload, metadata, created_at)
                VALUES ('credential.updated', 'gateway', :payload::jsonb, '{}'::jsonb, :created_at)
            """),
            {
                "payload": json.dumps({
                    "service_name": body.service_name,
                    "credential_key": body.credential_key,
                    "updated_by": body.updated_by,
                    # DO NOT log the value or any masked version
                }),
                "created_at": updated_at,
            },
        )

    # Signal all services to reload credentials
    await request.app.state.redis.publish(
        CREDENTIAL_RELOAD_CHANNEL,
        json.dumps({
            "service_name": body.service_name,
            "credential_key": body.credential_key,
            "updated_at": updated_at.isoformat(),
        }),
    )

    log.info(
        "credential.set_complete",
        service=body.service_name,
        key=body.credential_key,
        updated_by=body.updated_by,
    )

    return {
        "success": True,
        "service_name": body.service_name,
        "credential_key": body.credential_key,
        "updated_at": updated_at.isoformat(),
        "message": f"Credential '{body.credential_key}' for '{body.service_name}' stored successfully.",
    }


@router.post("/delete", summary="Soft-delete a credential (mark inactive)")
async def delete_credential(
    request: Request,
    body: CredentialDeleteRequest,
) -> dict:
    """Mark a credential as inactive. Does not physically delete the row (audit trail preserved)."""
    store = _require_store(request)
    _validate_credential(body.service_name, body.credential_key)

    await store.delete(body.service_name, body.credential_key)

    deleted_at = datetime.now(timezone.utc)

    async with get_async_session(request.app.state.db_engine) as session:
        await session.execute(
            text("""
                INSERT INTO audit_log (event_type, service, payload, metadata, created_at)
                VALUES ('credential.deleted', 'gateway', :payload::jsonb, '{}'::jsonb, :created_at)
            """),
            {
                "payload": json.dumps({
                    "service_name": body.service_name,
                    "credential_key": body.credential_key,
                }),
                "created_at": deleted_at,
            },
        )

    await request.app.state.redis.publish(
        CREDENTIAL_RELOAD_CHANNEL,
        json.dumps({
            "action": "deleted",
            "service_name": body.service_name,
            "credential_key": body.credential_key,
        }),
    )

    return {
        "success": True,
        "service_name": body.service_name,
        "credential_key": body.credential_key,
        "deleted_at": deleted_at.isoformat(),
    }


@router.post("/reload", summary="Signal all services to reload credentials from DB")
async def signal_reload(request: Request) -> dict:
    """
    Publish a full-reload signal to all services.
    Use after bulk credential updates or after restoring a backup.
    """
    _require_store(request)

    await request.app.state.redis.publish(
        CREDENTIAL_RELOAD_CHANNEL,
        json.dumps({"action": "full_reload", "triggered_at": datetime.now(timezone.utc).isoformat()}),
    )

    return {
        "signal_published": True,
        "channel": CREDENTIAL_RELOAD_CHANNEL,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
