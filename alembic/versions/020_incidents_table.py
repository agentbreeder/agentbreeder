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
  - ``incident_metadata`` (jsonb, default ``{}``)

This migration deliberately does NOT seed any rows. The previous
``_SEED_INCIDENTS`` list (3 fake demo incidents) is dropped from the service
code in the same change so a fresh deploy starts with an empty table.

This file uses **raw SQL** with ``IF NOT EXISTS`` semantics rather than
SQLAlchemy's schema constructs because:
  - PostgreSQL has no ``CREATE TYPE IF NOT EXISTS`` for enums
  - SQLAlchemy's ``checkfirst=True`` is unreliable in async-bound alembic
  - PL/pgSQL ``DO $$ … EXCEPTION`` blocks don't round-trip through asyncpg's
    prepared-statement protocol (the inner ``CREATE TYPE`` bubbles up as
    ``DuplicateObjectError`` even though EXCEPTION should swallow it)
  - Even with ``create_type=False`` on the SQLAlchemy ``Enum`` instances,
    ``op.create_table`` still appears to emit a ``CREATE TYPE`` statement
    in some SQLAlchemy 2.x async paths

Raw SQL with a ``pg_type`` pre-check + ``CREATE TABLE IF NOT EXISTS`` +
``CREATE INDEX IF NOT EXISTS`` is the bullet-proof idempotent idiom and
avoids every one of the above traps.

Revision ID: 020
Revises: 019
Create Date: 2026-04-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "020"
down_revision: str = "019"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()

    has_severity = bind.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = 'incidentseverity'")
    ).first()
    if not has_severity:
        op.execute("CREATE TYPE incidentseverity AS ENUM ('critical', 'high', 'medium', 'low')")

    has_status = bind.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = 'incidentstatus'")
    ).first()
    if not has_status:
        op.execute(
            "CREATE TYPE incidentstatus AS ENUM ('open', 'investigating', 'mitigated', 'resolved')"
        )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS incidents (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title               VARCHAR(200) NOT NULL,
            severity            incidentseverity NOT NULL DEFAULT 'medium',
            status              incidentstatus   NOT NULL DEFAULT 'open',
            affected_agent_id   UUID REFERENCES agents(id) ON DELETE SET NULL,
            description         TEXT NOT NULL DEFAULT '',
            created_by          VARCHAR(255),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at         TIMESTAMPTZ,
            timeline            JSONB NOT NULL DEFAULT '[]'::jsonb,
            incident_metadata   JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_incidents_status ON incidents (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_incidents_severity ON incidents (severity)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_incidents_created_at ON incidents (created_at)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_incidents_affected_agent_id ON incidents (affected_agent_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_incidents_affected_agent_id")
    op.execute("DROP INDEX IF EXISTS ix_incidents_created_at")
    op.execute("DROP INDEX IF EXISTS ix_incidents_severity")
    op.execute("DROP INDEX IF EXISTS ix_incidents_status")
    op.execute("DROP TABLE IF EXISTS incidents CASCADE")
    op.execute("DROP TYPE IF EXISTS incidentstatus")
    op.execute("DROP TYPE IF EXISTS incidentseverity")
