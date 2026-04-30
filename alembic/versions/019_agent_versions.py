"""Add ``agent_versions`` table — historical YAML snapshots per agent (#210).

The dashboard's `/agents/:id` Versions + Compare tabs were rendering
``MOCK_VERSIONS`` / ``MOCK_VERSION_YAML`` constants because the registry
only stored the *current* ``config_snapshot`` per agent — there was no
historical record. This migration adds an append-only ``agent_versions``
table, populated by ``AgentRegistry.register`` whenever an agent's
version string changes, so the dashboard can show real diffs across
prior versions.

Schema:
  - ``id`` (uuid, pk)
  - ``agent_id`` (uuid, fk → ``agents.id`` on delete cascade)
  - ``version`` (str(20)) — the value of ``config.version`` at the time of register
  - ``config_snapshot`` (jsonb) — full ``AgentConfig.model_dump(mode="json")``
  - ``config_yaml`` (text) — the ``ruamel.yaml`` rendering of the snapshot,
    so the dashboard can display the original YAML form without
    re-serializing on the client
  - ``created_by`` (str(255), nullable) — actor email at the time of register
  - ``created_at`` (timestamptz, default now())
  - ``UNIQUE (agent_id, version)`` — prevents duplicate rows when the same
    agent is registered multiple times at the same version (we update the
    existing row's snapshot/yaml/created_by instead).

Revision ID: 019
Revises: 018
Create Date: 2026-04-29
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "019"
down_revision: str = "018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "agent_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("config_snapshot", JSONB, nullable=False, server_default="{}"),
        sa.Column("config_yaml", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_id_version"),
    )
    op.create_index(
        "ix_agent_versions_agent_created_at",
        "agent_versions",
        ["agent_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_versions_agent_created_at", table_name="agent_versions")
    op.drop_table("agent_versions")
