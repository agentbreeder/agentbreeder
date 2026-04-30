"""Add ``compliance_scans`` table for persisted SOC 2 / HIPAA scan history (#208).

Replaces the in-memory ``_SEED_COMPLIANCE_CONTROLS`` list in
``api.services.agentops_service`` with a real, executable scanner
(``engine.compliance``) that writes one row per scan.

Schema:
  - ``id`` (uuid, pk)
  - ``ran_at`` (timestamptz, indexed)
  - ``overall_status`` (varchar — kept as a plain string instead of an enum
    so adding new statuses later doesn't require an ALTER TYPE / pg_type
    pre-check round-trip).
  - ``results`` (jsonb) — full per-control payload (id, name, category,
    status, evidence, details, last_checked).
  - ``summary`` (jsonb) — compact roll-up: total/passed/failed/partial/skipped.

Like migration 020, this migration is written in raw SQL with
``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS`` so a
partially-applied state can be re-run safely. There are no enums in this
migration (``overall_status`` is a plain ``VARCHAR(32)``) so the
``pg_type`` pre-check pattern from 020 is not needed here.

This migration deliberately seeds nothing. A fresh deploy starts with an
empty table and the first ``GET /api/v1/agentops/compliance/status`` call
triggers the first real scan.

Revision ID: 021
Revises: 020
Create Date: 2026-04-29
"""

from __future__ import annotations

from alembic import op

revision: str = "021"
down_revision: str = "020"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS compliance_scans (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ran_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            overall_status  VARCHAR(32) NOT NULL,
            results         JSONB NOT NULL DEFAULT '[]'::jsonb,
            summary         JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_compliance_scans_ran_at ON compliance_scans (ran_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_compliance_scans_overall_status "
        "ON compliance_scans (overall_status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_compliance_scans_overall_status")
    op.execute("DROP INDEX IF EXISTS ix_compliance_scans_ran_at")
    op.execute("DROP TABLE IF EXISTS compliance_scans")
