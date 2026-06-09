"""
Session token verification (Python) — mirrors the dashboard's lib/auth.ts.

The terminal signs a minimal HS256 JWS:  base64url(payload).base64url(HMAC-SHA256).
The signature covers the ASCII bytes of the base64url payload string. Verification
is order-independent (we never re-serialise the payload — we check the signature
over the received body, then parse it), so this stays compatible with the JS signer.

This lets the FastAPI gateway VERIFY the operator's token itself instead of
trusting the X-Mezna-User/Role headers the dashboard proxy forwards — defense in
depth if the gateway port is ever exposed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

_VALID_ROLES = ("admin", "operator", "viewer")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def sign_session_token(payload: dict, secret: str) -> str:
    """Sign a payload (HS256). Provided mainly for tests; the gateway only verifies."""
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def verify_session_token(token: str, secret: str) -> dict | None:
    """
    Verify signature + expiry + role. Returns the payload dict, or None if the
    token is malformed, the signature is wrong, it has expired, or the role is
    unrecognised. Constant-time signature comparison.
    """
    if not token or not secret:
        return None
    parts = token.split(".")
    if len(parts) != 2:
        return None
    body_b64, sig_b64 = parts

    try:
        expected = hmac.new(secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).digest()
        actual = _b64url_decode(sig_b64)
    except Exception:
        return None
    if not hmac.compare_digest(expected, actual):
        return None

    try:
        payload = json.loads(_b64url_decode(body_b64).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or exp * 1000 < time.time() * 1000:
        return None
    if payload.get("role") not in _VALID_ROLES:
        return None
    return payload
