"""
Gateway request identity — verified operator behind a control action.

The dashboard proxy verifies the session and forwards both the signed token
(X-Mezna-Token) and convenience headers (X-Mezna-User/Role). This module prefers
the CRYPTOGRAPHICALLY VERIFIED token (HS256 with the shared SESSION_SECRET) +
a Redis revocation check, and only falls back to the (untrusted) headers when no
valid token is present. That closes header-spoofing if the gateway is ever exposed.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from fastapi import HTTPException, Request, status

from mezna_shared.redis_client import RedisKeys
from mezna_shared.session import verify_session_token

from .config import settings

log = structlog.get_logger()


@dataclass
class Identity:
    user: str
    role: str | None
    verified: bool   # True only when derived from a valid signed token


async def _is_revoked(redis, payload: dict) -> bool:
    """True if the token was issued before a global or per-user revocation epoch."""
    iat = payload.get("iat")
    if not isinstance(iat, (int, float)):
        return False
    try:
        all_epoch = await redis.get(RedisKeys.SESSION_REVOKE_ALL)
        if all_epoch and iat < int(all_epoch):
            return True
        user_epoch = await redis.get(RedisKeys.session_revoke_user(str(payload.get("sub", ""))))
        if user_epoch and iat < int(user_epoch):
            return True
    except Exception as exc:
        log.warning("auth.revocation_check_failed", error=str(exc))
        return False  # fail open — a Redis blip must not lock out the operator
    return False


async def resolve_identity(request: Request) -> Identity:
    """Verified identity from the token, else untrusted header fallback."""
    token = request.headers.get("x-mezna-token")
    if token and settings.SESSION_SECRET:
        payload = verify_session_token(token, settings.SESSION_SECRET)
        if payload and not await _is_revoked(request.app.state.redis, payload):
            return Identity(user=str(payload.get("sub") or "unknown"), role=payload.get("role"), verified=True)

    hdr_user = request.headers.get("x-mezna-user")
    return Identity(
        user=hdr_user.strip() if hdr_user and hdr_user.strip() else "dashboard",
        role=request.headers.get("x-mezna-role"),
        verified=False,
    )


def require_verified(identity: Identity) -> None:
    """Reject header-only callers when GATEWAY_REQUIRE_AUTH is on."""
    if settings.GATEWAY_REQUIRE_AUTH and not identity.verified:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="valid session token required")


def require_admin(identity: Identity) -> None:
    """Admin-only actions must present a VERIFIED admin token (no header trust)."""
    if not (identity.verified and identity.role == "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verified admin role required")
