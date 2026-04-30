"""Add ``incidents`` table for persistent incident storage (#207).

Replaces the in-process ``_incidents`` dict + ``_SEED_INCIDENTS`` seed list
in ``api.services.agentops_service`` with a real PostgreSQL-backed table.
Created incidents now survive API restarts and are shared across replicas.

Schema:
  - ``id`` (uuid, pk)
  - ``title`` (varchar(200))
  - ``severity`` (enum: critical | high | medium | low)
  - ``status`` (enum: open | investigating | mitigated | resolved)
  - ``affected_agent_id`` (uuid, fk → agents.id, nullable, ON DELETE SET NULL)
  - ``description`` (text)
  - ``created_by`` (varchar(255), nullable)
  - ``created_at`` (timestamptz)
  - ``resolved_at`` (timestamptz, nullable)
  - ``timeline`` (jsonb, default ``[]``)
  - ``metadata`` (jsonb, default ``{}``)

This migration deliberately does NOT seed any rows. The previous
``_SEED_INCIDENTS`` list (3 fake demo incidents) is dropped from the service
code in the same change so a fresh deploy starts with an empty table.

Revision ID: 020
Revises: 019
Create Date: 2026-04-29
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "020"
down_revision: str = "019"
branch_labels: str | None = None
depends_on: str | None = None


# ``create_type=False`` keeps SQLAlchemy from re-issuing CREATE TYPE
# inside ``create_table`` — we create the enums explicitly in
# ``upgrade()`` with ``checkfirst=True`` so the migration is idempotent
# against partially-applied state.
_SEVERITY_ENUM = sa.Enum(
    "critical",
    "high",
    "medium",
    "low",
    name="incidentseverity",
    create_type=False,
)
_STATUS_ENUM = sa.Enum(
    "open",
    "investigating",
    "mitigated",
    "resolved",
    name="incidentstatus",
    create_type=False,
)


def upgrade() -> None:
    # PostgreSQL CREATE TYPE has no IF NOT EXISTS; SQLAlchemy's
    # ``checkfirst=True`` is unreliable in async-bound alembic contexts
    # (the existence query runs in a separate scope). Use a DO block
    # that swallows the duplicate_object SQLSTATE so the migration is
    # idempotent against persisted DB volumes (e.g. CI's docker compose
    # stack reusing a postgres volume across runs).
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE incidentseverity AS ENUM ('critical', 'high', 'medium', 'low');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE incidentstatus AS ENUM ('open', 'investigating', 'mitigated', 'resolved');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )

    op.create_table(
        "incidents",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("severity", _SEVERITY_ENUM, nullable=False, server_default="medium"),
        sa.Column("status", _STATUS_ENUM, nullable=False, server_default="open"),
        sa.Column(
            "affected_agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeline", JSONB, nullable=False, server_default="[]"),
        sa.Column("incident_metadata", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_incidents_status", "incidents", ["status"])
    op.create_index("ix_incidents_severity", "incidents", ["severity"])
    op.create_index("ix_incidents_created_at", "incidents", ["created_at"])
    op.create_index("ix_incidents_affected_agent_id", "incidents", ["affected_agent_id"])


def downgrade() -> None:
    op.drop_index("ix_incidents_affected_agent_id", table_name="incidents")
    op.drop_index("ix_incidents_created_at", table_name="incidents")
    op.drop_index("ix_incidents_severity", table_name="incidents")
    op.drop_index("ix_incidents_status", table_name="incidents")
    op.drop_table("incidents")

    bind = op.get_bind()
    _STATUS_ENUM.drop(bind, checkfirst=True)
    _SEVERITY_ENUM.drop(bind, checkfirst=True)
