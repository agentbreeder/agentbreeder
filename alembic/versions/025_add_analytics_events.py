"""Add analytics_events table (W4 funnel).

Revision ID: 025
Revises: 024
Create Date: 2026-06-14

Structural product-analytics events backing the Studio Builder funnel view.
PII-free by design: only event name, engine, team, session_id, and structural
props are stored (cloud-security §11.2). A retention/TTL job prunes old rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analytics_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("engine", sa.String(20), nullable=True),
        sa.Column("team", sa.String(100), nullable=True),
        sa.Column("session_id", UUID(as_uuid=True), nullable=True),
        sa.Column("props", sa.JSON(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_analytics_events_event", "analytics_events", ["event"])
    op.create_index("ix_analytics_events_team", "analytics_events", ["team"])
    op.create_index("ix_analytics_events_created_at", "analytics_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_analytics_events_created_at", table_name="analytics_events")
    op.drop_index("ix_analytics_events_team", table_name="analytics_events")
    op.drop_index("ix_analytics_events_event", table_name="analytics_events")
    op.drop_table("analytics_events")
