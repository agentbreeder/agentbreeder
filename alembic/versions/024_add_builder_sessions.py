"""Add ``builder_sessions`` table (Wave 3 conversational builder).

Revision ID: 024
Revises: 023
Create Date: 2026-06-14

Server-side resumable BuilderSession resource backing the Studio conversational
agent builder's eject-to-code flow. state JSON holds conversation history, the
evolving agent.yaml, generated files, and the deploy job handle. Tenant-scoped
by team.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "builder_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("team", sa.String(100), nullable=False),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("engine", sa.String(20), nullable=False, server_default="claude"),
        sa.Column("state", sa.JSON(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_builder_sessions_team", "builder_sessions", ["team"])


def downgrade() -> None:
    op.drop_index("ix_builder_sessions_team", table_name="builder_sessions")
    op.drop_table("builder_sessions")
