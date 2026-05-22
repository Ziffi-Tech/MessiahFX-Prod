"""
Async SQLAlchemy engine and session factory.

Usage:
    from mezna_shared.db import get_engine, get_async_session

    engine = get_engine(settings.DATABASE_URL)

    async with get_async_session(engine) as session:
        result = await session.execute(select(Trade))
"""

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text
from contextlib import asynccontextmanager
from typing import AsyncGenerator

log = structlog.get_logger()

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(
    database_url: str,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_pre_ping: bool = True,
) -> AsyncEngine:
    """
    Return the shared async engine. Creates it on first call.

    pool_pre_ping=True ensures broken connections are recycled transparently,
    which is important for long-running services.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            database_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=pool_pre_ping,
            echo=False,  # Set True temporarily for query debugging only
            pool_recycle=3600,  # Recycle connections every hour
        )
        log.info("db.engine_created", pool_size=pool_size, max_overflow=max_overflow)
    return _engine


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return the shared session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_async_session(
    engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager that yields an AsyncSession with automatic commit/rollback.

    Usage:
        async with get_async_session(engine) as session:
            session.add(trade)
            # commits automatically on context exit
            # rolls back on exception
    """
    factory = get_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_db_connection(engine: AsyncEngine) -> bool:
    """
    Verify database connectivity. Used in health checks.
    Returns True if connected, False otherwise.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.warning("db.connection_check_failed", error=str(exc))
        return False


async def dispose_engine() -> None:
    """Dispose the engine and close all connections. Call at service shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        log.info("db.engine_disposed")
