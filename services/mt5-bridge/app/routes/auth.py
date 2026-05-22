"""
Simple bearer-token authentication for the MT5 bridge.

The BRIDGE_API_KEY must be set in production.
If it is empty, requests pass through with a warning (dev mode only).
"""

import structlog
from fastapi import Header, HTTPException, status

from ..config import settings

log = structlog.get_logger()


async def require_api_key(x_api_key: str = Header(default="")) -> None:
    """
    FastAPI dependency — validates the X-Api-Key header.

    In production: BRIDGE_API_KEY must be set and must match.
    In dev (empty key): all requests pass through with a logged warning.
    """
    if not settings.BRIDGE_API_KEY:
        log.warning(
            "mt5_bridge.auth_disabled",
            hint="Set BRIDGE_API_KEY in .env to enable authentication",
        )
        return  # Dev mode — no auth

    if x_api_key != settings.BRIDGE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Api-Key header",
        )
