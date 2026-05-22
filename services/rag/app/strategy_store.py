"""
Strategy profile store — Redis-backed persistence for extracted StrategyProfiles.

Each profile is stored as a JSON string under key:
    rag:strategy:<source_id>

An index set tracks all known source_ids:
    rag:strategy:index  →  Redis Set of source_id strings

Profiles have no TTL — they persist until explicitly deleted or the book
is re-ingested (upsert overwrites the existing key).

All functions return safely on Redis connection errors — callers receive
None / [] rather than exceptions so that the ingest endpoint can still
return a 200 even if Redis is temporarily unavailable.
"""

import json
from typing import Any

import structlog
from redis.asyncio import Redis

log = structlog.get_logger()

_KEY_PREFIX = "rag:strategy:"
_INDEX_KEY = "rag:strategy:index"


def _profile_key(source_id: str) -> str:
    return f"{_KEY_PREFIX}{source_id}"


async def save_profile(redis: Redis, profile: dict[str, Any]) -> bool:
    """
    Persist a StrategyProfile to Redis.
    Returns True on success, False on error.
    """
    source_id = profile.get("source_id", "")
    if not source_id:
        log.error("strategy_store.save.missing_source_id")
        return False

    key = _profile_key(source_id)
    try:
        payload = json.dumps(profile, ensure_ascii=False)
        await redis.set(key, payload)
        await redis.sadd(_INDEX_KEY, source_id)
        log.info(
            "strategy_store.saved",
            source_id=source_id,
            strategy_type=profile.get("strategy_type"),
            confidence=profile.get("confidence"),
        )
        return True
    except Exception as exc:
        log.error("strategy_store.save_error", source_id=source_id, error=str(exc)[:100])
        return False


async def get_profile(redis: Redis, source_id: str) -> dict[str, Any] | None:
    """
    Retrieve a StrategyProfile by source_id.
    Returns None if not found or on error.
    """
    key = _profile_key(source_id)
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        log.error("strategy_store.get_error", source_id=source_id, error=str(exc)[:100])
        return None


async def list_profiles(redis: Redis) -> list[dict[str, Any]]:
    """
    Return summary dicts for all stored profiles (not full profiles — just
    the fields needed for listing: source_id, source_title, strategy_name,
    strategy_type, confidence, extracted_at, extraction_complete).

    Profiles with missing keys are included with defaults.
    """
    try:
        source_ids = await redis.smembers(_INDEX_KEY)
    except Exception as exc:
        log.error("strategy_store.list_error", error=str(exc)[:100])
        return []

    summaries: list[dict[str, Any]] = []
    for sid_bytes in source_ids:
        sid = sid_bytes.decode() if isinstance(sid_bytes, bytes) else sid_bytes
        raw = await redis.get(_profile_key(sid))
        if not raw:
            # Index entry exists but profile key is gone — clean up index
            await redis.srem(_INDEX_KEY, sid)
            continue
        try:
            profile = json.loads(raw)
            summaries.append({
                "source_id": profile.get("source_id", sid),
                "source_title": profile.get("source_title", ""),
                "strategy_name": profile.get("strategy_name", ""),
                "strategy_type": profile.get("strategy_type", "other"),
                "confidence": profile.get("confidence", "low"),
                "extracted_at": profile.get("extracted_at", ""),
                "extraction_complete": profile.get("extraction_complete", False),
                "entry_rules_count": len(profile.get("entry_criteria", [])),
                "exit_rules_count": len(profile.get("exit_criteria", [])),
            })
        except Exception as exc:
            log.warning("strategy_store.corrupt_profile", sid=sid, error=str(exc)[:80])

    # Sort by extraction date, newest first
    summaries.sort(key=lambda x: x.get("extracted_at", ""), reverse=True)
    return summaries


async def delete_profile(redis: Redis, source_id: str) -> bool:
    """
    Delete a strategy profile and remove it from the index.
    Returns True if it existed and was deleted, False otherwise.
    """
    key = _profile_key(source_id)
    try:
        deleted = await redis.delete(key)
        await redis.srem(_INDEX_KEY, source_id)
        if deleted:
            log.info("strategy_store.deleted", source_id=source_id)
        return bool(deleted)
    except Exception as exc:
        log.error("strategy_store.delete_error", source_id=source_id, error=str(exc)[:100])
        return False
