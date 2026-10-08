"""Pydantic schemas for API request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from api.models.enums import (
    A2AStatus,
    AgentStatus,
    BudgetDuration,
    KeyScopeType,
    ListingStatus,
    ProviderStatus,
    ProviderType,
    TemplateCategory,
    TemplateStatus,
    UserRole,
)

T = TypeVar("T")


# --- Standard API Response ---


class ApiMeta(BaseModel):
    page: int = 1
    per_page: int = 20
    total: int = 0


class ApiResponse(BaseModel, Generic[T]):
    """Standard API response wrapper: {data, meta, errors}."""

    data: T
    meta: ApiMeta = Field(default_factory=ApiMeta)
    errors: list[str] = Field(default_factory=list)


# --- Auth Schemas ---


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str
    team: str = "default"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # True when the authenticated user must rotate their password before
    # accessing any other endpoint. Drives forced-password-change flows in
    # Studio and the CLI. Issue #464.
    must_change_password: bool = False


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: UserRole
    team: str
    is_active: bool
    must_change_password: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


# --- Agent Schemas ---


class AgentCreate(BaseModel):
    name: str
    version: str
    description: str = ""
    team: str
    owner: str
    framework: str
    model_primary: str
    model_fallback: str | None = None
    endpoint_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    config_snapshot: dict[str, Any] = Field(default_factory=dict)


class AgentBriefResponse(BaseModel):
    """Minimal agent info for usage references."""

    id: uuid.UUID
    name: str
    status: AgentStatus

    model_config = {"from_attributes": True}


class AgentUpdate(BaseModel):
    version: str | None = None
    description: str | None = None
    endpoint_url: str | None = None
    status: AgentStatus | None = None
    tags: list[str] | None = None


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    version: str
    description: str
    team: str
    owner: str
    framework: str
    model_primary: str
    model_fallback: str | None
    endpoint_url: str | None
    status: AgentStatus
    tags: list[str]
    config_snapshot: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentCloneRequest(BaseModel):
    name: str
    version: str = "1.0.0"


class AgentYamlRequest(BaseModel):
    """Request body for YAML-based agent operations."""

    yaml_content: str


class AgentValidationErrorItem(BaseModel):
    path: str
    message: str
    suggestion: str = ""


class AgentValidationResponse(BaseModel):
    """Response from the /validate endpoint."""

    valid: bool
    errors: list[AgentValidationErrorItem] = Field(default_factory=list)
    warnings: list[AgentValidationErrorItem] = Field(default_factory=list)


# --- Tool Schemas ---


class ToolCreate(BaseModel):
    name: str
    description: str = ""
    tool_type: str = "mcp_server"
    schema_definition: dict[str, Any] = Field(default_factory=dict)
    endpoint: str | None = None
    source: str = "manual"


class ToolResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    tool_type: str
    endpoint: str | None
    status: str
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ToolDetailResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    tool_type: str
    schema_definition: dict[str, Any]
    endpoint: str | None
    status: str
    source: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ToolUsageResponse(BaseModel):
    agent_id: uuid.UUID
    agent_name: str
    agent_status: str


class ToolExecuteRequest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


class ToolExecuteResponse(BaseModel):
    output: Any = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    error: str | None = None


class PromptRenderRequest(BaseModel):
    user_message: str = ""
    model: str = "gemini-2.5-flash"
    temperature: float = 0.4


class PromptRenderResponse(BaseModel):
    output: str = ""
    model: str = ""
    duration_ms: int = 0
    error: str | None = None


class AgentInvokeRequest(BaseModel):
    input: str
    endpoint_url: str | None = None
    # Optional explicit override. When omitted (the default for the dashboard's
    # Invoke panel) the API resolves the token from the workspace secrets
    # backend keyed by ``agentbreeder/<agent-name>/auth-token``.
    auth_token: str | None = None
    session_id: str | None = None


class AgentInvokeToolCall(BaseModel):
    """Structured tool-call entry in an agent's invoke history (#215).

    Mirrors the ``ToolCall`` shape every runtime template returns under
    ``InvokeResponse.history`` so the dashboard playground can render a
    tool-call timeline without regex-scraping the message body.
    """

    name: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    duration_ms: int = 0
    started_at: str = ""


class AgentInvokeResponse(BaseModel):
    output: str = ""
    session_id: str | None = None
    duration_ms: int = 0
    error: str | None = None
    status_code: int = 0
    # Structured tool-call timeline forwarded from the runtime (#215).  Always
    # present; empty for runtimes that don't surface tool telemetry naturally.
    history: list[AgentInvokeToolCall] = Field(default_factory=list)


# --- Model Schemas ---


class ModelCreate(BaseModel):
    name: str
    provider: str
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    source: str = "manual"
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    capabilities: list[str] | None = None


class ModelResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    description: str
    status: str
    source: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    capabilities: list[str] | None = None
    # Track G — model lifecycle (#163). All nullable for legacy/manual rows.
    discovered_at: datetime | None = None
    last_seen_at: datetime | None = None
    deprecated_at: datetime | None = None
    deprecation_replacement_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ModelUsageResponse(BaseModel):
    agent_id: uuid.UUID
    agent_name: str
    agent_status: str
    usage_type: str  # "primary" or "fallback"


# --- Prompt Schemas ---


class PromptCreate(BaseModel):
    name: str
    version: str
    content: str
    description: str = ""
    team: str


class PromptUpdate(BaseModel):
    content: str | None = None
    description: str | None = None


class PromptContentUpdate(BaseModel):
    """Update just the prompt content; auto-creates a version snapshot."""

    content: str
    change_summary: str | None = None
    author: str = "builder"


class PromptResponse(BaseModel):
    id: uuid.UUID
    name: str
    version: str
    content: str
    description: str
    team: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Prompt Version Schemas ---


class PromptVersionCreate(BaseModel):
    version: str
    content: str
    change_summary: str | None = None
    author: str


class PromptVersionResponse(BaseModel):
    id: uuid.UUID
    prompt_id: uuid.UUID
    version: str
    content: str
    change_summary: str | None
    author: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PromptVersionDiffResponse(BaseModel):
    version_a: PromptVersionResponse
    version_b: PromptVersionResponse
    diff: list[str]


# --- Builder Session Schemas ---


class BuilderSessionResponse(BaseModel):
    id: str
    team: str
    engine: str
    agent_yaml: str | None = None
    files: dict[str, str] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)


class BuilderSessionCreateRequest(BaseModel):
    engine: str = "claude"  # "claude" | "codex"


class BuilderMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10_000)


class BuilderEjectRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=4000)
    engine: str | None = None  # override session engine for this run


# --- Provider Schemas ---


class ProviderCreate(BaseModel):
    name: str
    provider_type: ProviderType
    base_url: str | None = None
    config: dict[str, Any] | None = None


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    status: ProviderStatus | None = None
    config: dict[str, Any] | None = None


class ProviderResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider_type: ProviderType
    base_url: str | None
    status: ProviderStatus
    is_enabled: bool
    last_verified: datetime | None
    latency_ms: int | None
    avg_latency_ms: int | None
    model_count: int
    config: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DiscoveredModel(BaseModel):
    id: str
    name: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    capabilities: list[str] = Field(default_factory=list)


class ProviderStatusSummary(BaseModel):
    """First-run detection: tells the dashboard if any providers exist."""

    has_providers: bool
    provider_count: int
    total_models: int


# --- MCP Server Schemas ---


class McpServerCreate(BaseModel):
    name: str
    endpoint: str
    transport: str = "stdio"
    timeout_seconds: int = Field(
        default=10,
        ge=1,
        le=120,
        description="HTTP timeout (seconds) for MCP discovery + connectivity calls.",
    )


class McpServerUpdate(BaseModel):
    name: str | None = None
    endpoint: str | None = None
    transport: str | None = None
    status: str | None = None
    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        le=120,
        description="HTTP timeout (seconds) for MCP discovery + connectivity calls.",
    )


class McpServerResponse(BaseModel):
    id: uuid.UUID
    name: str
    endpoint: str
    transport: str
    status: str
    tool_count: int
    last_ping_at: datetime | None
    created_at: datetime
    updated_at: datetime
    timeout_seconds: int = 10

    model_config = {"from_attributes": True}

    @classmethod
    def model_validate(  # type: ignore[override]
        cls,
        obj: object,
        *args: Any,
        **kwargs: Any,
    ) -> McpServerResponse:
        base = super().model_validate(obj, *args, **kwargs)
        # Pull timeout_seconds out of deploy_config JSON when loading from ORM.
        cfg = getattr(obj, "deploy_config", None) or {}
        timeout = cfg.get("timeout_seconds") if isinstance(cfg, dict) else None
        if isinstance(timeout, int) and 1 <= timeout <= 120:
            base.timeout_seconds = timeout
        return base


class McpServerTestResult(BaseModel):
    success: bool
    latency_ms: int | None = None
    error: str | None = None


class McpServerDiscoveredTool(BaseModel):
    name: str
    description: str
    schema_definition: dict[str, Any] = Field(default_factory=dict)


class McpServerDiscoverResult(BaseModel):
    tools: list[McpServerDiscoveredTool]
    total: int


# --- Search ---


class SearchResult(BaseModel):
    entity_type: str
    id: uuid.UUID
    name: str
    description: str
    team: str | None = None
    score: float = 1.0


# --- Sandbox Schemas ---


class SandboxExecuteRequest(BaseModel):
    """Request to execute tool code in an isolated sandbox."""

    code: str
    input_json: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    network_enabled: bool = False
    tool_id: str | None = None


class SandboxExecuteResponse(BaseModel):
    """Result of a sandbox tool execution."""

    execution_id: str
    output: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timed_out: bool = False
    error: str | None = None


# --- Memory Schemas ---


class CreateMemoryConfigRequest(BaseModel):
    name: str
    team: str = "default"
    owner: str = ""
    backend_type: Literal["postgresql", "redis"] = "postgresql"
    memory_type: Literal["buffer_window", "buffer", "summary", "entity", "semantic"] = (
        "buffer_window"
    )
    max_messages: int = Field(default=100, ge=1, le=100_000)
    namespace_pattern: str = "{agent_id}:{session_id}"
    scope: str = "agent"  # "agent" (team/global: Phase 2)
    linked_agents: list[str] = Field(default_factory=list)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    # MM8: Optional TTL in seconds. None = messages never expire. When set,
    # ``MemoryService.cleanup_expired_messages()`` will delete messages older
    # than this many seconds. Enforcement is opt-in (deployer cron / one-shot).
    ttl_seconds: int | None = Field(default=None, ge=1)


class MemoryConfigResponse(BaseModel):
    id: str
    name: str
    backend_type: str
    memory_type: str
    max_messages: int
    namespace_pattern: str
    scope: str
    linked_agents: list[str]
    description: str
    # MM8: Echo the TTL setting so clients can display expiration policy.
    ttl_seconds: int | None = None
    created_at: datetime
    updated_at: datetime


class MemoryMessageCreate(BaseModel):
    session_id: str
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str = Field(..., max_length=100_000)
    agent_id: str | None = None
    # ``metadata`` is free-form JSON. Recognised keys:
    #   - ``user_id`` (str | None): the human user this message belongs to.
    #     Required for GDPR-style ``DELETE /api/v1/memory/user/{user_id}``
    #     "right to be forgotten" requests (MM9). Messages without a
    #     ``user_id`` are not user-deletable.
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryMessageResponse(BaseModel):
    id: str
    config_id: str
    session_id: str
    agent_id: str | None
    role: str
    content: str
    metadata: dict[str, Any]
    timestamp: datetime


class MemoryStatsResponse(BaseModel):
    config_id: str
    backend_type: str
    memory_type: str
    message_count: int
    session_count: int
    storage_size_bytes: int
    linked_agent_count: int


class ConversationSummaryResponse(BaseModel):
    session_id: str
    agent_id: str | None
    message_count: int
    first_message_at: datetime | None
    last_message_at: datetime | None


class DeleteConversationsRequest(BaseModel):
    session_id: str | None = None
    agent_id: str | None = None
    before: str | None = None  # ISO datetime string


class MemorySearchResultResponse(BaseModel):
    message: MemoryMessageResponse
    score: float
    highlight: str


# --- A2A Agent Schemas ---


class AgentCardSkill(BaseModel):
    """A skill exposed by an A2A agent."""

    id: str
    name: str
    description: str = ""
    input_modes: list[str] = Field(default_factory=lambda: ["text"])
    output_modes: list[str] = Field(default_factory=lambda: ["text"])


class AgentCard(BaseModel):
    """Google A2A Agent Card — describes an agent's capabilities."""

    name: str
    description: str = ""
    url: str
    version: str = "1.0.0"
    capabilities: list[str] = Field(default_factory=list)
    skills: list[AgentCardSkill] = Field(default_factory=list)
    auth_schemes: list[str] = Field(default_factory=lambda: ["none"])
    default_input_modes: list[str] = Field(default_factory=lambda: ["text"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text"])


class A2AAgentCreate(BaseModel):
    name: str
    endpoint_url: str
    agent_id: uuid.UUID | None = None
    agent_card: AgentCard | None = None
    capabilities: list[str] = Field(default_factory=list)
    auth_scheme: str = "none"
    team: str | None = None


class A2AAgentUpdate(BaseModel):
    endpoint_url: str | None = None
    agent_card: dict[str, Any] | None = None
    capabilities: list[str] | None = None
    auth_scheme: str | None = None
    status: A2AStatus | None = None


class A2AAgentResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID | None
    name: str
    agent_card: dict[str, Any]
    endpoint_url: str
    status: A2AStatus
    capabilities: list[str]
    auth_scheme: str | None
    team: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class A2AInvokeRequest(BaseModel):
    input_message: str
    context: dict[str, Any] = Field(default_factory=dict)


class A2AInvokeResponse(BaseModel):
    output: str
    tokens: int = 0
    latency_ms: int = 0
    status: str = "success"
    error: str | None = None


# --- Template & Marketplace Schemas (M21 / M22) ---


class TemplateParameter(BaseModel):
    """A user-fillable parameter in a template."""

    name: str
    label: str = ""
    description: str = ""
    type: str = "string"
    default: str | None = None
    required: bool = True
    options: list[str] = Field(default_factory=list)


class TemplateCreate(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str = ""
    category: TemplateCategory = TemplateCategory.other
    framework: str
    config_template: dict[str, Any]
    parameters: list[TemplateParameter] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    author: str
    team: str = "default"
    readme: str = ""


class TemplateUpdate(BaseModel):
    version: str | None = None
    description: str | None = None
    category: TemplateCategory | None = None
    config_template: dict[str, Any] | None = None
    parameters: list[TemplateParameter] | None = None
    tags: list[str] | None = None
    status: TemplateStatus | None = None
    readme: str | None = None


class TemplateResponse(BaseModel):
    id: uuid.UUID
    name: str
    version: str
    description: str
    category: TemplateCategory
    framework: str
    config_template: dict[str, Any]
    parameters: list[dict[str, Any]]
    tags: list[str]
    author: str
    team: str
    status: TemplateStatus
    use_count: int
    readme: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TemplateInstantiateRequest(BaseModel):
    """Fill in template parameters to generate an agent.yaml."""

    values: dict[str, str]


class TemplateInstantiateResponse(BaseModel):
    yaml_content: str
    agent_name: str


class MarketplaceListingCreate(BaseModel):
    template_id: uuid.UUID
    submitted_by: str


class MarketplaceListingUpdate(BaseModel):
    status: ListingStatus | None = None
    reviewed_by: str | None = None
    reject_reason: str | None = None
    featured: bool | None = None


class MarketplaceListingResponse(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    status: ListingStatus
    submitted_by: str
    reviewed_by: str | None
    reject_reason: str | None
    featured: bool
    avg_rating: float
    review_count: int
    install_count: int
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    template: TemplateResponse | None = None

    model_config = {"from_attributes": True}


class ListingReviewCreate(BaseModel):
    reviewer: str
    rating: int = Field(ge=1, le=5)
    comment: str = ""


class ListingReviewResponse(BaseModel):
    id: uuid.UUID
    listing_id: uuid.UUID
    reviewer: str
    rating: int
    comment: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MarketplaceBrowseItem(BaseModel):
    """Flattened view for marketplace browsing."""

    listing_id: uuid.UUID
    template_id: uuid.UUID
    name: str
    description: str
    category: TemplateCategory
    framework: str
    tags: list[str]
    author: str
    avg_rating: float
    review_count: int
    install_count: int
    featured: bool
    published_at: datetime | None


# ---------------------------------------------------------------------------
# LiteLLM Virtual Key Schemas
# ---------------------------------------------------------------------------


class LiteLLMKeyCreate(BaseModel):
    key_alias: str = Field(..., description="Human-readable alias, e.g. 'team-engineering-prod'")
    scope_type: KeyScopeType
    scope_id: str = Field(..., description="Team name, user id, agent name, etc.")
    team_id: str | None = None
    agent_name: str | None = None
    allowed_models: list[str] | None = Field(
        None, description="Model IDs this key may call. Null = all models."
    )
    max_budget: float | None = Field(None, description="Max spend in USD (null = unlimited)")
    budget_duration: BudgetDuration | None = None
    tpm_limit: int | None = Field(None, description="Token-per-minute rate limit")
    rpm_limit: int | None = Field(None, description="Requests-per-minute rate limit")
    tags: list[str] = Field(default_factory=list, description="Routing / cost-attribution tags")
    expires_at: datetime | None = None


class LiteLLMKeyResponse(BaseModel):
    id: uuid.UUID
    key_alias: str
    key_prefix: str
    litellm_key_id: str | None
    scope_type: KeyScopeType
    scope_id: str
    team_id: str | None
    agent_name: str | None
    created_by: str
    allowed_models: list[str] | None
    max_budget: float | None
    budget_duration: BudgetDuration | None
    tpm_limit: int | None
    rpm_limit: int | None
    tags: list[str]
    expires_at: datetime | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LiteLLMKeyCreateResponse(LiteLLMKeyResponse):
    """Returned only on creation — includes the full key value once."""

    key_value: str = Field(
        ..., description="Full sk-... value. Store it — it will not be shown again."
    )


# ---------------------------------------------------------------------------
# RBAC Phase 2 — Resource Permissions + Asset Approvals
# ---------------------------------------------------------------------------

VALID_ACTIONS = {"read", "use", "write", "deploy", "publish", "admin"}
VALID_RESOURCE_TYPES = {"agent", "prompt", "tool", "memory", "rag", "model", "mcp_server"}
VALID_PRINCIPAL_TYPES = {"user", "team", "service_principal", "group"}
VALID_APPROVAL_STATUSES = {"pending", "approved", "rejected"}


class PermissionGrant(BaseModel):
    """Request body for granting a permission."""

    resource_type: str = Field(
        ..., description="One of: agent, prompt, tool, memory, rag, model, mcp_server"
    )
    resource_id: uuid.UUID
    principal_type: str = Field(..., description="One of: user, team, service_principal, group")
    principal_id: str
    actions: list[str] = Field(..., description='e.g. ["read", "use", "deploy"]')


class PermissionResponse(BaseModel):
    id: uuid.UUID
    resource_type: str
    resource_id: uuid.UUID
    principal_type: str
    principal_id: str
    actions: list[str]
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PermissionCheckResponse(BaseModel):
    allowed: bool
    reason: str


class ApprovalRequestCreate(BaseModel):
    """Submit an asset for admin approval."""

    asset_type: str
    asset_id: uuid.UUID
    asset_version: str | None = None
    message: str | None = Field(None, description="Optional note from the submitter")


class ApprovalResponse(BaseModel):
    id: uuid.UUID
    asset_type: str
    asset_id: uuid.UUID
    asset_version: str | None
    submitter_id: str
    status: str
    approver_id: str | None
    reason: str | None
    message: str | None
    created_at: datetime
    decided_at: datetime | None

    model_config = {"from_attributes": True}


class ApprovalDecision(BaseModel):
    """Body for approve/reject endpoints."""

    reason: str | None = Field(None, description="Admin note explaining the decision")


# ---------------------------------------------------------------------------
# RBAC Phase 3 — Service Principals + Principal Groups
# ---------------------------------------------------------------------------

VALID_SP_ROLES = {"deployer", "contributor", "viewer"}


class ServicePrincipalCreate(BaseModel):
    name: str = Field(..., description="Unique slug for this service principal")
    team_id: str
    role: str = Field("viewer", description="One of: deployer, contributor, viewer")
    allowed_assets: list[str] | None = Field(
        None,
        description='Optional allowlist: ["agent:uuid", "prompt:uuid", ...]',
    )


class ServicePrincipalUpdate(BaseModel):
    role: str | None = None
    allowed_assets: list[str] | None = None
    is_active: bool | None = None


class ServicePrincipalResponse(BaseModel):
    id: uuid.UUID
    name: str
    team_id: str
    role: str
    allowed_assets: list[str] | None
    created_by: str
    last_used_at: datetime | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ServicePrincipalKeyResponse(BaseModel):
    """Returned after key rotation — includes full key value once."""

    service_principal_id: uuid.UUID
    key_alias: str
    key_value: str = Field(..., description="Full sk-... value. Store it — not shown again.")


class PrincipalGroupCreate(BaseModel):
    name: str
    team_id: str
    member_ids: list[str] = Field(default_factory=list)


class PrincipalGroupUpdate(BaseModel):
    name: str | None = None


class PrincipalGroupResponse(BaseModel):
    id: uuid.UUID
    name: str
    team_id: str
    member_ids: list[str]
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class GroupMemberAdd(BaseModel):
    member_id: str = Field(..., description="User email or service_principal ID")


# --- Analytics (W4 builder funnel) ---


class AnalyticsEventIngest(BaseModel):
    """PII-free structural product-analytics event (design §11.2)."""

    event: str = Field(..., min_length=1, max_length=64)
    engine: str | None = Field(default=None, max_length=20)
    session_id: uuid.UUID | None = None  # typed -> pydantic 422s on bad UUIDs
    props: dict[str, Any] = Field(default_factory=dict)


class FunnelStage(BaseModel):
    key: str
    label: str
    count: int
    dropoff_pct: float  # vs previous stage; 0.0 for the first


class EngineScorecard(BaseModel):
    engine: str
    samples: int
    spec_validity_rate: float
    deploy_success_rate: float
    turns_to_spec: float
    hallucinated_field_rate: float


class FunnelMetrics(BaseModel):
    period: str
    time_to_first_deploy_p50_s: float | None = None
    time_to_first_deploy_p90_s: float | None = None
    stages: list[FunnelStage] = Field(default_factory=list)
    engines: list[EngineScorecard] = Field(default_factory=list)
