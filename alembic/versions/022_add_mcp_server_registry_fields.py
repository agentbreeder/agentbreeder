"""Add missing registry fields to ``mcp_servers`` (version, team, deploy_config, image_uri).

Revision ID: 022
Revises: 021
Create Date: 2026-05-14

The ``McpServer`` SQLAlchemy model (``api/models/database.py``) declares the
columns ``version``, ``team``, ``deploy_config`` and ``image_uri`` but no
migration ever added them to the database. As a result
``GET /api/v1/mcp-servers`` raises ``UndefinedColumnError: column
mcp_servers.version does not exist`` and the dashboard page
``/mcp-servers`` shows "Failed to load MCP servers: API error 500".

This migration adds the four columns (all nullable to avoid breaking
existing rows) and indexes ``team`` to match the model's ``index=True``
declaration.

Idempotent: uses ``IF NOT EXISTS`` so a partially-applied state can be
re-run safely.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE mcp_servers
            ADD COLUMN IF NOT EXISTS version VARCHAR(20),
            ADD COLUMN IF NOT EXISTS team VARCHAR(100),
            ADD COLUMN IF NOT EXISTS deploy_config JSON,
            ADD COLUMN IF NOT EXISTS image_uri VARCHAR(500);
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mcp_servers_team ON mcp_servers (team);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_mcp_servers_team;")
    op.execute(
        """
        ALTER TABLE mcp_servers
            DROP COLUMN IF EXISTS image_uri,
            DROP COLUMN IF EXISTS deploy_config,
            DROP COLUMN IF EXISTS team,
            DROP COLUMN IF EXISTS version;
        """
    )
