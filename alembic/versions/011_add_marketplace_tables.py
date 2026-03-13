"""Add marketplace tables (templates, listings, reviews).

Revision ID: 011
Revises: 010
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011"
down_revision: str = "010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # --- Templates ---
    op.create_table(
        "templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("version", sa.String(20), nullable=False, server_default="1.0.0"),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column(
            "category",
            sa.Enum(
                "customer_support",
                "data_analysis",
                "code_review",
                "research",
                "automation",
                "content",
                "other",
                name="templatecategory",
            ),
            nullable=False,
            server_default="other",
        ),
        sa.Column("framework", sa.String(50), nullable=False),
        sa.Column("config_template", sa.JSON, nullable=False),
        sa.Column("parameters", sa.JSON, server_default="[]"),
        sa.Column("tags", sa.JSON, server_default="[]"),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("team", sa.String(100), nullable=False, server_default="default"),
        sa.Column(
            "status",
            sa.Enum("draft", "published", "deprecated", name="templatestatus"),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("use_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("readme", sa.Text, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_templates_name", "templates", ["name"])
    op.create_index("ix_templates_category", "templates", ["category"])
    op.create_index("ix_templates_framework", "templates", ["framework"])
    op.create_index("ix_templates_team_status", "templates", ["team", "status"])

    # --- Marketplace Listings ---
    op.create_table(
        "marketplace_listings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "template_id",
            UUID(as_uuid=True),
            sa.ForeignKey("templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "rejected", "unlisted", name="listingstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("submitted_by", sa.String(255), nullable=False),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("reject_reason", sa.Text, nullable=True),
        sa.Column("featured", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("avg_rating", sa.Float, nullable=False, server_default="0"),
        sa.Column("review_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("install_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_listings_template_id", "marketplace_listings", ["template_id"])
    op.create_index("ix_listings_status", "marketplace_listings", ["status"])
    op.create_index("ix_listings_avg_rating", "marketplace_listings", ["avg_rating"])

    # --- Listing Reviews ---
    op.create_table(
        "listing_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "listing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reviewer", sa.String(255), nullable=False),
        sa.Column("rating", sa.Integer, nullable=False),
        sa.Column("comment", sa.Text, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_listing_reviews_listing_id", "listing_reviews", ["listing_id"])
    op.create_index("ix_listing_reviews_reviewer", "listing_reviews", ["reviewer"])


def downgrade() -> None:
    op.drop_table("listing_reviews")
    op.drop_table("marketplace_listings")
    op.drop_table("templates")
    op.execute("DROP TYPE IF EXISTS templatecategory")
    op.execute("DROP TYPE IF EXISTS templatestatus")
    op.execute("DROP TYPE IF EXISTS listingstatus")
