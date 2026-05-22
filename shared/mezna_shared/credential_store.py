"""
Credential store — encrypted credential management with env fallback.

Load order (for each credential):
  1. Database (encrypted, managed via dashboard)
  2. Environment variable (fallback for initial setup and migration)

Services call get() on startup and on Redis reload signal.
Services never call set() — only the gateway writes credentials.

Redis signal key: credentials:reload
  Published by gateway when any credential is updated.
  Services subscribe and reload their credential cache.
"""

import os
import structlog
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .db import get_async_session
from .encryption import CredentialEncryption, mask_credential

log = structlog.get_logger()

# Redis channel for credential reload signals
CREDENTIAL_RELOAD_CHANNEL = "credentials:reload"

# Mapping: (service_name, credential_key) → environment variable name
# Used as fallback when no DB credential exists.
ENV_FALLBACK_MAP: dict[tuple[str, str], str] = {
    ("binance", "api_key"):       "BINANCE_API_KEY",
    ("binance", "secret_key"):    "BINANCE_API_SECRET",
    ("binance", "testnet"):       "BINANCE_TESTNET",
    ("oanda", "api_key"):         "OANDA_API_KEY",
    ("oanda", "account_id"):      "OANDA_ACCOUNT_ID",
    ("oanda", "environment"):     "OANDA_ENVIRONMENT",
    ("anthropic", "api_key"):     "ANTHROPIC_API_KEY",
    ("telegram", "bot_token"):    "TELEGRAM_BOT_TOKEN",
    ("telegram", "chat_id"):      "TELEGRAM_CHAT_ID",
    ("discord", "webhook_url"):   "DISCORD_WEBHOOK_URL",
}


class CredentialStore:
    """
    Thread-safe credential store with encrypted DB backing and env fallback.

    Usage:
        store = CredentialStore(engine=app.state.db_engine, encryption_key=settings.CREDENTIAL_ENCRYPTION_KEY)
        api_key = await store.get("binance", "api_key")
        await store.set("binance", "api_key", new_key, updated_by="dashboard")
    """

    def __init__(self, engine: AsyncEngine, encryption_key: str) -> None:
        self._engine = engine
        self._enc = CredentialEncryption(encryption_key)
        # In-memory cache: {(service_name, credential_key): plaintext_value}
        self._cache: dict[tuple[str, str], str] = {}

    async def get(
        self,
        service_name: str,
        credential_key: str,
        fallback: str = "",
    ) -> str:
        """
        Return the credential value. Checks memory cache, then DB, then env var.

        NEVER log the return value of this method.
        """
        cache_key = (service_name, credential_key)

        # 1. Memory cache (populated on load_all or previous get)
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 2. Database
        db_value = await self._get_from_db(service_name, credential_key)
        if db_value is not None:
            self._cache[cache_key] = db_value
            return db_value

        # 3. Environment variable fallback
        env_key = ENV_FALLBACK_MAP.get(cache_key)
        if env_key:
            env_value = os.getenv(env_key, fallback)
            if env_value:
                log.info(
                    "credentials.using_env_fallback",
                    service=service_name,
                    key=credential_key,
                    env_var=env_key,
                )
                self._cache[cache_key] = env_value
                return env_value

        return fallback

    async def set(
        self,
        service_name: str,
        credential_key: str,
        plaintext_value: str,
        updated_by: str = "dashboard",
    ) -> None:
        """
        Encrypt and store a credential. Updates cache immediately.

        Args:
            service_name: e.g. 'binance', 'oanda'
            credential_key: e.g. 'api_key', 'secret_key'
            plaintext_value: The actual credential (will be encrypted before storage)
            updated_by: Who is setting this credential (for audit)
        """
        if not plaintext_value.strip():
            raise ValueError("Credential value cannot be empty")

        encrypted = self._enc.encrypt(plaintext_value)
        now = datetime.now(timezone.utc)

        async with get_async_session(self._engine) as session:
            await session.execute(
                text("""
                    INSERT INTO credentials
                        (service_name, credential_key, encrypted_value, source, updated_by, created_at, updated_at)
                    VALUES
                        (:service_name, :credential_key, :encrypted_value, 'dashboard', :updated_by, :now, :now)
                    ON CONFLICT (service_name, credential_key) DO UPDATE
                    SET
                        encrypted_value = EXCLUDED.encrypted_value,
                        is_active = true,
                        source = 'dashboard',
                        updated_by = EXCLUDED.updated_by,
                        updated_at = EXCLUDED.updated_at
                """),
                {
                    "service_name": service_name,
                    "credential_key": credential_key,
                    "encrypted_value": encrypted,
                    "updated_by": updated_by,
                    "now": now,
                },
            )

        # Update memory cache
        self._cache[(service_name, credential_key)] = plaintext_value

        log.info(
            "credentials.updated",
            service=service_name,
            key=credential_key,
            updated_by=updated_by,
            # Note: masked value only — never log actual credential
        )

    async def delete(self, service_name: str, credential_key: str) -> None:
        """Mark a credential as inactive (soft delete)."""
        async with get_async_session(self._engine) as session:
            await session.execute(
                text("""
                    UPDATE credentials
                    SET is_active = false, updated_at = NOW()
                    WHERE service_name = :service_name AND credential_key = :credential_key
                """),
                {"service_name": service_name, "credential_key": credential_key},
            )
        self._cache.pop((service_name, credential_key), None)
        log.info("credentials.deleted", service=service_name, key=credential_key)

    async def load_all(self) -> None:
        """
        Pre-load all active credentials into memory cache at service startup.
        Call this once in the lifespan startup handler.
        """
        loaded = 0
        async with get_async_session(self._engine) as session:
            result = await session.execute(
                text("""
                    SELECT service_name, credential_key, encrypted_value
                    FROM credentials
                    WHERE is_active = true
                    ORDER BY service_name, credential_key
                """)
            )
            rows = result.fetchall()

        for row in rows:
            try:
                decrypted = self._enc.decrypt(row.encrypted_value)
                self._cache[(row.service_name, row.credential_key)] = decrypted
                loaded += 1
            except Exception as exc:
                log.error(
                    "credentials.load_failed",
                    service=row.service_name,
                    key=row.credential_key,
                    error=str(exc),
                )

        log.info("credentials.loaded", count=loaded)

    def clear_cache(self) -> None:
        """Clear in-memory cache. Services will re-fetch from DB on next get()."""
        self._cache.clear()

    async def list_metadata(self) -> list[dict[str, Any]]:
        """
        Return credential metadata for dashboard display.
        NEVER returns encrypted or plaintext values.
        Returns: service, key, is_active, source, updated_at, updated_by, is_set
        """
        async with get_async_session(self._engine) as session:
            result = await session.execute(
                text("""
                    SELECT
                        service_name,
                        credential_key,
                        is_active,
                        source,
                        updated_at,
                        updated_by
                    FROM credentials
                    ORDER BY service_name, credential_key
                """)
            )
            rows = result.fetchall()

        # Also include env-only credentials (not in DB)
        db_keys = {(r.service_name, r.credential_key) for r in rows}
        metadata = [
            {
                "service_name": r.service_name,
                "credential_key": r.credential_key,
                "is_active": r.is_active,
                "source": r.source,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "updated_by": r.updated_by,
                "is_set": True,
            }
            for r in rows
        ]

        # Add env-only credentials not yet in DB
        for (svc, key), env_var in ENV_FALLBACK_MAP.items():
            if (svc, key) not in db_keys and os.getenv(env_var):
                metadata.append({
                    "service_name": svc,
                    "credential_key": key,
                    "is_active": True,
                    "source": "env",
                    "updated_at": None,
                    "updated_by": "environment",
                    "is_set": True,
                })

        return metadata

    async def _get_from_db(
        self, service_name: str, credential_key: str
    ) -> str | None:
        """Fetch and decrypt a single credential from DB. Returns None if not found."""
        try:
            async with get_async_session(self._engine) as session:
                result = await session.execute(
                    text("""
                        SELECT encrypted_value FROM credentials
                        WHERE service_name = :service_name
                          AND credential_key = :credential_key
                          AND is_active = true
                        LIMIT 1
                    """),
                    {"service_name": service_name, "credential_key": credential_key},
                )
                row = result.fetchone()

            if row is None:
                return None
            return self._enc.decrypt(row.encrypted_value)
        except Exception as exc:
            log.error(
                "credentials.db_fetch_failed",
                service=service_name,
                key=credential_key,
                error=str(exc),
            )
            return None
