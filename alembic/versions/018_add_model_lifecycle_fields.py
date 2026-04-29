"""Add lifecycle columns to ``models`` table — Track G (#163).

Adds:
  - ``discovered_at`` (timestamptz, nullable) — first time the model appeared
    in a discovery sync. NULL for legacy/manual entries.
  - ``last_seen_at`` (timestamptz, nullable) — last successful discovery sync
    that observed this model. Used to derive deprecation / retirement.
  - ``deprecation_replacement_id`` (uuid, nullable, FK ``models.id``) — points
    at the replacement model when an upstream provider deprecates one in
    favour of another. Set manually via ``agentbreeder model deprecate``.
  - ``deprecated_at`` (timestamptz, nullable) — when the lifecycle service
    moved this row to ``status='deprecated'``.

The pre-existing ``status`` column already exists (``String(20)``,
default ``"active"``); this migration does NOT change it. Track G simply
starts using the ``"beta" | "deprecated" | "retired"`` values that the column
already permits.

A partial index on ``(status, last_seen_at)`` keeps the daily lifecycle
sweep query fast on workspaces with hundreds of models per provider.

Revision ID: 018
Revises: 017
Create Date: 2026-04-29
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "018"
down_revision: str = "017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "models",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "models",
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "models",
        sa.Column(
            "deprecation_replacement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("models.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_models_status_last_seen_at",
        "models",
        ["status", "last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_models_status_last_seen_at", table_name="models")
    op.drop_column("models", "deprecation_replacement_id")
    op.drop_column("models", "deprecated_at")
    op.drop_column("models", "last_seen_at")
    op.drop_column("models", "discovered_at")
