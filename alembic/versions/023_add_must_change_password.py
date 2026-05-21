"""Add ``must_change_password`` flag to ``users`` table.

Revision ID: 023
Revises: 022
Create Date: 2026-05-21

Per issue #464 (UX-3): the seeded admin account ships with the publicly
documented credential ``admin@agentbreeder.local`` / ``plant``. Without a
forced rotation, that credential is live on every fresh install — fine for
a 10-minute localhost demo, **not** fine the moment the API gets exposed
via a reverse proxy or a shared dev machine.

This migration adds ``must_change_password BOOLEAN NOT NULL DEFAULT FALSE``
to ``users``. Existing rows on running installs are unaffected (default
FALSE preserves current behavior). The ``_seed_default_admin`` path in
``api/main.py`` sets it to TRUE explicitly so freshly seeded admins are
forced to change the password before they can do anything else.

Idempotent: uses ``ADD COLUMN IF NOT EXISTS`` so a partially-applied
state can be re-run safely.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS must_change_password;")
