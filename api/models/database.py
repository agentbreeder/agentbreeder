"""SQLAlchemy database models for the AgentBreeder registry."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from api.models.enums import (
    A2AStatus,
    AgentStatus,
    BudgetDuration,
    DeployJobStatus,
    EvalRunStatus,
    KeyScopeType,
    ListingStatus,
    OrchestrationStatus,
    ProviderStatus,
    ProviderType,
    TemplateCategory,
    TemplateStatus,
    UserRole,
)


class Base(DeclarativeBase):  # type: ignore[misc]
    pass


class User(Base):
    """A user account for the AgentBreeder platform."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.viewer, nullable=False)
    team: Mapped[str] = mapped_column(String(100), nullable=False, default="default")
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Agent(Base):
    """An AI agent registered in the agentbreeder registry."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(63), unique=True, nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    team: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    framework: Mapped[str] = mapped_column(String(50), nullable=False)
    model_primary: Mapped[str] = mapped_column(String(100), nullable=False)
    model_fallback: Mapped[str | None] = mapped_column(String(100), nullable=True)
    endpoint_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus), default=AgentStatus.deploying, nullable=False
    )
    tags: Mapped[list] = mapped_column(JSON, default=list)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    deploy_jobs: Mapped[list[DeployJob]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_agents_team_status", "team", "status"),
        Index("ix_agents_framework", "framework"),
    )


class DeployJob(Base):
    """A deployment job for an agent."""

    __tablename__ = "deploy_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[DeployJobStatus] = mapped_column(
        Enum(DeployJobStatus), default=DeployJobStatus.pending, nullable=False
    )
    target: Mapped[str] = mapped_column(String(50), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped[Agent] = relationship(back_populates="deploy_jobs")


class Tool(Base):
    """A tool or MCP server in the registry."""

    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tool_type: Mapped[str] = mapped_column(String(50), default="mcp_server")
    schema_definition: Mapped[dict] = mapped_column(JSON, default=dict)
    endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    source: Mapped[str] = mapped_column(String(50), default="manual")
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Model(Base):
    """An LLM model registered in the agentbreeder registry."""

    __tablename__ = "models"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    source: Mapped[str] = mapped_column(String(50), default="manual")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_price_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_price_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    capabilities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Prompt(Base):
    """A versioned prompt template."""

    __tablename__ = "prompts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    team: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    versions: Mapped[list[PromptVersion]] = relationship(
        back_populates="prompt", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_prompts_name_version", "name", "version", unique=True),)


class PromptVersion(Base):
    """A versioned snapshot of a prompt's content."""

    __tablename__ = "prompt_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prompt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    prompt: Mapped[Prompt] = relationship(back_populates="versions")

    __table_args__ = (
        Index("ix_prompt_versions_prompt_id", "prompt_id"),
        Index("ix_prompt_versions_created_at", "created_at"),
        Index("ix_prompt_versions_prompt_id_version", "prompt_id", "version", unique=True),
    )


class KnowledgeBase(Base):
    """A knowledge base / RAG data source."""

    __tablename__ = "knowledge_bases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    kb_type: Mapped[str] = mapped_column(String(50), default="document")
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class McpServer(Base):
    """An MCP server registered in the agentbreeder registry."""

    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    transport: Mapped[str] = mapped_column(String(30), nullable=False, default="stdio")
    status: Mapped[str] = mapped_column(String(20), default="active")
    tool_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_ping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    team: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    deploy_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    image_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)


class Provider(Base):
    """An LLM provider configuration (e.g. OpenAI, Anthropic, Ollama)."""

    __tablename__ = "providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    provider_type: Mapped[ProviderType] = mapped_column(Enum(ProviderType), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[ProviderStatus] = mapped_column(
        Enum(ProviderStatus), default=ProviderStatus.active, nullable=False
    )
    is_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_verified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_providers_provider_type", "provider_type"),)


# ---------------------------------------------------------------------------
# Evaluation Framework (M18)
# ---------------------------------------------------------------------------


class EvalDataset(Base):
    """An evaluation dataset containing test cases for agent evaluation."""

    __tablename__ = "eval_datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    format: Mapped[str] = mapped_column(String(20), nullable=False, default="jsonl")
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    team: Mapped[str] = mapped_column(String(100), nullable=False, default="default")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    rows: Mapped[list[EvalDatasetRow]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class EvalDatasetRow(Base):
    """A single row / test case in an evaluation dataset."""

    __tablename__ = "eval_dataset_rows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False
    )
    input: Mapped[dict] = mapped_column(JSON, nullable=False)
    expected_output: Mapped[str] = mapped_column(Text, nullable=False)
    expected_tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    dataset: Mapped[EvalDataset] = relationship(back_populates="rows")

    __table_args__ = (Index("ix_eval_dataset_rows_dataset_id", "dataset_id"),)


class EvalRun(Base):
    """A single evaluation run against a dataset."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    agent_name: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_datasets.id"), nullable=False
    )
    status: Mapped[EvalRunStatus] = mapped_column(
        Enum(EvalRunStatus), default=EvalRunStatus.pending, nullable=False
    )
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    results: Mapped[list[EvalResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_eval_runs_agent_name", "agent_name"),
        Index("ix_eval_runs_dataset_id", "dataset_id"),
        Index("ix_eval_runs_status", "status"),
    )


class EvalResult(Base):
    """Result of evaluating a single dataset row."""

    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False
    )
    row_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_dataset_rows.id"), nullable=False
    )
    actual_output: Mapped[str] = mapped_column(Text, nullable=False)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped[EvalRun] = relationship(back_populates="results")

    __table_args__ = (
        Index("ix_eval_results_run_id", "run_id"),
        Index("ix_eval_results_row_id", "row_id"),
    )


# ---------------------------------------------------------------------------
# Orchestration (M29)
# ---------------------------------------------------------------------------


class Orchestration(Base):
    """A multi-agent orchestration definition."""

    __tablename__ = "orchestrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(63), unique=True, nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    team: Mapped[str | None] = mapped_column(String(100), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    strategy: Mapped[str] = mapped_column(String(30), nullable=False)
    agents_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    shared_state_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deploy_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[OrchestrationStatus] = mapped_column(
        Enum(OrchestrationStatus), default=OrchestrationStatus.draft, nullable=False
    )
    endpoint_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    config_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_orchestrations_team_status", "team", "status"),
        Index("ix_orchestrations_strategy", "strategy"),
    )


# ---------------------------------------------------------------------------
# A2A Agent Registry (M19)
# ---------------------------------------------------------------------------


class A2AAgent(Base):
    """An A2A-compatible agent registered for inter-agent communication."""

    __tablename__ = "a2a_agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    agent_card: Mapped[dict] = mapped_column(JSON, default=dict)
    endpoint_url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[A2AStatus] = mapped_column(
        Enum(A2AStatus), default=A2AStatus.registered, nullable=False
    )
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    auth_scheme: Mapped[str | None] = mapped_column(String(50), nullable=True)
    team: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_a2a_agents_team_status", "team", "status"),
        Index("ix_a2a_agents_agent_id", "agent_id"),
    )


# ---------------------------------------------------------------------------
# Marketplace (M21 / M22)
# ---------------------------------------------------------------------------


class Template(Base):
    """A parameterized agent configuration template."""

    __tablename__ = "templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[TemplateCategory] = mapped_column(
        Enum(TemplateCategory), default=TemplateCategory.other, nullable=False
    )
    framework: Mapped[str] = mapped_column(String(50), nullable=False)
    config_template: Mapped[dict] = mapped_column(JSON, nullable=False)
    parameters: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    team: Mapped[str] = mapped_column(String(100), nullable=False, default="default")
    status: Mapped[TemplateStatus] = mapped_column(
        Enum(TemplateStatus), default=TemplateStatus.draft, nullable=False
    )
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    readme: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    listings: Mapped[list[MarketplaceListing]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_templates_category", "category"),
        Index("ix_templates_framework", "framework"),
        Index("ix_templates_team_status", "team", "status"),
    )


class MarketplaceListing(Base):
    """A marketplace listing for a published template."""

    __tablename__ = "marketplace_listings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[ListingStatus] = mapped_column(
        Enum(ListingStatus), default=ListingStatus.pending, nullable=False
    )
    submitted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    featured: Mapped[bool] = mapped_column(default=False, nullable=False)
    avg_rating: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    install_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    template: Mapped[Template] = relationship(back_populates="listings")
    reviews: Mapped[list[ListingReview]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_listings_template_id", "template_id"),
        Index("ix_listings_status", "status"),
        Index("ix_listings_avg_rating", "avg_rating"),
    )


class ListingReview(Base):
    """A user review/rating for a marketplace listing."""

    __tablename__ = "listing_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer: Mapped[str] = mapped_column(String(255), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    listing: Mapped[MarketplaceListing] = relationship(back_populates="reviews")

    __table_args__ = (
        Index("ix_listing_reviews_listing_id", "listing_id"),
        Index("ix_listing_reviews_reviewer", "reviewer"),
    )


# ---------------------------------------------------------------------------
# LiteLLM Virtual Key References (M22)
# ---------------------------------------------------------------------------


class LiteLLMKeyRef(Base):
    """Reference to a LiteLLM virtual key stored in the litellm DB.

    AgentBreeder stores the metadata (scope, tags, budget) here so it can
    associate keys with teams, users, agents, and service principals without
    storing the secret value itself.
    """

    __tablename__ = "litellm_key_refs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Human-readable alias shown in the dashboard (e.g. "team-engineering-prod")
    key_alias: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    # First 12 chars of the sk-... value for safe display; never the full key
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)

    # The LiteLLM internal key ID (used to call /key/delete)
    litellm_key_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Who this key is issued to
    scope_type: Mapped[KeyScopeType] = mapped_column(Enum(KeyScopeType), nullable=False)
    scope_id: Mapped[str] = mapped_column(
        String(255), nullable=False
    )  # team name / user id / agent name

    # Optional FK-friendly denormalized fields for filtering
    team_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    agent_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)

    # What models this key can call (null = all models)
    allowed_models: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Spend limits
    max_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_duration: Mapped[BudgetDuration | None] = mapped_column(
        Enum(BudgetDuration), nullable=True
    )
    tpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)  # tokens/minute
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)  # requests/minute

    # Routing / classification tags (e.g. ["production", "rag", "customer-support"])
    tags: Mapped[list] = mapped_column(JSON, default=list)

    # Soft expiry (LiteLLM also enforces this, but stored here for dashboard display)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_litellm_key_refs_scope", "scope_type", "scope_id"),
        Index("ix_litellm_key_refs_team", "team_id"),
        Index("ix_litellm_key_refs_agent", "agent_name"),
    )


# ---------------------------------------------------------------------------
# Phase 2 — Asset ACL + Approval Queue
# ---------------------------------------------------------------------------


class ResourcePermission(Base):
    """Fine-grained ACL entry granting a principal specific actions on a resource."""

    __tablename__ = "resource_permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # What resource this permission applies to
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Who the permission is granted to
    principal_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # user|team|service_principal|group
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # What they can do: ["read", "use", "write", "deploy", "publish", "admin"]
    actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_resource_permissions_resource", "resource_type", "resource_id"),
        Index("ix_resource_permissions_principal", "principal_type", "principal_id"),
    )


class AssetApprovalRequest(Base):
    """Persisted approval queue entry — an asset submitted for admin sign-off."""

    __tablename__ = "asset_approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asset_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    submitter_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending|approved|rejected

    approver_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # admin note on decision
    message: Mapped[str | None] = mapped_column(Text, nullable=True)  # submitter message

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_asset_approval_requests_status", "status"),
        Index("ix_asset_approval_requests_submitter", "submitter_id"),
        Index("ix_asset_approval_requests_asset", "asset_type", "asset_id"),
    )


# ---------------------------------------------------------------------------
# Phase 3 — Service Principals + Principal Groups
# ---------------------------------------------------------------------------


class ServicePrincipal(Base):
    """Non-human identity (CI bot, agent, service) with RBAC role and scoped access."""

    __tablename__ = "service_principals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    team_id: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(
        String(50), nullable=False, default="viewer"
    )  # deployer|contributor|viewer

    # Optional allowlist of resource_type:resource_id pairs
    allowed_assets: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_service_principals_name", "name"),
        Index("ix_service_principals_team", "team_id"),
    )


class MemoryConfig(Base):
    """Persistent memory configuration — replaces the ephemeral in-process dict store."""

    __tablename__ = "memory_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    team: Mapped[str] = mapped_column(String(100), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(50), nullable=False, default="buffer_window")
    backend: Mapped[str] = mapped_column(String(50), nullable=False, default="postgresql")
    scope: Mapped[str] = mapped_column(String(50), nullable=False, default="agent")
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[MemoryMessage]] = relationship(
        "MemoryMessage", back_populates="config", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_memory_configs_team", "team"),
        Index("ix_memory_configs_name", "name"),
    )


class MemoryMessage(Base):
    """A single conversation turn stored for a memory config session."""

    __tablename__ = "memory_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_configs.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)

    config: Mapped[MemoryConfig] = relationship("MemoryConfig", back_populates="messages")

    __table_args__ = (
        Index("ix_memory_messages_config_session", "config_id", "session_id"),
        Index("ix_memory_messages_config_created", "config_id", "created_at"),
    )


class MemoryEntity(Base):
    """A named entity extracted from conversation history for entity-type memory configs."""

    __tablename__ = "memory_entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memory_configs.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_memory_entities_config_id", "config_id"),
        Index("ix_memory_entities_entity_type", "config_id", "entity_type"),
    )


class PrincipalGroup(Base):
    """Named group of users and/or service principals — used as a principal_id in ACL entries."""

    __tablename__ = "principal_groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    team_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # List of member identifiers (user emails or service_principal IDs)
    member_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_principal_groups_team", "team_id"),
        Index("ix_principal_groups_team_name", "team_id", "name", unique=True),
    )
