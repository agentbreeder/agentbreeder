"""Add enhanced fields to models table

Revision ID: 003
Revises: 002
Create Date: 2026-03-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("models", sa.Column("context_window", sa.Integer(), nullable=True))
    op.add_column("models", sa.Column("max_output_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "models", sa.Column("input_price_per_million", sa.Float(), nullable=True)
    )
    op.add_column(
        "models", sa.Column("output_price_per_million", sa.Float(), nullable=True)
    )
    op.add_column("models", sa.Column("capabilities", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("models", "capabilities")
    op.drop_column("models", "output_price_per_million")
    op.drop_column("models", "input_price_per_million")
    op.drop_column("models", "max_output_tokens")
    op.drop_column("models", "context_window")
