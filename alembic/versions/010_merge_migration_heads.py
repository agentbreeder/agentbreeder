"""merge migration heads

Revision ID: 010
Revises: 008, 009
Create Date: 2026-03-12 20:28:06.359256
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "010"
down_revision: str | None = ("008", "009")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
