"""Add orchestrations table

Revision ID: 009
Revises: 007
Create Date: 2026-03-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "009"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orchestrations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(63), unique=True, nullable=False, index=True),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("team", sa.String(100), nullable=True),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("strategy", sa.String(30), nullable=False),
        sa.Column("agents_config", sa.JSON(), nullable=False),
        sa.Column("shared_state_config", sa.JSON(), nullable=True),
        sa.Column("deploy_config", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("endpoint_url", sa.String(500), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
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
    op.create_index("ix_orchestrations_team_status", "orchestrations", ["team", "status"])
    op.create_index("ix_orchestrations_strategy", "orchestrations", ["strategy"])


def downgrade() -> None:
    op.drop_index("ix_orchestrations_strategy")
    op.drop_index("ix_orchestrations_team_status")
    op.drop_table("orchestrations")
