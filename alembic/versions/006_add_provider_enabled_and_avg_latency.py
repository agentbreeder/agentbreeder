"""Add is_enabled and avg_latency_ms columns to providers table

Revision ID: 006
Revises: 005
Create Date: 2026-03-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "providers",
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "providers",
        sa.Column("avg_latency_ms", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("providers", "avg_latency_ms")
    op.drop_column("providers", "is_enabled")
