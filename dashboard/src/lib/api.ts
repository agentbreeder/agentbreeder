/** API client for AgentBreeder backend. */

const BASE = "/api/v1";

export interface ApiMeta {
  page: number;
  per_page: number;
  total: number;
}

export interface ApiResponse<T> {
  data: T;
  meta: ApiMeta;
  errors: string[];
}

function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = localStorage.getItem("ag-token");
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<ApiResponse<T>> {
  const res = await fetch(`${BASE}${path}`, {
    headers: getAuthHeaders(),
    ...init,
  });
  if (res.status === 401) {
    // Token expired or invalid — clear and redirect to login
    localStorage.removeItem("ag-token");
    if (window.location.pathname !== "/login") {
      window.location.href = "/login";
    }
    throw new Error("Session expired");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `API error ${res.status}`);
  }
  return res.json();
}

// --- Agent types ---

export type AgentStatus = "deploying" | "running" | "stopped" | "failed" | "degraded" | "error";

export interface Agent {
  id: string;
  name: string;
  version: string;
  description: string;
  team: string;
  owner: string;
  framework: string;
  model_primary: string;
  model_fallback: string | null;
  endpoint_url: string | null;
  status: AgentStatus;
  tags: string[];
  config_snapshot: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// --- Agent validation types ---

export interface AgentValidationError {
  path: string;
  message: string;
  suggestion: string;
}

export interface AgentValidationResult {
  valid: boolean;
  errors: AgentValidationError[];
  warnings: AgentValidationError[];
}

// --- Tool types ---

export interface Tool {
  id: string;
  name: string;
  description: string;
  tool_type: string;
  endpoint: string | null;
  status: string;
  source: string;
  created_at: string;
}

export interface ToolDetail extends Tool {
  schema_definition: Record<string, unknown>;
  updated_at: string;
}

export interface ToolUsage {
  agent_id: string;
  agent_name: string;
  agent_status: string;
  agent_version: string;
  last_deployed: string | null;
}

export type ToolHealthStatus = "healthy" | "slow" | "down" | "unknown";

export interface ToolHealth {
  status: ToolHealthStatus;
  last_ping: string | null;
  latency_ms: number | null;
}

export interface ToolRunResponse {
  output: unknown;
  stdout: string;
  stderr: string;
  exit_code: number;
  duration_ms: number;
  error: string | null;
}

export interface PromptRenderResponse {
  output: string;
  model: string;
  duration_ms: number;
  error: string | null;
}

export interface AgentInvokeResponse {
  output: string;
  session_id: string | null;
  duration_ms: number;
  status_code: number;
  error: string | null;
}

// --- Model types ---

export interface Model {
  id: string;
  name: string;
  provider: string;
  description: string;
  status: string;
  source: string;
  context_window: number | null;
  max_output_tokens: number | null;
  input_price_per_million: number | null;
  output_price_per_million: number | null;
  capabilities: string[] | null;
  // Track G — model lifecycle (#163). Nullable for legacy/manual entries.
  discovered_at: string | null;
  last_seen_at: string | null;
  deprecated_at: string | null;
  deprecation_replacement_id: string | null;
  created_at: string;
  updated_at: string | null;
}

/** Per-provider result emitted by `POST /api/v1/models/sync`. */
export interface ModelSyncProviderResult {
  provider: string;
  added: string[];
  seen: string[];
  deprecated: string[];
  retired: string[];
  error: string | null;
  total_seen: number;
}

/** Top-level result of `POST /api/v1/models/sync`. */
export interface ModelSyncResult {
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  providers: ModelSyncProviderResult[];
  totals: { added: number; deprecated: number; retired: number };
}

export interface ModelUsage {
  agent_id: string;
  agent_name: string;
  agent_status: string;
  usage_type: string;
  token_count: number | null;
  last_used: string | null;
}

// --- Prompt types ---

export interface Prompt {
  id: string;
  name: string;
  version: string;
  content: string;
  description: string;
  team: string;
  created_at: string;
}

// --- Prompt Version types ---

export interface PromptVersion {
  id: string;
  prompt_id: string;
  version_number: number;
  content: string;
  change_summary: string;
  created_by: string;
  created_at: string;
}

export interface PromptDiff {
  left_version: number;
  right_version: number;
  diff: string;
}

// --- Deploy types ---

export type DeployJobStatus =
  | "pending"
  | "parsing"
  | "building"
  | "provisioning"
  | "deploying"
  | "health_checking"
  | "registering"
  | "completed"
  | "failed";

export interface DeployJob {
  id: string;
  agent_id: string;
  agent_name: string | null;
  status: DeployJobStatus;
  target: string;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface DeployLogEntry {
  timestamp: string;
  level: string;
  message: string;
  step: string | null;
}

export interface DeployJobDetail extends DeployJob {
  logs: DeployLogEntry[];
}

// --- Provider types ---

export type ProviderType =
  | "openai"
  | "anthropic"
  | "google"
  | "ollama"
  | "litellm"
  | "openrouter";

export type ProviderStatus = "active" | "disabled" | "error";

export interface Provider {
  id: string;
  name: string;
  provider_type: ProviderType;
  base_url: string | null;
  status: ProviderStatus;
  last_verified: string | null;
  latency_ms: number | null;
  model_count: number;
  config: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

/**
 * A preset entry in the OpenAI-compatible provider catalog (Track F / issue #160).
 *
 * These are read from `engine/providers/catalog.yaml` plus user-local overrides
 * at `~/.agentbreeder/providers.local.yaml`. The dashboard surfaces them as a
 * "Configure" list so users can connect a provider in one click.
 */
export interface CatalogProvider {
  name: string;
  type: "openai_compatible" | "gateway";
  base_url: string;
  api_key_env: string;
  default_headers: Record<string, string>;
  docs: string | null;
  discovery: string | null;
  notable_models: string[];
  source: "builtin" | "user-local" | "workspace";
}

export interface ProviderTestResult {
  success: boolean;
  latency_ms: number | null;
  model_count: number | null;
  error: string | null;
}

export interface ProviderDiscoverResult {
  models: string[];
  total: number;
}

// --- MCP Server types ---

export type McpTransport = "stdio" | "sse" | "streamable_http";

export interface McpServer {
  id: string;
  name: string;
  endpoint: string;
  transport: McpTransport;
  status: string;
  tool_count: number;
  last_ping_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface McpServerTestResult {
  success: boolean;
  latency_ms: number | null;
  error: string | null;
}

export interface McpServerDiscoveredTool {
  name: string;
  description: string;
  schema_definition: Record<string, unknown>;
}

export interface McpServerDiscoverResult {
  tools: McpServerDiscoveredTool[];
  total: number;
}

// --- A2A Agent types ---

export type A2AStatus = "registered" | "active" | "inactive" | "error";

export interface A2AAgent {
  id: string;
  agent_id: string | null;
  name: string;
  agent_card: Record<string, unknown>;
  endpoint_url: string;
  status: A2AStatus;
  capabilities: string[];
  auth_scheme: string | null;
  team: string | null;
  created_at: string;
  updated_at: string;
}

export interface A2AInvokeResponse {
  output: string;
  tokens: number;
  latency_ms: number;
  status: string;
  error: string | null;
}

// --- Template & Marketplace types ---

export type TemplateCategory =
  | "customer_support"
  | "data_analysis"
  | "code_review"
  | "research"
  | "automation"
  | "content"
  | "other";

export type TemplateStatus = "draft" | "published" | "deprecated";
export type ListingStatus = "pending" | "approved" | "rejected" | "unlisted";

export interface TemplateParameter {
  name: string;
  label: string;
  description: string;
  type: string;
  default: string | null;
  required: boolean;
  options: string[];
}

export interface Template {
  id: string;
  name: string;
  version: string;
  description: string;
  category: TemplateCategory;
  framework: string;
  config_template: Record<string, unknown>;
  parameters: TemplateParameter[];
  tags: string[];
  author: string;
  team: string;
  status: TemplateStatus;
  use_count: number;
  readme: string;
  created_at: string;
  updated_at: string;
}

export interface MarketplaceListing {
  id: string;
  template_id: string;
  status: ListingStatus;
  submitted_by: string;
  reviewed_by: string | null;
  reject_reason: string | null;
  featured: boolean;
  avg_rating: number;
  review_count: number;
  install_count: number;
  published_at: string | null;
  created_at: string;
  updated_at: string;
  template: Template | null;
}

export interface ListingReview {
  id: string;
  listing_id: string;
  reviewer: string;
  rating: number;
  comment: string;
  created_at: string;
}

export interface MarketplaceBrowseItem {
  listing_id: string;
  template_id: string;
  name: string;
  description: string;
  category: TemplateCategory;
  framework: string;
  tags: string[];
  author: string;
  avg_rating: number;
  review_count: number;
  install_count: number;
  featured: boolean;
  published_at: string | null;
}

// --- Prompt Test types ---

export interface PromptTestRequest {
  prompt_text: string;
  model_id?: string;
  model_name?: string;
  variables: Record<string, string>;
  temperature: number;
  max_tokens: number;
}

export interface PromptTestResponse {
  response_text: string;
  rendered_prompt: string;
  model_name: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  latency_ms: number;
  temperature: number;
}

// --- Sandbox types ---

export interface SandboxExecuteRequest {
  code: string;
  input_json: Record<string, unknown>;
  timeout_seconds: number;
  network_enabled: boolean;
  tool_id?: string;
}

export interface SandboxExecuteResponse {
  execution_id: string;
  output: string;
  stdout: string;
  stderr: string;
  exit_code: number;
  duration_ms: number;
  timed_out: boolean;
  error: string | null;
}

// --- RAG types ---

export interface VectorIndex {
  id: string;
  name: string;
  description: string;
  embedding_model: string;
  entity_model: string;
  chunk_strategy: string;
  chunk_size: number;
  chunk_overlap: number;
  dimensions: number;
  source: string;
  index_type: "vector" | "graph" | "hybrid";
  doc_count: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface IngestJob {
  id: string;
  index_id: string;
  status: string;
  total_files: number;
  processed_files: number;
  total_chunks: number;
  embedded_chunks: number;
  progress_pct: number;
  error: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface RAGSearchHit {
  chunk_id: string;
  text: string;
  source: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface GraphEntity {
  id: string;
  entity: string;
  entity_type: string;
  description: string;
  chunk_ids: string[];
}

export interface GraphRelationship {
  id: string;
  subject_id: string;
  predicate: string;
  object_id: string;
  subject_entity: string;
  object_entity: string;
  chunk_ids: string[];
  weight: number;
}

export interface GraphMetadata {
  index_id: string;
  index_type: string;
  node_count: number;
  edge_count: number;
  entity_types: { type: string; count: number }[];
  top_entities: { entity: string; type: string; chunk_count: number }[];
}

export interface RAGSearchResponse {
  index_id: string;
  query: string;
  top_k: number;
  results: RAGSearchHit[];
  total: number;
}

// --- Memory types ---

export interface MemoryConfig {
  id: string;
  name: string;
  backend_type: string;
  memory_type: string;
  max_messages: number;
  namespace_pattern: string;
  scope: string;
  linked_agents: string[];
  description: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryMessage {
  id: string;
  config_id: string;
  session_id: string;
  agent_id: string | null;
  role: string;
  content: string;
  metadata: Record<string, unknown>;
  timestamp: string;
}

export interface MemoryStats {
  config_id: string;
  backend_type: string;
  memory_type: string;
  message_count: number;
  session_count: number;
  storage_size_bytes: number;
  linked_agent_count: number;
}

export interface ConversationSummary {
  session_id: string;
  agent_id: string | null;
  message_count: number;
  first_message_at: string | null;
  last_message_at: string | null;
}

export interface MemorySearchHit {
  message: MemoryMessage;
  score: number;
  highlight: string;
}

// --- Git / PR types ---

export type PRStatus =
  | "draft"
  | "submitted"
  | "in_review"
  | "approved"
  | "changes_requested"
  | "rejected"
  | "published";

export interface GitDiffEntry {
  file_path: string;
  status: string;
  diff_text: string;
}

export interface GitDiffResponse {
  base: string;
  head: string;
  files: GitDiffEntry[];
  stats: string;
}

export interface GitCommitInfo {
  sha: string;
  author: string;
  date: string;
  message: string;
}

export interface GitPRComment {
  id: string;
  pr_id: string;
  author: string;
  text: string;
  created_at: string;
}

export interface GitPR {
  id: string;
  branch: string;
  title: string;
  description: string;
  submitter: string;
  resource_type: string;
  resource_name: string;
  status: PRStatus;
  reviewer: string | null;
  reject_reason: string | null;
  tag: string | null;
  comments: GitPRComment[];
  commits: GitCommitInfo[];
  diff: GitDiffResponse | null;
  created_at: string;
  updated_at: string;
}

// --- Playground types ---

export interface ConversationMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface PlaygroundToolCall {
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_output: Record<string, unknown>;
  duration_ms: number;
}

export interface PlaygroundChatRequest {
  agent_id: string;
  message: string;
  model_override?: string;
  system_prompt_override?: string;
  conversation_history: ConversationMessage[];
}

export interface PlaygroundChatResponse {
  response: string;
  tool_calls: PlaygroundToolCall[];
  token_count: number;
  cost_estimate: number;
  latency_ms: number;
  model_used: string;
  conversation_id: string;
}

export interface SaveEvalCaseRequest {
  agent_id: string;
  conversation_history: ConversationMessage[];
  assistant_message: string;
  model_used: string;
  tags: string[];
}

export interface SaveEvalCaseResponse {
  eval_case_id: string;
  saved: boolean;
}

// --- Trace types ---

export type TraceStatus = "success" | "error" | "timeout";
export type SpanType = "llm" | "tool" | "agent" | "retrieval" | "custom";

export interface Trace {
  id: string;
  trace_id: string;
  agent_id: string | null;
  agent_name: string;
  status: TraceStatus;
  duration_ms: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  model_name: string | null;
  input_preview: string | null;
  output_preview: string | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface Span {
  id: string;
  trace_id: string;
  span_id: string;
  parent_span_id: string | null;
  name: string;
  span_type: SpanType;
  status: string;
  duration_ms: number;
  input_data: Record<string, unknown> | null;
  output_data: Record<string, unknown> | null;
  model_name: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  metadata: Record<string, unknown>;
  started_at: string;
  ended_at: string | null;
  children: Span[];
}

export interface TraceDetail {
  trace: Trace;
  spans: Span[];
}

export interface TraceMetrics {
  agent_name: string;
  request_count: number;
  error_count: number;
  avg_duration_ms: number;
  p50_duration_ms: number;
  p95_duration_ms: number;
  p99_duration_ms: number;
  total_tokens: number;
  total_cost_usd: number;
  period_days: number;
}

// --- Team types ---

export interface TeamResponse {
  id: string;
  name: string;
  display_name: string;
  description: string;
  member_count: number;
  created_at: string;
}

export interface TeamMemberResponse {
  id: string;
  user_id: string;
  user_email: string;
  user_name: string;
  role: string;
  joined_at: string;
}

export interface TeamDetailResponse {
  id: string;
  name: string;
  display_name: string;
  description: string;
  member_count: number;
  members: TeamMemberResponse[];
  created_at: string;
  updated_at: string;
}

export interface TeamApiKeyResponse {
  id: string;
  provider: string;
  key_hint: string;
  created_by: string;
  created_at: string;
}

// --- Cost types ---

export interface CostEvent {
  id: string;
  trace_id: string | null;
  agent_id: string | null;
  agent_name: string;
  team: string;
  model_name: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  request_type: string;
  created_at: string;
}

export interface CostSummary {
  total_cost: number;
  total_tokens: number;
  request_count: number;
  period: string;
}

export interface CostBreakdownItem {
  name: string;
  cost: number;
  tokens: number;
  requests: number;
}

export interface CostBreakdown {
  by_agent: CostBreakdownItem[];
  by_model: CostBreakdownItem[];
  by_team: CostBreakdownItem[];
}

export interface DailyCostPoint {
  date: string;
  cost: number;
  tokens: number;
  requests: number;
}

export interface CostTrendResponse {
  points: DailyCostPoint[];
  total_cost: number;
  period: string;
}

export interface TopSpender {
  agent_name: string;
  cost: number;
  tokens: number;
  requests: number;
  team: string;
}

export interface CostComparisonResponse {
  model_a: string;
  model_b: string;
  model_a_cost: number;
  model_b_cost: number;
  savings_pct: number;
  sample_tokens: number;
}

export interface Budget {
  id: string;
  team: string;
  monthly_limit_usd: number;
  alert_threshold_pct: number;
  current_month_spend: number;
  pct_used: number;
  is_exceeded: boolean;
  created_at: string;
  updated_at: string;
}

// --- Audit types ---

export interface AuditEvent {
  id: string;
  actor: string;
  actor_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  resource_name: string;
  team: string | null;
  details: Record<string, unknown>;
  ip_address: string | null;
  created_at: string;
}

// --- Lineage types ---

export interface LineageNode {
  id: string;
  name: string;
  type: string;
  status: string;
}

export interface LineageEdge {
  source_id: string;
  target_id: string;
  dependency_type: string;
}

export interface LineageGraphResponse {
  nodes: LineageNode[];
  edges: LineageEdge[];
}

export interface AffectedAgent {
  name: string;
  dependency_type: string;
}

export interface ImpactAnalysisResponse {
  resource_name: string;
  resource_type: string;
  affected_agents: AffectedAgent[];
}

export interface ResourceDependency {
  id: string;
  source_type: string;
  source_id: string;
  source_name: string;
  target_type: string;
  target_id: string;
  target_name: string;
  dependency_type: string;
  created_at: string;
}

// --- Eval types ---

export type EvalRunStatus = "pending" | "running" | "completed" | "failed" | "cancelled";

export interface EvalDataset {
  id: string;
  name: string;
  description: string;
  agent_name: string;
  team: string;
  tags: string[];
  row_count: number;
  version: number;
  format: string;
  created_at: string;
  updated_at: string;
}

export interface EvalDatasetRow {
  id: string;
  dataset_id: string;
  input: Record<string, unknown>;
  expected_output: string;
  tags: string[];
  created_at: string;
}

export interface EvalRun {
  id: string;
  agent_name: string;
  dataset_id: string;
  status: EvalRunStatus;
  config: Record<string, unknown>;
  summary: EvalRunSummary | null;
  started_at: string;
  completed_at: string | null;
  created_at: string;
}

export interface EvalRunSummary {
  overall_score: number;
  metrics: Record<string, EvalMetricSummary>;
  total_rows: number;
  passed_rows: number;
  failed_rows: number;
}

export interface EvalMetricSummary {
  mean: number;
  p95: number;
  min: number;
  max: number;
}

export interface EvalRunResult {
  id: string;
  run_id: string;
  row_id: string;
  input: Record<string, unknown>;
  expected_output: string;
  actual_output: string;
  scores: Record<string, number>;
  latency_ms: number;
  status: string;
  error: string | null;
}

export interface EvalScoreTrend {
  run_id: string;
  overall_score: number;
  metrics: Record<string, number>;
  completed_at: string;
}

export interface EvalComparison {
  run_a: EvalRun;
  run_b: EvalRun;
  deltas: Record<string, number>;
}

// --- Search types ---

export interface SearchResult {
  entity_type: string;
  id: string;
  name: string;
  description: string;
  team: string | null;
  score: number;
}

// --- API functions ---

export const api = {
  agents: {
    list: (params?: {
      team?: string;
      framework?: string;
      status?: AgentStatus;
      page?: number;
      per_page?: number;
    }) => {
      const sp = new URLSearchParams();
      if (params?.team) sp.set("team", params.team);
      if (params?.framework) sp.set("framework", params.framework);
      if (params?.status) sp.set("status", params.status);
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<Agent[]>(`/agents${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => request<Agent>(`/agents/${id}`),
    search: (q: string, page = 1) =>
      request<Agent[]>(`/agents/search?q=${encodeURIComponent(q)}&page=${page}`),
    clone: (id: string, body: { name: string; version: string }) =>
      request<Agent>(`/agents/${id}/clone`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    validate: (yamlContent: string) =>
      request<AgentValidationResult>("/agents/validate", {
        method: "POST",
        body: JSON.stringify({ yaml_content: yamlContent }),
      }),
    fromYaml: (yamlContent: string) =>
      request<Agent>("/agents/from-yaml", {
        method: "POST",
        body: JSON.stringify({ yaml_content: yamlContent }),
      }),
    // Bearer token is resolved server-side from the workspace secrets backend
    // (`agentbreeder/<agent-name>/auth-token`). Callers no longer supply it
    // from the browser. See issue #176.
    invoke: (
      id: string,
      body: { input: string; endpoint_url?: string; session_id?: string }
    ) =>
      request<AgentInvokeResponse>(`/agents/${id}/invoke`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  tools: {
    list: (params?: { tool_type?: string; source?: string; page?: number }) => {
      const sp = new URLSearchParams();
      if (params?.tool_type) sp.set("tool_type", params.tool_type);
      if (params?.source) sp.set("source", params.source);
      if (params?.page) sp.set("page", String(params.page));
      const qs = sp.toString();
      return request<Tool[]>(`/registry/tools${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => request<ToolDetail>(`/registry/tools/${id}`),
    usage: (id: string) => request<ToolUsage[]>(`/registry/tools/${id}/usage`),
    health: (id: string) => request<ToolHealth>(`/registry/tools/${id}/health`),
    create: (body: {
      name: string;
      description?: string;
      tool_type?: string;
      schema_definition?: Record<string, unknown>;
      endpoint?: string;
      source?: string;
    }) =>
      request<Tool>("/registry/tools", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (
      id: string,
      body: {
        name?: string;
        description?: string;
        schema_definition?: Record<string, unknown>;
        endpoint?: string;
      }
    ) =>
      request<ToolDetail>(`/registry/tools/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    run: (id: string, args: Record<string, unknown> = {}) =>
      request<ToolRunResponse>(`/registry/tools/${id}/execute`, {
        method: "POST",
        body: JSON.stringify({ args }),
      }),
  },
  models: {
    list: (params?: { provider?: string; source?: string; page?: number }) => {
      const sp = new URLSearchParams();
      if (params?.provider) sp.set("provider", params.provider);
      if (params?.source) sp.set("source", params.source);
      if (params?.page) sp.set("page", String(params.page));
      const qs = sp.toString();
      return request<Model[]>(`/registry/models${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => request<Model>(`/registry/models/${id}`),
    usage: (id: string) => request<ModelUsage[]>(`/registry/models/${id}/usage`),
    compare: (ids: string[]) =>
      request<Model[]>(`/registry/models/compare?ids=${ids.join(",")}`),
    create: (data: {
      name: string;
      provider: string;
      description?: string;
      context_window?: number | null;
      input_price_per_million?: number | null;
      output_price_per_million?: number | null;
      capabilities?: string[];
    }) =>
      request<Model>("/registry/models", {
        method: "POST",
        body: JSON.stringify({ source: "manual", ...data }),
      }),
    /**
     * Track G — kick off a model lifecycle sync. Deployer role required.
     * Pass an explicit list of provider names to scope the sync, or leave
     * empty for "every configured provider".
     */
    sync: (providers: string[] = []) =>
      request<ModelSyncResult>("/models/sync", {
        method: "POST",
        body: JSON.stringify({ providers }),
      }),
    /** Track G — manually mark a model deprecated. Deployer role required. */
    deprecate: (name: string, replacement?: string) =>
      request<{
        id: string;
        name: string;
        status: string;
        deprecated_at: string | null;
        replacement: string | null;
      }>(`/models/${encodeURIComponent(name)}/deprecate`, {
        method: "POST",
        body: JSON.stringify(replacement ? { replacement } : {}),
      }),
    /** Track G — list endpoint with lifecycle status filtering. */
    listLifecycle: (params?: {
      provider?: string;
      status?: string;
      page?: number;
      per_page?: number;
    }) => {
      const sp = new URLSearchParams();
      if (params?.provider) sp.set("provider", params.provider);
      if (params?.status) sp.set("status", params.status);
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<Model[]>(`/models${qs ? `?${qs}` : ""}`);
    },
  },
  prompts: {
    list: (params?: { team?: string; page?: number }) => {
      const sp = new URLSearchParams();
      if (params?.team) sp.set("team", params.team);
      if (params?.page) sp.set("page", String(params.page));
      const qs = sp.toString();
      return request<Prompt[]>(`/registry/prompts${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => request<Prompt>(`/registry/prompts/${id}`),
    create: (data: { name: string; version: string; content: string; description?: string; team: string }) =>
      request<Prompt>("/registry/prompts", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    update: (id: string, data: { content?: string; description?: string }) =>
      request<Prompt>(`/registry/prompts/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    delete: (id: string) =>
      request<{ deleted: boolean }>(`/registry/prompts/${id}`, {
        method: "DELETE",
      }),
    versions: (id: string) => request<Prompt[]>(`/registry/prompts/${id}/versions`),
    duplicate: (id: string) =>
      request<Prompt>(`/registry/prompts/${id}/duplicate`, {
        method: "POST",
      }),
    versionHistory: (id: string) =>
      request<PromptVersion[]>(`/registry/prompts/${id}/versions/history`),
    createVersion: (
      id: string,
      data: { content: string; change_summary?: string; created_by?: string }
    ) =>
      request<PromptVersion>(`/registry/prompts/${id}/versions/history`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    getVersion: (promptId: string, versionId: string) =>
      request<PromptVersion>(
        `/registry/prompts/${promptId}/versions/history/${versionId}`
      ),
    diffVersions: (promptId: string, v1: string, v2: string) =>
      request<PromptDiff>(
        `/registry/prompts/${promptId}/versions/history/${v1}/diff/${v2}`
      ),
    updateContent: (
      id: string,
      data: { content: string; change_summary?: string; author?: string }
    ) =>
      request<Prompt>(`/registry/prompts/${id}/content`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    test: (data: PromptTestRequest) =>
      request<PromptTestResponse>("/prompts/test", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    render: (
      id: string,
      data: { user_message: string; model: string; temperature?: number }
    ) =>
      request<PromptRenderResponse>(`/registry/prompts/${id}/render`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
  },
  deploys: {
    list: (params?: {
      agent_id?: string;
      status?: DeployJobStatus;
      page?: number;
      per_page?: number;
    }) => {
      const sp = new URLSearchParams();
      if (params?.agent_id) sp.set("agent_id", params.agent_id);
      if (params?.status) sp.set("status", params.status);
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<DeployJob[]>(`/deploys${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => request<DeployJob>(`/deploys/${id}`),
    getDetail: (id: string) => request<DeployJobDetail>(`/deploys/${id}`),
    create: (body: {
      agent_id?: string;
      config_yaml?: string;
      target?: string;
    }) =>
      request<DeployJob>("/deploys", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    cancel: (id: string) =>
      request<{ cancelled: boolean }>(`/deploys/${id}`, { method: "DELETE" }),
    rollback: (id: string) =>
      request<{ rolled_back: boolean }>(`/deploys/${id}/rollback`, {
        method: "POST",
      }),
  },
  providers: {
    list: (params?: {
      provider_type?: ProviderType;
      status?: ProviderStatus;
      page?: number;
    }) => {
      const sp = new URLSearchParams();
      if (params?.provider_type) sp.set("provider_type", params.provider_type);
      if (params?.status) sp.set("status", params.status);
      if (params?.page) sp.set("page", String(params.page));
      const qs = sp.toString();
      return request<Provider[]>(`/providers${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => request<Provider>(`/providers/${id}`),
    create: (body: {
      name: string;
      provider_type: ProviderType;
      base_url?: string;
      config?: Record<string, unknown>;
    }) =>
      request<Provider>("/providers", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (
      id: string,
      body: {
        name?: string;
        base_url?: string;
        status?: ProviderStatus;
        config?: Record<string, unknown>;
      }
    ) =>
      request<Provider>(`/providers/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    delete: (id: string) =>
      request<{ message: string }>(`/providers/${id}`, { method: "DELETE" }),
    test: (id: string) =>
      request<ProviderTestResult>(`/providers/${id}/test`, { method: "POST" }),
    discover: (id: string) =>
      request<ProviderDiscoverResult>(`/providers/${id}/discover`, {
        method: "POST",
      }),
    /**
     * Pull an Ollama model. Returns the raw Response so callers can stream
     * SSE events. The body is a stream of JSON events from Ollama's
     * /api/pull (status / digest / total / completed), terminated by
     * `{"status":"success"}` or `{"status":"error","message":"..."}`.
     */
    pullModel: (id: string, model: string) =>
      fetch(`${BASE}/providers/${id}/pull-model`, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({ model }),
      }),
    catalog: () => request<CatalogProvider[]>("/providers/catalog"),
    catalogStatus: (workspace?: string) =>
      request<Record<string, boolean>>(
        `/providers/catalog/status${
          workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""
        }`,
      ),
  },
  mcpServers: {
    list: (params?: { page?: number; per_page?: number }) => {
      const sp = new URLSearchParams();
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<McpServer[]>(`/mcp-servers${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => request<McpServer>(`/mcp-servers/${id}`),
    create: (body: { name: string; endpoint: string; transport: string }) =>
      request<McpServer>("/mcp-servers", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (
      id: string,
      body: { name?: string; endpoint?: string; transport?: string; status?: string }
    ) =>
      request<McpServer>(`/mcp-servers/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    delete: (id: string) =>
      request<{ deleted: boolean }>(`/mcp-servers/${id}`, { method: "DELETE" }),
    test: (id: string) =>
      request<McpServerTestResult>(`/mcp-servers/${id}/test`, { method: "POST" }),
    discover: (id: string) =>
      request<McpServerDiscoverResult>(`/mcp-servers/${id}/discover`, {
        method: "POST",
      }),
    execute: (id: string, toolName: string, arguments_: Record<string, unknown> = {}) =>
      request<Record<string, unknown>>(
        `/mcp-servers/${id}/execute?tool_name=${encodeURIComponent(toolName)}`,
        {
          method: "POST",
          body: JSON.stringify(arguments_),
        }
      ),
  },
  sandbox: {
    execute: (body: SandboxExecuteRequest) =>
      request<SandboxExecuteResponse>("/tools/sandbox/execute", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  rag: {
    listIndexes: (params?: { page?: number; per_page?: number }) => {
      const sp = new URLSearchParams();
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<VectorIndex[]>(`/rag/indexes${qs ? `?${qs}` : ""}`);
    },
    getIndex: (id: string) => request<VectorIndex>(`/rag/indexes/${id}`),
    createIndex: (body: {
      name: string;
      description?: string;
      embedding_model?: string;
      chunk_strategy?: string;
      chunk_size?: number;
      chunk_overlap?: number;
    }) =>
      request<VectorIndex>("/rag/indexes", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    deleteIndex: (id: string) =>
      request<{ deleted: boolean }>(`/rag/indexes/${id}`, { method: "DELETE" }),
    getGraphMeta: (indexId: string) =>
      request<GraphMetadata>(`/rag/indexes/${indexId}/graph`),
    listEntities: (
      indexId: string,
      params?: { page?: number; per_page?: number; entity_type?: string },
    ) => {
      const qs = new URLSearchParams();
      if (params?.page) qs.set("page", String(params.page));
      if (params?.per_page) qs.set("per_page", String(params.per_page));
      if (params?.entity_type) qs.set("entity_type", params.entity_type);
      const q = qs.toString();
      return request<GraphEntity[]>(`/rag/indexes/${indexId}/entities${q ? `?${q}` : ""}`);
    },
    listRelationships: (
      indexId: string,
      params?: { page?: number; per_page?: number; predicate?: string },
    ) => {
      const qs = new URLSearchParams();
      if (params?.page) qs.set("page", String(params.page));
      if (params?.per_page) qs.set("per_page", String(params.per_page));
      if (params?.predicate) qs.set("predicate", params.predicate);
      const q = qs.toString();
      return request<GraphRelationship[]>(`/rag/indexes/${indexId}/relationships${q ? `?${q}` : ""}`);
    },
    ingest: async (indexId: string, files: File[]) => {
      const formData = new FormData();
      for (const file of files) {
        formData.append("files", file);
      }
      const headers: Record<string, string> = {};
      const token = localStorage.getItem("ag-token");
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch(`${BASE}/rag/indexes/${indexId}/ingest`, {
        method: "POST",
        headers,
        body: formData,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `API error ${res.status}`);
      }
      return res.json() as Promise<ApiResponse<IngestJob>>;
    },
    getIngestJob: (indexId: string, jobId: string) =>
      request<IngestJob>(`/rag/indexes/${indexId}/ingest/${jobId}`),
    search: (body: {
      index_id: string;
      query: string;
      top_k?: number;
      vector_weight?: number;
      text_weight?: number;
    }) =>
      request<RAGSearchResponse>("/rag/search", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  memory: {
    listConfigs: (params?: { page?: number; per_page?: number }) => {
      const sp = new URLSearchParams();
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<MemoryConfig[]>(`/memory/configs${qs ? `?${qs}` : ""}`);
    },
    getConfig: (id: string) => request<MemoryConfig>(`/memory/configs/${id}`),
    createConfig: (body: {
      name: string;
      backend_type?: string;
      memory_type?: string;
      max_messages?: number;
      namespace_pattern?: string;
      scope?: string;
      linked_agents?: string[];
      description?: string;
    }) =>
      request<MemoryConfig>("/memory/configs", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    deleteConfig: (id: string) =>
      request<{ deleted: boolean }>(`/memory/configs/${id}`, { method: "DELETE" }),
    getStats: (id: string) => request<MemoryStats>(`/memory/configs/${id}/stats`),
    storeMessage: (
      configId: string,
      body: {
        session_id: string;
        role: string;
        content: string;
        agent_id?: string;
        metadata?: Record<string, unknown>;
      }
    ) =>
      request<MemoryMessage>(`/memory/configs/${configId}/messages`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    listConversations: (configId: string, params?: { agent_id?: string; page?: number }) => {
      const sp = new URLSearchParams();
      if (params?.agent_id) sp.set("agent_id", params.agent_id);
      if (params?.page) sp.set("page", String(params.page));
      const qs = sp.toString();
      return request<ConversationSummary[]>(
        `/memory/configs/${configId}/conversations${qs ? `?${qs}` : ""}`
      );
    },
    getConversation: (configId: string, sessionId: string) =>
      request<MemoryMessage[]>(
        `/memory/configs/${configId}/conversations/${sessionId}`
      ),
    deleteConversations: (
      configId: string,
      body: { session_id?: string; agent_id?: string; before?: string }
    ) =>
      request<{ deleted_count: number }>(
        `/memory/configs/${configId}/conversations`,
        {
          method: "DELETE",
          body: JSON.stringify(body),
        }
      ),
    search: (configId: string, q: string, limit?: number) => {
      const sp = new URLSearchParams({ q });
      if (limit) sp.set("limit", String(limit));
      return request<MemorySearchHit[]>(
        `/memory/configs/${configId}/search?${sp.toString()}`
      );
    },
  },
  git: {
    listBranches: (user?: string) => {
      const sp = new URLSearchParams();
      if (user) sp.set("user", user);
      const qs = sp.toString();
      return request<{ branches: string[] }>(`/git/branches${qs ? `?${qs}` : ""}`);
    },
    createBranch: (body: { user: string; resource_type: string; resource_name: string }) =>
      request<{ branch: string }>("/git/branches", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    getDiff: (branch: string, base = "main") =>
      request<GitDiffResponse>(`/git/diff/${encodeURIComponent(branch)}?base=${encodeURIComponent(base)}`),
    commit: (body: {
      branch: string;
      file_path: string;
      content: string;
      message: string;
      author: string;
    }) =>
      request<GitCommitInfo>("/git/commits", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    prs: {
      list: (params?: { status?: PRStatus; resource_type?: string }) => {
        const sp = new URLSearchParams();
        if (params?.status) sp.set("status", params.status);
        if (params?.resource_type) sp.set("resource_type", params.resource_type);
        const qs = sp.toString();
        return request<{ prs: GitPR[] }>(`/git/prs${qs ? `?${qs}` : ""}`);
      },
      get: (id: string) => request<GitPR>(`/git/prs/${id}`),
      create: (body: {
        branch: string;
        title: string;
        description?: string;
        submitter: string;
      }) =>
        request<GitPR>("/git/prs", {
          method: "POST",
          body: JSON.stringify(body),
        }),
      approve: (id: string, reviewer: string) =>
        request<GitPR>(`/git/prs/${id}/approve`, {
          method: "POST",
          body: JSON.stringify({ reviewer }),
        }),
      reject: (id: string, reviewer: string, reason: string) =>
        request<GitPR>(`/git/prs/${id}/reject`, {
          method: "POST",
          body: JSON.stringify({ reviewer, reason }),
        }),
      merge: (id: string, tagVersion?: string) =>
        request<GitPR>(`/git/prs/${id}/merge`, {
          method: "POST",
          body: JSON.stringify({ tag_version: tagVersion ?? null }),
        }),
      addComment: (id: string, author: string, text: string) =>
        request<GitPRComment>(`/git/prs/${id}/comments`, {
          method: "POST",
          body: JSON.stringify({ author, text }),
        }),
    },
  },
  playground: {
    chat: (body: PlaygroundChatRequest) =>
      request<PlaygroundChatResponse>("/playground/chat", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    saveEvalCase: (body: SaveEvalCaseRequest) =>
      request<SaveEvalCaseResponse>("/playground/eval-case", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  traces: {
    list: (params?: {
      agent_name?: string;
      status?: TraceStatus;
      date_from?: string;
      date_to?: string;
      min_duration?: number;
      min_cost?: number;
      q?: string;
      page?: number;
      per_page?: number;
    }) => {
      const sp = new URLSearchParams();
      if (params?.agent_name) sp.set("agent_name", params.agent_name);
      if (params?.status) sp.set("status", params.status);
      if (params?.date_from) sp.set("date_from", params.date_from);
      if (params?.date_to) sp.set("date_to", params.date_to);
      if (params?.min_duration != null) sp.set("min_duration", String(params.min_duration));
      if (params?.min_cost != null) sp.set("min_cost", String(params.min_cost));
      if (params?.q) sp.set("q", params.q);
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<Trace[]>(`/traces${qs ? `?${qs}` : ""}`);
    },
    get: (traceId: string) =>
      request<TraceDetail>(`/traces/${encodeURIComponent(traceId)}`),
    create: (body: {
      trace_id: string;
      agent_name: string;
      status?: string;
      duration_ms?: number;
      total_tokens?: number;
      input_tokens?: number;
      output_tokens?: number;
      cost_usd?: number;
      model_name?: string;
      input_preview?: string;
      output_preview?: string;
      error_message?: string;
      metadata?: Record<string, unknown>;
    }) =>
      request<Trace>("/traces", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    createSpan: (
      traceId: string,
      body: {
        span_id: string;
        name: string;
        span_type?: string;
        parent_span_id?: string;
        status?: string;
        duration_ms?: number;
        input_data?: Record<string, unknown>;
        output_data?: Record<string, unknown>;
        model_name?: string;
        input_tokens?: number;
        output_tokens?: number;
        cost_usd?: number;
        metadata?: Record<string, unknown>;
        started_at?: string;
        ended_at?: string;
      }
    ) =>
      request<Span>(`/traces/${encodeURIComponent(traceId)}/spans`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    metrics: (agentName: string, days?: number) => {
      const sp = new URLSearchParams();
      if (days) sp.set("days", String(days));
      const qs = sp.toString();
      return request<TraceMetrics>(
        `/traces/metrics/${encodeURIComponent(agentName)}${qs ? `?${qs}` : ""}`
      );
    },
    delete: (before: string) =>
      request<{ deleted_count: number }>(`/traces?before=${encodeURIComponent(before)}`, {
        method: "DELETE",
      }),
  },
  teams: {
    list: (params?: { page?: number; per_page?: number }) => {
      const sp = new URLSearchParams();
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<TeamResponse[]>(`/teams${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => request<TeamDetailResponse>(`/teams/${id}`),
    create: (body: { name: string; display_name: string; description?: string }) =>
      request<TeamResponse>("/teams", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (id: string, body: { display_name?: string; description?: string }) =>
      request<TeamResponse>(`/teams/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    delete: (id: string) =>
      request<{ deleted: boolean }>(`/teams/${id}`, { method: "DELETE" }),
    addMember: (teamId: string, body: { user_email: string; role?: string }) =>
      request<TeamMemberResponse>(`/teams/${teamId}/members`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    updateMemberRole: (teamId: string, userId: string, body: { role: string }) =>
      request<TeamMemberResponse>(`/teams/${teamId}/members/${userId}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    removeMember: (teamId: string, userId: string) =>
      request<{ removed: boolean }>(`/teams/${teamId}/members/${userId}`, {
        method: "DELETE",
      }),
    listApiKeys: (teamId: string) =>
      request<TeamApiKeyResponse[]>(`/teams/${teamId}/api-keys`),
    setApiKey: (teamId: string, body: { provider: string; api_key: string }) =>
      request<TeamApiKeyResponse>(`/teams/${teamId}/api-keys`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    deleteApiKey: (teamId: string, keyId: string) =>
      request<{ deleted: boolean }>(`/teams/${teamId}/api-keys/${keyId}`, {
        method: "DELETE",
      }),
    testApiKey: (teamId: string, keyId: string) =>
      request<{ success: boolean; error?: string }>(`/teams/${teamId}/api-keys/${keyId}/test`, {
        method: "POST",
      }),
  },
  costs: {
    recordEvent: (body: {
      agent_name: string;
      team: string;
      model_name: string;
      provider: string;
      input_tokens: number;
      output_tokens: number;
      cost_usd: number;
      request_type?: string;
      trace_id?: string;
    }) =>
      request<CostEvent>("/costs/events", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    summary: (params?: { team?: string; agent_name?: string; days?: number }) => {
      const sp = new URLSearchParams();
      if (params?.team) sp.set("team", params.team);
      if (params?.agent_name) sp.set("agent_name", params.agent_name);
      if (params?.days) sp.set("days", String(params.days));
      const qs = sp.toString();
      return request<CostSummary>(`/costs/summary${qs ? `?${qs}` : ""}`);
    },
    breakdown: (params?: { days?: number; group_by?: string }) => {
      const sp = new URLSearchParams();
      if (params?.days) sp.set("days", String(params.days));
      if (params?.group_by) sp.set("group_by", params.group_by);
      const qs = sp.toString();
      return request<CostBreakdown>(`/costs/breakdown${qs ? `?${qs}` : ""}`);
    },
    trend: (params?: { days?: number; team?: string; agent_name?: string }) => {
      const sp = new URLSearchParams();
      if (params?.days) sp.set("days", String(params.days));
      if (params?.team) sp.set("team", params.team);
      if (params?.agent_name) sp.set("agent_name", params.agent_name);
      const qs = sp.toString();
      return request<CostTrendResponse>(`/costs/trend${qs ? `?${qs}` : ""}`);
    },
    topSpenders: (params?: { days?: number; limit?: number }) => {
      const sp = new URLSearchParams();
      if (params?.days) sp.set("days", String(params.days));
      if (params?.limit) sp.set("limit", String(params.limit));
      const qs = sp.toString();
      return request<TopSpender[]>(`/costs/top-spenders${qs ? `?${qs}` : ""}`);
    },
    compare: (body: { model_a: string; model_b: string; sample_tokens?: number }) =>
      request<CostComparisonResponse>("/costs/compare", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  budgets: {
    list: () => request<Budget[]>("/budgets"),
    create: (body: { team: string; monthly_limit_usd: number; alert_threshold_pct?: number }) =>
      request<Budget>("/budgets", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    get: (team: string) => request<Budget>(`/budgets/${encodeURIComponent(team)}`),
    update: (team: string, body: { monthly_limit_usd?: number; alert_threshold_pct?: number }) =>
      request<Budget>(`/budgets/${encodeURIComponent(team)}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
  },
  audit: {
    list: (params?: {
      actor?: string;
      action?: string;
      resource_type?: string;
      resource_name?: string;
      team?: string;
      date_from?: string;
      date_to?: string;
      page?: number;
      per_page?: number;
    }) => {
      const sp = new URLSearchParams();
      if (params?.actor) sp.set("actor", params.actor);
      if (params?.action) sp.set("action", params.action);
      if (params?.resource_type) sp.set("resource_type", params.resource_type);
      if (params?.resource_name) sp.set("resource_name", params.resource_name);
      if (params?.team) sp.set("team", params.team);
      if (params?.date_from) sp.set("date_from", params.date_from);
      if (params?.date_to) sp.set("date_to", params.date_to);
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<AuditEvent[]>(`/audit${qs ? `?${qs}` : ""}`);
    },
    forResource: (resourceType: string, resourceId: string) =>
      request<AuditEvent[]>(`/audit/resource/${resourceType}/${resourceId}`),
    create: (body: {
      actor: string;
      action: string;
      resource_type: string;
      resource_name: string;
      resource_id?: string;
      team?: string;
      details?: Record<string, unknown>;
    }) =>
      request<AuditEvent>("/audit", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  lineage: {
    graph: (resourceType: string, resourceId: string) =>
      request<LineageGraphResponse>(
        `/lineage/${resourceType}/${encodeURIComponent(resourceId)}`
      ),
    impact: (resourceType: string, resourceName: string) =>
      request<ImpactAnalysisResponse>(
        `/lineage/impact/${resourceType}/${encodeURIComponent(resourceName)}`
      ),
    registerDependency: (body: {
      source_type: string;
      source_id: string;
      source_name: string;
      target_type: string;
      target_id: string;
      target_name: string;
      dependency_type: string;
    }) =>
      request<ResourceDependency>("/lineage/dependencies", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    syncAgent: (agentName: string, configSnapshot: Record<string, unknown>) =>
      request<ResourceDependency[]>(`/lineage/sync/${encodeURIComponent(agentName)}`, {
        method: "POST",
        body: JSON.stringify(configSnapshot),
      }),
  },
  evals: {
    datasets: {
      list: (params?: { team?: string; page?: number; per_page?: number }) => {
        const sp = new URLSearchParams();
        if (params?.team) sp.set("team", params.team);
        if (params?.page) sp.set("page", String(params.page));
        if (params?.per_page) sp.set("per_page", String(params.per_page));
        const qs = sp.toString();
        return request<EvalDataset[]>(`/eval/datasets${qs ? `?${qs}` : ""}`);
      },
      get: (id: string) => request<EvalDataset>(`/eval/datasets/${id}`),
      create: (body: {
        name: string;
        description?: string;
        agent_name?: string;
        team?: string;
        tags?: string[];
      }) =>
        request<EvalDataset>("/eval/datasets", {
          method: "POST",
          body: JSON.stringify(body),
        }),
      delete: (id: string) =>
        request<{ deleted: boolean }>(`/eval/datasets/${id}`, { method: "DELETE" }),
      addRows: (id: string, rows: { input: Record<string, unknown>; expected_output: string; tags?: string[] }[]) =>
        request<EvalDatasetRow[]>(`/eval/datasets/${id}/rows`, {
          method: "POST",
          body: JSON.stringify({ rows }),
        }),
      listRows: (id: string, params?: { page?: number; per_page?: number }) => {
        const sp = new URLSearchParams();
        if (params?.page) sp.set("page", String(params.page));
        if (params?.per_page) sp.set("per_page", String(params.per_page));
        const qs = sp.toString();
        return request<EvalDatasetRow[]>(`/eval/datasets/${id}/rows${qs ? `?${qs}` : ""}`);
      },
      importJsonl: (id: string, content: string) =>
        request<{ imported: number }>(`/eval/datasets/${id}/import`, {
          method: "POST",
          body: JSON.stringify({ content }),
        }),
      exportJsonl: (id: string) =>
        request<{ content: string }>(`/eval/datasets/${id}/export`),
    },
    runs: {
      create: (body: {
        agent_name: string;
        dataset_id: string;
        config?: Record<string, unknown>;
      }) =>
        request<EvalRun>("/eval/runs", {
          method: "POST",
          body: JSON.stringify(body),
        }),
      list: (params?: {
        agent_name?: string;
        dataset_id?: string;
        status?: EvalRunStatus;
        page?: number;
        per_page?: number;
      }) => {
        const sp = new URLSearchParams();
        if (params?.agent_name) sp.set("agent_name", params.agent_name);
        if (params?.dataset_id) sp.set("dataset_id", params.dataset_id);
        if (params?.status) sp.set("status", params.status);
        if (params?.page) sp.set("page", String(params.page));
        if (params?.per_page) sp.set("per_page", String(params.per_page));
        const qs = sp.toString();
        return request<EvalRun[]>(`/eval/runs${qs ? `?${qs}` : ""}`);
      },
      get: (id: string) => request<EvalRun & { results: EvalRunResult[] }>(`/eval/runs/${id}`),
      cancel: (id: string) =>
        request<{ cancelled: boolean }>(`/eval/runs/${id}`, { method: "DELETE" }),
    },
    scores: {
      trend: (agent: string, params?: { metric?: string; limit?: number }) => {
        const sp = new URLSearchParams({ agent });
        if (params?.metric) sp.set("metric", params.metric);
        if (params?.limit) sp.set("limit", String(params.limit));
        return request<EvalScoreTrend[]>(`/eval/scores/trend?${sp.toString()}`);
      },
      compare: (runA: string, runB: string) =>
        request<EvalComparison>(`/eval/scores/compare?run_a=${runA}&run_b=${runB}`),
    },
  },
  a2a: {
    list: (params?: { team?: string; page?: number; per_page?: number }) => {
      const sp = new URLSearchParams();
      if (params?.team) sp.set("team", params.team);
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<A2AAgent[]>(`/a2a/agents${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => request<A2AAgent>(`/a2a/agents/${id}`),
    create: (body: {
      name: string;
      endpoint_url: string;
      agent_id?: string;
      agent_card?: Record<string, unknown>;
      capabilities?: string[];
      auth_scheme?: string;
      team?: string;
    }) =>
      request<A2AAgent>("/a2a/agents", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (
      id: string,
      body: {
        endpoint_url?: string;
        agent_card?: Record<string, unknown>;
        capabilities?: string[];
        auth_scheme?: string;
        status?: string;
      }
    ) =>
      request<A2AAgent>(`/a2a/agents/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    delete: (id: string) =>
      request<{ deleted: boolean }>(`/a2a/agents/${id}`, { method: "DELETE" }),
    invoke: (agentName: string, body: { input_message: string; context?: Record<string, unknown> }) =>
      request<A2AInvokeResponse>(`/a2a/invoke?agent_name=${encodeURIComponent(agentName)}`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  templates: {
    list: (params?: { category?: string; framework?: string; status?: string; page?: number; per_page?: number }) => {
      const sp = new URLSearchParams();
      if (params?.category) sp.set("category", params.category);
      if (params?.framework) sp.set("framework", params.framework);
      if (params?.status) sp.set("status", params.status);
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<Template[]>(`/templates${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => request<Template>(`/templates/${id}`),
    create: (body: Omit<Template, "id" | "use_count" | "status" | "created_at" | "updated_at">) =>
      request<Template>("/templates", { method: "POST", body: JSON.stringify(body) }),
    update: (id: string, body: Partial<Template>) =>
      request<Template>(`/templates/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    delete: (id: string) =>
      request<{ deleted: boolean }>(`/templates/${id}`, { method: "DELETE" }),
    instantiate: (id: string, values: Record<string, string>) =>
      request<{ yaml_content: string; agent_name: string }>(`/templates/${id}/instantiate`, {
        method: "POST",
        body: JSON.stringify({ values }),
      }),
  },
  marketplace: {
    browse: (params?: {
      category?: string;
      framework?: string;
      q?: string;
      featured?: boolean;
      sort?: string;
      page?: number;
      per_page?: number;
    }) => {
      const sp = new URLSearchParams();
      if (params?.category) sp.set("category", params.category);
      if (params?.framework) sp.set("framework", params.framework);
      if (params?.q) sp.set("q", params.q);
      if (params?.featured != null) sp.set("featured", String(params.featured));
      if (params?.sort) sp.set("sort", params.sort);
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<MarketplaceBrowseItem[]>(`/marketplace/browse${qs ? `?${qs}` : ""}`);
    },
    getListing: (id: string) => request<MarketplaceListing>(`/marketplace/listings/${id}`),
    submitListing: (templateId: string, submittedBy: string) =>
      request<MarketplaceListing>("/marketplace/listings", {
        method: "POST",
        body: JSON.stringify({ template_id: templateId, submitted_by: submittedBy }),
      }),
    updateListing: (id: string, body: { status?: string; reviewed_by?: string; reject_reason?: string; featured?: boolean }) =>
      request<MarketplaceListing>(`/marketplace/listings/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    addReview: (listingId: string, body: { reviewer: string; rating: number; comment?: string }) =>
      request<ListingReview>(`/marketplace/listings/${listingId}/reviews`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    getReviews: (listingId: string, params?: { page?: number; per_page?: number }) => {
      const sp = new URLSearchParams();
      if (params?.page) sp.set("page", String(params.page));
      if (params?.per_page) sp.set("per_page", String(params.per_page));
      const qs = sp.toString();
      return request<ListingReview[]>(`/marketplace/listings/${listingId}/reviews${qs ? `?${qs}` : ""}`);
    },
    install: (listingId: string) =>
      request<{ installed: boolean }>(`/marketplace/listings/${listingId}/install`, { method: "POST" }),
  },
  search: (q: string) =>
    request<SearchResult[]>(`/registry/search?q=${encodeURIComponent(q)}`),
  health: () => fetch("/health").then((r) => r.json()),
  secrets: {
    workspace: (workspace?: string) =>
      request<WorkspaceBackendInfo>(
        `/secrets/workspace${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`,
      ),
    list: (workspace?: string) =>
      request<SecretSummary[]>(
        `/secrets${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`,
      ),
    create: (
      body: { name: string; value: string; backend?: string },
      workspace?: string,
    ) =>
      request<SecretSummary>(
        `/secrets${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    rotate: (name: string, newValue: string, workspace?: string) =>
      request<SecretSummary>(
        `/secrets/${encodeURIComponent(name)}/rotate${
          workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""
        }`,
        { method: "POST", body: JSON.stringify({ new_value: newValue }) },
      ),
  },
};

// --- Secrets types (Track K) ---

export interface SecretSummary {
  name: string;
  masked_value: string;
  backend: string;
  workspace: string;
  updated_at: string | null;
  mirror_destinations: string[];
}

export interface WorkspaceBackendInfo {
  workspace: string;
  backend: string;
  supported_backends: string[];
}
