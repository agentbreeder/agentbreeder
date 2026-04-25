"""Memory Phase 2: memory_entities table + embedding column.

Revision ID: 017
Revises: 016
Create Date: 2026-04-25

Adds:
  - embedding FLOAT[] column to memory_messages (nullable — only semantic type populates it)
  - memory_entities table for entity-type memory configs
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from alembic import op

revision: str = "017"
down_revision: str = "016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Add embedding column to memory_messages (nullable — not all rows have embeddings)
    op.add_column(
        "memory_messages",
        sa.Column("embedding", ARRAY(sa.Float), nullable=True),
    )

    # New memory_entities table
    op.create_table(
        "memory_entities",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "config_id",
            UUID(as_uuid=True),
            sa.ForeignKey("memory_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String, nullable=False),  # person, product, decision, date
        sa.Column("name", sa.String, nullable=False),
        sa.Column("attributes", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_memory_entities_config_id", "memory_entities", ["config_id"])
    op.create_index(
        "ix_memory_entities_entity_type", "memory_entities", ["config_id", "entity_type"]
    )


def downgrade() -> None:
    op.drop_table("memory_entities")
    op.drop_column("memory_messages", "embedding")
