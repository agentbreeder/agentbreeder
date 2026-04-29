"""Pydantic schemas for API request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

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


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: UserRole
    team: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


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


class AgentInvokeResponse(BaseModel):
    output: str = ""
    session_id: str | None = None
    duration_ms: int = 0
    error: str | None = None
    status_code: int = 0


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


class PromptTestRequest(BaseModel):
    """Request body for testing a prompt against an LLM."""

    prompt_text: str
    model_id: str | None = None
    model_name: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    temperature: float = 0.7
    max_tokens: int = 1024


class PromptTestResponse(BaseModel):
    """Response from a prompt test execution."""

    response_text: str
    rendered_prompt: str
    model_name: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: int
    temperature: float


# --- Deploy Schemas ---


class DeployRequest(BaseModel):
    agent_id: uuid.UUID | None = None
    config_path: str | None = None
    config_yaml: str | None = None
    target: str = "local"


class DeployJobResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str | None = None
    status: DeployJobStatus
    target: str
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class DeployLogEntry(BaseModel):
    timestamp: str
    level: str
    message: str
    step: str | None = None


class DeployJobDetailResponse(BaseModel):
    """Deploy job with logs -- returned by the detail endpoint."""

    id: str
    agent_id: str
    agent_name: str | None = None
    status: str
    target: str
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    logs: list[DeployLogEntry] = Field(default_factory=list)


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


class ProviderTestResult(BaseModel):
    success: bool
    latency_ms: int | None = None
    models_found: int | None = None
    error: str | None = None


class DiscoveredModel(BaseModel):
    id: str
    name: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    capabilities: list[str] = Field(default_factory=list)


class ModelDiscoveryResult(BaseModel):
    provider_id: uuid.UUID
    provider_type: ProviderType
    models: list[DiscoveredModel]
    total: int


class ProviderDiscoverResult(BaseModel):
    """Legacy alias — prefer ModelDiscoveryResult."""

    models: list[DiscoveredModel]
    total: int


class ProviderStatusSummary(BaseModel):
    """First-run detection: tells the dashboard if any providers exist."""

    has_providers: bool
    provider_count: int
    total_models: int


class ProviderHealthCheckResult(BaseModel):
    """Result of a single provider's health check."""

    provider_id: str
    name: str
    status: str
    checked: bool
    latency_ms: int | None = None
    success: bool | None = None
    reason: str | None = None


class OllamaDetectResult(BaseModel):
    """Result of Ollama auto-detection."""

    provider: ProviderResponse
    models: list[DiscoveredModel]
    created: bool


# --- MCP Server Schemas ---


class McpServerCreate(BaseModel):
    name: str
    endpoint: str
    transport: str = "stdio"


class McpServerUpdate(BaseModel):
    name: str | None = None
    endpoint: str | None = None
    transport: str | None = None
    status: str | None = None


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

    model_config = {"from_attributes": True}


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


# --- Git / Pull Request Schemas ---


class GitBranchCreateRequest(BaseModel):
    """Request to create a draft branch."""

    user: str
    resource_type: str
    resource_name: str


class GitBranchResponse(BaseModel):
    branch: str


class GitBranchListResponse(BaseModel):
    branches: list[str]


class GitCommitRequest(BaseModel):
    """Request to commit a file change on a branch."""

    branch: str
    file_path: str
    content: str
    message: str
    author: str


class GitCommitResponse(BaseModel):
    sha: str
    author: str
    date: str
    message: str


class GitDiffEntry(BaseModel):
    file_path: str
    status: str
    diff_text: str = ""


class GitDiffResponse(BaseModel):
    base: str
    head: str
    files: list[GitDiffEntry] = Field(default_factory=list)
    stats: str = ""


class GitPRCreateRequest(BaseModel):
    """Request to create a pull request."""

    branch: str
    title: str
    description: str = ""
    submitter: str


class GitPRCommentRequest(BaseModel):
    author: str
    text: str


class GitPRCommentResponse(BaseModel):
    id: uuid.UUID
    pr_id: uuid.UUID
    author: str
    text: str
    created_at: datetime


class GitPRRejectRequest(BaseModel):
    reviewer: str
    reason: str


class GitPRApproveRequest(BaseModel):
    reviewer: str


class GitPRMergeRequest(BaseModel):
    tag_version: str | None = None


class GitPRResponse(BaseModel):
    id: uuid.UUID
    branch: str
    title: str
    description: str
    submitter: str
    resource_type: str
    resource_name: str
    status: str
    reviewer: str | None = None
    reject_reason: str | None = None
    tag: str | None = None
    comments: list[GitPRCommentResponse] = Field(default_factory=list)
    commits: list[GitCommitResponse] = Field(default_factory=list)
    diff: GitDiffResponse | None = None
    created_at: datetime
    updated_at: datetime


class GitPRListResponse(BaseModel):
    prs: list[GitPRResponse]


# --- Memory Schemas ---


class CreateMemoryConfigRequest(BaseModel):
    name: str
    team: str = "default"
    owner: str = ""
    backend_type: str = "postgresql"  # "postgresql" | "redis"
    memory_type: str = (
        "buffer_window"  # "buffer_window" | "buffer" (summary/entity/semantic: Phase 2)
    )
    max_messages: int = Field(default=100, ge=1, le=100_000)
    namespace_pattern: str = "{agent_id}:{session_id}"
    scope: str = "agent"  # "agent" (team/global: Phase 2)
    linked_agents: list[str] = Field(default_factory=list)
    description: str = ""
    tags: list[str] = Field(default_factory=list)


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
    created_at: datetime
    updated_at: datetime


class MemoryMessageCreate(BaseModel):
    session_id: str
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    agent_id: str | None = None
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


# --- RAG / Vector Index Schemas ---


class CreateIndexRequest(BaseModel):
    name: str
    description: str = ""
    embedding_model: str = "openai/text-embedding-3-small"
    chunk_strategy: str = "fixed_size"
    chunk_size: int = 512
    chunk_overlap: int = 64
    source: str = "manual"


class VectorIndexResponse(BaseModel):
    id: str
    name: str
    description: str
    embedding_model: str
    chunk_strategy: str
    chunk_size: int
    chunk_overlap: int
    dimensions: int
    source: str
    doc_count: int
    chunk_count: int
    created_at: str
    updated_at: str
    index_type: str = "vector"
    entity_model: str = "claude-haiku-4-5-20251001"
    max_hops: int = 2
    relationship_types: list[str] = []
    node_count: int = 0
    edge_count: int = 0


class IngestJobResponse(BaseModel):
    id: str
    index_id: str
    status: str
    total_files: int
    processed_files: int
    total_chunks: int
    embedded_chunks: int
    progress_pct: float
    error: str | None = None
    started_at: str
    completed_at: str | None = None


class RAGSearchRequest(BaseModel):
    index_id: str
    query: str
    top_k: int = 10
    vector_weight: float = 0.7
    text_weight: float = 0.3


class RAGSearchHit(BaseModel):
    chunk_id: str
    text: str
    source: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class RAGSearchResponse(BaseModel):
    index_id: str
    query: str
    top_k: int
    results: list[RAGSearchHit]
    total: int


# --- Evaluation Framework (M18) ---


class EvalDatasetCreate(BaseModel):
    name: str
    description: str = ""
    agent_id: uuid.UUID | None = None
    version: str = "1.0.0"
    format: str = "jsonl"
    team: str = "default"
    tags: list[str] = Field(default_factory=list)


class EvalDatasetResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    agent_id: uuid.UUID | None
    version: str
    format: str
    row_count: int
    team: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EvalDatasetRowCreate(BaseModel):
    input: dict[str, Any]
    expected_output: str
    expected_tool_calls: list[dict[str, Any]] | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalDatasetRowResponse(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    input: dict[str, Any]
    expected_output: str
    expected_tool_calls: list[dict[str, Any]] | None
    tags: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class EvalRunCreate(BaseModel):
    agent_name: str
    dataset_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class EvalRunResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID | None
    agent_name: str
    dataset_id: uuid.UUID
    status: EvalRunStatus
    config: dict[str, Any]
    summary: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class EvalResultResponse(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    row_id: uuid.UUID
    actual_output: str
    scores: dict[str, Any]
    latency_ms: int
    token_count: int
    cost_usd: float
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class EvalRunDetailResponse(BaseModel):
    """Eval run with all results included."""

    id: uuid.UUID
    agent_id: uuid.UUID | None
    agent_name: str
    dataset_id: uuid.UUID
    status: EvalRunStatus
    config: dict[str, Any]
    summary: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    results: list[EvalResultResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class EvalScoreSummary(BaseModel):
    """Per-metric aggregation for a run."""

    metric: str
    mean: float
    median: float
    p95: float
    min: float
    max: float
    count: int


# --- Orchestration Schemas ---


class OrchestrationCreate(BaseModel):
    name: str
    version: str
    description: str = ""
    team: str | None = None
    owner: str | None = None
    strategy: str
    agents: dict[str, Any]
    shared_state: dict[str, Any] = Field(default_factory=dict)
    deploy: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class OrchestrationUpdate(BaseModel):
    version: str | None = None
    description: str | None = None
    strategy: str | None = None
    agents_config: dict[str, Any] | None = None
    status: OrchestrationStatus | None = None
    tags: list[str] | None = None


class OrchestrationResponse(BaseModel):
    id: uuid.UUID
    name: str
    version: str
    description: str
    team: str | None
    owner: str | None
    strategy: str
    agents_config: dict[str, Any]
    shared_state_config: dict[str, Any] | None
    deploy_config: dict[str, Any] | None
    status: str
    endpoint_url: str | None
    config_snapshot: dict[str, Any] | None
    tags: list[str] | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OrchestrationExecuteRequest(BaseModel):
    input_message: str
    context: dict[str, Any] = Field(default_factory=dict)


class OrchestrationExecuteResponse(BaseModel):
    orchestration_name: str
    strategy: str
    input_message: str
    output: str
    agent_trace: list[dict[str, Any]] = Field(default_factory=list)
    total_latency_ms: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0


class OrchestrationValidateResponse(BaseModel):
    valid: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)


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
