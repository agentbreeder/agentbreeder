"""Add providers table for LLM provider configuration

Revision ID: 003
Revises: 002
Create Date: 2026-03-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column(
            "provider_type",
            sa.Enum(
                "openai", "anthropic", "google", "ollama", "litellm", "openrouter",
                name="providertype",
            ),
            nullable=False,
        ),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "disabled", "error", name="providerstatus"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("last_verified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("model_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("config", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_providers_provider_type", "providers", ["provider_type"])


def downgrade() -> None:
    op.drop_index("ix_providers_provider_type")
    op.drop_table("providers")
    op.execute("DROP TYPE IF EXISTS providertype")
    op.execute("DROP TYPE IF EXISTS providerstatus")
