"""Add mcp_servers table

Revision ID: 007
Revises: 006
Create Date: 2026-03-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("endpoint", sa.String(500), nullable=False),
        sa.Column(
            "transport",
            sa.String(30),
            nullable=False,
            server_default="stdio",
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("tool_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_ping_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("mcp_servers")
