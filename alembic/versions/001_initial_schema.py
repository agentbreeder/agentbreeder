"""Initial schema — agents, tools, models, prompts, knowledge_bases, deploy_jobs

Revision ID: 001
Revises:
Create Date: 2026-03-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Agents
    op.create_table(
        "agents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(63), unique=True, nullable=False, index=True),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("team", sa.String(100), nullable=False, index=True),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("framework", sa.String(50), nullable=False),
        sa.Column("model_primary", sa.String(100), nullable=False),
        sa.Column("model_fallback", sa.String(100), nullable=True),
        sa.Column("endpoint_url", sa.String(500), nullable=True),
        sa.Column(
            "status",
            sa.Enum("deploying", "running", "stopped", "failed", name="agentstatus"),
            nullable=False,
            server_default="deploying",
        ),
        sa.Column("tags", sa.JSON, server_default="[]"),
        sa.Column("config_snapshot", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agents_team_status", "agents", ["team", "status"])
    op.create_index("ix_agents_framework", "agents", ["framework"])

    # Deploy jobs
    op.create_table(
        "deploy_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "parsing", "building", "provisioning", "deploying",
                "health_checking", "registering", "completed", "failed",
                name="deployjobstatus",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("target", sa.String(50), nullable=False),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("logs", sa.JSON, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Tools
    op.create_table(
        "tools",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("tool_type", sa.String(50), server_default="mcp_server"),
        sa.Column("schema_definition", sa.JSON, server_default="{}"),
        sa.Column("endpoint", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("source", sa.String(50), server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Models
    op.create_table(
        "models",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("source", sa.String(50), server_default="manual"),
        sa.Column("config", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Prompts
    op.create_table(
        "prompts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, index=True),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("team", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_prompts_name_version", "prompts", ["name", "version"], unique=True)

    # Knowledge bases
    op.create_table(
        "knowledge_bases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("kb_type", sa.String(50), server_default="document"),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("config", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("knowledge_bases")
    op.drop_table("prompts")
    op.drop_table("models")
    op.drop_table("tools")
    op.drop_table("deploy_jobs")
    op.drop_table("agents")
    op.execute("DROP TYPE IF EXISTS agentstatus")
    op.execute("DROP TYPE IF EXISTS deployjobstatus")
