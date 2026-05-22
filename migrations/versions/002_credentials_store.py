"""Encrypted credential store table.

Revision ID: 002
Revises: 001
Create Date: 2026-05-19

Stores API keys and secrets encrypted at rest using Fernet symmetric encryption.
The encryption key (CREDENTIAL_ENCRYPTION_KEY) is stored only in env vars / Coolify.

Design:
- Each credential is a (service_name, credential_key) pair
- Values are Fernet-encrypted before storage
- Endpoints are write-only: plaintext is never returned by any API
- Credentials are loaded at service startup with env var fallback
- On update, services receive a Redis signal to reload without restart

Services covered: binance, oanda, anthropic, telegram, discord
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credentials",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "service_name",
            sa.String(50),
            nullable=False,
            comment="binance | oanda | anthropic | telegram | discord",
        ),
        sa.Column(
            "credential_key",
            sa.String(100),
            nullable=False,
            comment="api_key | secret_key | account_id | bot_token | chat_id | webhook_url",
        ),
        sa.Column(
            "encrypted_value",
            sa.Text,
            nullable=False,
            comment="Fernet-encrypted credential value. Never store plaintext.",
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default="dashboard",
            comment="dashboard | migration | api",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_by",
            sa.String(100),
            nullable=False,
            server_default="system",
        ),
        sa.UniqueConstraint("service_name", "credential_key", name="uq_credentials_service_key"),
    )
    op.create_index("ix_credentials_service_name", "credentials", ["service_name"])
    op.create_index("ix_credentials_updated_at", "credentials", ["updated_at"])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS credentials CASCADE;")
