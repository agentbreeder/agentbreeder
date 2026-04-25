"""Add RBAC asset ACL, approval queue, service principals, and principal groups.

Revision ID: 015
Revises: 014
Create Date: 2026-04-24

Phase 2: resource_permissions, asset_approval_requests
Phase 3: service_principals, principal_groups
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "015"
down_revision: str = "014"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # Phase 2: resource_permissions
    # -----------------------------------------------------------------------
    op.create_table(
        "resource_permissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", UUID(as_uuid=True), nullable=False),
        sa.Column("principal_type", sa.String(20), nullable=False),
        sa.Column("principal_id", sa.String(255), nullable=False),
        sa.Column("actions", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_resource_permissions_resource",
        "resource_permissions",
        ["resource_type", "resource_id"],
    )
    op.create_index(
        "ix_resource_permissions_principal",
        "resource_permissions",
        ["principal_type", "principal_id"],
    )

    # -----------------------------------------------------------------------
    # Phase 2: asset_approval_requests
    # -----------------------------------------------------------------------
    op.create_table(
        "asset_approval_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_type", sa.String(50), nullable=False),
        sa.Column("asset_id", UUID(as_uuid=True), nullable=False),
        sa.Column("asset_version", sa.String(50), nullable=True),
        sa.Column("submitter_id", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("approver_id", sa.String(255), nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_asset_approval_requests_status",
        "asset_approval_requests",
        ["status"],
    )
    op.create_index(
        "ix_asset_approval_requests_submitter",
        "asset_approval_requests",
        ["submitter_id"],
    )
    op.create_index(
        "ix_asset_approval_requests_asset",
        "asset_approval_requests",
        ["asset_type", "asset_id"],
    )

    # -----------------------------------------------------------------------
    # Phase 3: service_principals
    # -----------------------------------------------------------------------
    op.create_table(
        "service_principals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("team_id", sa.String(100), nullable=False),
        sa.Column(
            "role",
            sa.String(50),
            nullable=False,
            server_default="viewer",
        ),
        sa.Column("allowed_assets", sa.JSON, nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_service_principals_name", "service_principals", ["name"])
    op.create_index("ix_service_principals_team", "service_principals", ["team_id"])

    # -----------------------------------------------------------------------
    # Phase 3: principal_groups
    # -----------------------------------------------------------------------
    op.create_table(
        "principal_groups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("team_id", sa.String(100), nullable=False),
        sa.Column("member_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_principal_groups_team", "principal_groups", ["team_id"])
    op.create_index(
        "ix_principal_groups_team_name",
        "principal_groups",
        ["team_id", "name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_principal_groups_team_name", "principal_groups")
    op.drop_index("ix_principal_groups_team", "principal_groups")
    op.drop_table("principal_groups")

    op.drop_index("ix_service_principals_team", "service_principals")
    op.drop_index("ix_service_principals_name", "service_principals")
    op.drop_table("service_principals")

    op.drop_index("ix_asset_approval_requests_asset", "asset_approval_requests")
    op.drop_index("ix_asset_approval_requests_submitter", "asset_approval_requests")
    op.drop_index("ix_asset_approval_requests_status", "asset_approval_requests")
    op.drop_table("asset_approval_requests")

    op.drop_index("ix_resource_permissions_principal", "resource_permissions")
    op.drop_index("ix_resource_permissions_resource", "resource_permissions")
    op.drop_table("resource_permissions")
