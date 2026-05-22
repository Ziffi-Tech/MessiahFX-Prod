"""SQLAlchemy 2.0 declarative base for all MeznaQuantFX models."""

import uuid
from datetime import datetime
from sqlalchemy import TIMESTAMP, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncAttrs


class Base(AsyncAttrs, DeclarativeBase):
    """
    Base class for all ORM models.

    - AsyncAttrs: enables async lazy loading where needed
    - UUID primary keys: no sequential integer IDs exposed externally
    - All timestamps are timezone-aware (UTC)
    """

    type_annotation_map = {
        uuid.UUID: UUID(as_uuid=True),
    }
