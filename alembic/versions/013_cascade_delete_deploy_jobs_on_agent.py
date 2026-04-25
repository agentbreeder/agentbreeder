"""Cascade delete deploy_jobs when parent agent is deleted.

Revision ID: 013
Revises: 012
Create Date: 2026-04-24

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013"
down_revision: str = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the existing FK constraint (no CASCADE), then re-add with ON DELETE CASCADE.
    with op.batch_alter_table("deploy_jobs") as batch_op:
        batch_op.drop_constraint("deploy_jobs_agent_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "deploy_jobs_agent_id_fkey",
            "agents",
            ["agent_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    # Revert to FK without CASCADE.
    with op.batch_alter_table("deploy_jobs") as batch_op:
        batch_op.drop_constraint("deploy_jobs_agent_id_fkey", type_="foreignkey")
        batch_op.create_foreign_key(
            "deploy_jobs_agent_id_fkey",
            "agents",
            ["agent_id"],
            ["id"],
        )
