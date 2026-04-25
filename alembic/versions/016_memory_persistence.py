"""Add memory_configs and memory_messages tables for persistent memory storage.

Revision ID: 016
Revises: 015
Create Date: 2026-04-25

Replaces the in-process class-level dict storage in MemoryService with proper
database-backed tables. Data now survives API restarts and is shared across
replicas.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "016"
down_revision: str = "015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "memory_configs",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("team", sa.String(100), nullable=False),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("memory_type", sa.String(50), nullable=False, server_default="buffer_window"),
        sa.Column("backend", sa.String(50), nullable=False, server_default="postgresql"),
        sa.Column("scope", sa.String(50), nullable=False, server_default="agent"),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("tags", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_memory_configs_team", "memory_configs", ["team"])
    op.create_index("ix_memory_configs_name", "memory_configs", ["name"])

    op.create_table(
        "memory_messages",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "config_id",
            UUID(as_uuid=True),
            sa.ForeignKey("memory_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", sa.Text, nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_memory_messages_config_session", "memory_messages", ["config_id", "session_id"]
    )
    op.create_index(
        "ix_memory_messages_config_created", "memory_messages", ["config_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("memory_messages")
    op.drop_table("memory_configs")
