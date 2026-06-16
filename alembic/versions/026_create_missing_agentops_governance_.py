"""Create missing AgentOps/governance tables

Revision ID: 026
Revises: 025
Create Date: 2026-06-11 23:29:23.514064
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "026"
down_revision: str | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("resource_name", sa.String(length=255), nullable=False),
        sa.Column("team", sa.String(length=100), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"], unique=False)
    op.create_index("ix_audit_events_actor", "audit_events", ["actor"], unique=False)
    op.create_index(
        op.f("ix_audit_events_created_at"), "audit_events", ["created_at"], unique=False
    )
    op.create_index(
        "ix_audit_events_resource_type", "audit_events", ["resource_type"], unique=False
    )
    op.create_table(
        "budgets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("team", sa.String(length=100), nullable=False),
        sa.Column("monthly_limit_usd", sa.Float(), nullable=False),
        sa.Column("alert_threshold_pct", sa.Float(), nullable=False),
        sa.Column("current_month_spend", sa.Float(), nullable=False),
        sa.Column("is_exceeded", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_budgets_team"), "budgets", ["team"], unique=True)
    op.create_table(
        "cost_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=True),
        sa.Column("agent_id", sa.UUID(), nullable=True),
        sa.Column("agent_name", sa.String(length=100), nullable=False),
        sa.Column("team", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("request_type", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cost_events_agent_created", "cost_events", ["agent_name", "created_at"], unique=False
    )
    op.create_index(op.f("ix_cost_events_agent_name"), "cost_events", ["agent_name"], unique=False)
    op.create_index(op.f("ix_cost_events_created_at"), "cost_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_cost_events_team"), "cost_events", ["team"], unique=False)
    op.create_index(
        "ix_cost_events_team_created", "cost_events", ["team", "created_at"], unique=False
    )
    op.create_index(op.f("ix_cost_events_trace_id"), "cost_events", ["trace_id"], unique=False)
    op.create_table(
        "resource_dependencies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=False),
        sa.Column("target_name", sa.String(length=255), nullable=False),
        sa.Column("dependency_type", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type", "source_id", "target_type", "target_id", name="uq_resource_dep"
        ),
    )
    op.create_index(
        "ix_resource_dep_source",
        "resource_dependencies",
        ["source_type", "source_id"],
        unique=False,
    )
    op.create_index(
        "ix_resource_dep_target",
        "resource_dependencies",
        ["target_type", "target_id"],
        unique=False,
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), server_default="", nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_teams_name"), "teams", ["name"], unique=True)
    op.create_table(
        "a2a_agents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("agent_card", sa.JSON(), nullable=False),
        sa.Column("endpoint_url", sa.String(length=500), nullable=False),
        sa.Column(
            "status",
            sa.Enum("registered", "active", "inactive", "error", name="a2astatus"),
            nullable=False,
        ),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("auth_scheme", sa.String(length=50), nullable=True),
        sa.Column("team", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_a2a_agents_agent_id", "a2a_agents", ["agent_id"], unique=False)
    op.create_index(op.f("ix_a2a_agents_name"), "a2a_agents", ["name"], unique=True)
    op.create_index("ix_a2a_agents_team_status", "a2a_agents", ["team", "status"], unique=False)
    op.create_table(
        "team_api_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("encrypted_key", sa.String(), nullable=False),
        sa.Column("key_hint", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "provider", name="uq_team_provider"),
    )
    op.create_table(
        "team_memberships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_user"),
    )
    op.create_table(
        "traces",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=True),
        sa.Column("agent_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("input_preview", sa.Text(), nullable=True),
        sa.Column("output_preview", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_traces_trace_id"), "traces", ["trace_id"], unique=True)
    op.create_table(
        "spans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("span_id", sa.String(length=128), nullable=False),
        sa.Column("parent_span_id", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("span_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("input_data", sa.JSON(), nullable=True),
        sa.Column("output_data", sa.JSON(), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["traces.trace_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("span_id"),
    )
    op.create_index(op.f("ix_spans_trace_id"), "spans", ["trace_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_spans_trace_id"), table_name="spans")
    op.drop_table("spans")
    op.drop_index(op.f("ix_traces_trace_id"), table_name="traces")
    op.drop_table("traces")
    op.drop_table("team_memberships")
    op.drop_table("team_api_keys")
    op.drop_index("ix_a2a_agents_team_status", table_name="a2a_agents")
    op.drop_index(op.f("ix_a2a_agents_name"), table_name="a2a_agents")
    op.drop_index("ix_a2a_agents_agent_id", table_name="a2a_agents")
    op.drop_table("a2a_agents")
    op.drop_index(op.f("ix_teams_name"), table_name="teams")
    op.drop_table("teams")
    op.drop_index("ix_resource_dep_target", table_name="resource_dependencies")
    op.drop_index("ix_resource_dep_source", table_name="resource_dependencies")
    op.drop_table("resource_dependencies")
    op.drop_index(op.f("ix_cost_events_trace_id"), table_name="cost_events")
    op.drop_index("ix_cost_events_team_created", table_name="cost_events")
    op.drop_index(op.f("ix_cost_events_team"), table_name="cost_events")
    op.drop_index(op.f("ix_cost_events_created_at"), table_name="cost_events")
    op.drop_index(op.f("ix_cost_events_agent_name"), table_name="cost_events")
    op.drop_index("ix_cost_events_agent_created", table_name="cost_events")
    op.drop_table("cost_events")
    op.drop_index(op.f("ix_budgets_team"), table_name="budgets")
    op.drop_table("budgets")
    op.drop_index("ix_audit_events_resource_type", table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_created_at"), table_name="audit_events")
    op.drop_index("ix_audit_events_actor", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_table("audit_events")
    # drop_table does not remove the Postgres enum type created by upgrade()
    sa.Enum(name="a2astatus").drop(op.get_bind(), checkfirst=True)
