"""Tests for mezna_shared.session — HS256 sign/verify (mirrors lib/auth.ts)."""

import time

from mezna_shared.session import sign_session_token, verify_session_token


def _payload(role="admin", exp_offset=3600):
    now = int(time.time())
    return {"sub": "alice", "role": role, "iat": now, "exp": now + exp_offset}


def test_round_trip_valid():
    tok = sign_session_token(_payload(), "secret")
    out = verify_session_token(tok, "secret")
    assert out is not None
    assert out["sub"] == "alice"
    assert out["role"] == "admin"


def test_wrong_secret_rejected():
    tok = sign_session_token(_payload(), "secret")
    assert verify_session_token(tok, "other-secret") is None


def test_tampered_payload_rejected():
    tok = sign_session_token(_payload(), "secret")
    body, sig = tok.split(".")
    # Flip the last char of the body — signature no longer matches.
    forged = body[:-1] + ("A" if body[-1] != "A" else "B") + "." + sig
    assert verify_session_token(forged, "secret") is None


def test_expired_rejected():
    tok = sign_session_token(_payload(exp_offset=-10), "secret")
    assert verify_session_token(tok, "secret") is None


def test_unknown_role_rejected():
    tok = sign_session_token(_payload(role="superuser"), "secret")
    assert verify_session_token(tok, "secret") is None


def test_malformed_rejected():
    assert verify_session_token("", "secret") is None
    assert verify_session_token("only-one-part", "secret") is None
    assert verify_session_token("a.b.c", "secret") is None


def test_empty_secret_rejected():
    tok = sign_session_token(_payload(), "secret")
    assert verify_session_token(tok, "") is None
