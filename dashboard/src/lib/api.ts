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

/**
 * Drop-in replacement for `window.fetch` that attaches the Authorization
 * header and redirects to /login on a 401, mirroring `request()`. Use this
 * for endpoints that don't fit the ApiResponse<T> envelope helpers below.
 */
export async function authFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const res = await fetch(input, {
    ...init,
    headers: { ...getAuthHeaders(), ...(init.headers ?? {}) },
  });
  if (res.status === 401) {
    localStorage.removeItem("ag-token");
    if (window.location.pathname !== "/login") {
      window.location.href = "/login";
    }
    throw new Error("Session expired");
  }
  return res;
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

/**
 * Stream a Server-Sent-Events endpoint using fetch (so the Authorization
 * header works — EventSource cannot set headers). Calls `onEvent(event, data)`
 * for each parsed `event:`/`data:` frame. Resolves when the stream closes.
 */
export async function streamSSE(
  path: string,
  init: RequestInit,
  onEvent: (event: string, data: unknown) => void,
): Promise<void> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { ...getAuthHeaders(), ...((init.headers as Record<string, string> | undefined) ?? {}) },
  });
  if (res.status === 401) {
    localStorage.removeItem("ag-token");
    if (window.location.pathname !== "/login") window.location.href = "/login";
    throw new Error("Session expired");
  }
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `API error ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        let event = "message";
        const dataLines: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        if (dataLines.length === 0) continue;
        const dataStr = dataLines.join("\n");
        let data: unknown = dataStr;
        try {
          data = JSON.parse(dataStr);
        } catch {
          /* keep raw string */
        }
        onEvent(event, data);
      }
    }
  } finally {
    void reader.cancel();
  }
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

export interface AgentInvokeToolCall {
  name: string;
  args: Record<string, unknown>;
  result: string;
  duration_ms: number;
  started_at: string;
}

export interface AgentInvokeResponse {
  output: string;
  session_id: string | null;
  duration_ms: number;
  status_code: number;
  error: string | null;
  // Structured tool-call timeline (#215). Always present; empty when the
  // runtime did not surface tool telemetry naturally.
  history: AgentInvokeToolCall[];
}

export interface AgentVersionEntry {
  id: string;
  version: string;
  config_yaml: string;
  config_snapshot: Record<string, unknown>;
  created_by: string | null;
  created_at: string | null;
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

// --- Orchestration types ---

export type OrchestrationStrategy =
  | "router"
  | "sequential"
  | "parallel"
  | "hierarchical"
  | "supervisor"
  | "fan_out_fan_in";

export type OrchestrationStatus = "draft" | "deployed" | "archived";

export interface OrchestrationAgentRef {
  ref: string;
  routes?: { condition: string; target: string }[];
  fallback?: string;
  endpoint_url?: string;
}

export interface Orchestration {
  id: string;
  name: string;
  version: string;
  description: string;
  team: string | null;
  owner: string | null;
  strategy: OrchestrationStrategy;
  agents_config: Record<string, OrchestrationAgentRef>;
  shared_state_config: Record<string, unknown>;
  deploy_config: Record<string, unknown>;
  status: OrchestrationStatus;
  endpoint_url: string | null;
  config_snapshot: Record<string, unknown>;
  tags: string[];
  layout: Record<string, { x: number; y: number }>;
  created_at: string;
  updated_at: string;
}

// --- Prompt Test types ---

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

// --- Audit types ---

// --- Lineage types ---

// --- Eval types ---

// --- Search types ---

export interface SearchResult {
  entity_type: string;
  id: string;
  name: string;
  description: string;
  team: string | null;
  score: number;
}

// --- Builder / Recommend types ---

export interface RecommendInput {
  business_goal?: string;
  technical_use_case?: string;
  /** subset of "a"|"b"|"c"|"d"|"e": loops, checkpoints, HITL, parallel, none */
  state_flags?: string[];
  /** "aws" | "gcp" | "azure" | "kubernetes" | "local" */
  cloud_preference?: string;
  /** "python" | "typescript" | "none" */
  language_preference?: string;
  /** subset of "a"|"b"|"c"|"d"|"e": unstructured, sql, graph, live-apis, none */
  data_flags?: string[];
  /** "realtime" | "batch" | "event_driven" | "low_volume" */
  scale_profile?: string;
}

export interface Recommendation {
  /** "langgraph" | "crewai" | "claude_sdk" | "openai_agents" | "google_adk" */
  framework: string;
  /** "full_code" | "low_code" */
  code_tier: string;
  model_primary: string;
  /** "vector" | "graph" | "hybrid" | "sql_tool" | "none" */
  rag: string;
  /** "redis" | "postgresql" | "redis+postgresql" | "none" */
  memory: string;
  /** "mcp" | "a2a" | "mcp+a2a" | "none" */
  mcp_a2a: string;
  /** "ecs_fargate" | "cloud_run" | "azure_container_apps" | "docker_compose" */
  deploy_target: string;
  eval_dimensions: string[];
  /** field → one-sentence rationale */
  reasoning: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Chat-to-Build types (Phase 8)
// ---------------------------------------------------------------------------

/** A single chat message in OpenAI format (role + content). */
export interface ChatBuildMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

/**
 * A dependency the agent needs that the user must provide inline before the spec
 * can finish — emitted as a `setup_request` SSE event when the model calls the
 * `request_setup` tool. The captured value goes straight to the secrets / MCP
 * backend; the spec records only the reference (name).
 */
export interface ChatBuildSetupRequest {
  kind: "secret" | "mcp" | "provider";
  name: string;
  reason?: string;
}

/** Result of one POST /builders/chat turn. */
export interface ChatBuildResult {
  /** The assistant's text reply. Empty string when the model went straight to the tool. */
  assistant_message: string;
  /** Set when Claude emitted a complete agent spec via the submit_agent_spec tool. */
  agent_yaml: string | null;
  /** True only when agent_yaml is set AND passed schema validation. */
  valid: boolean;
  /** Schema validation error messages when valid is false and agent_yaml is set. */
  errors: string[];
  /** Set when the model called request_setup instead of submitting the spec. */
  setup_request?: ChatBuildSetupRequest | null;
}

/**
 * A persisted conversational-builder session. Mirrors the backend
 * `BuilderSessionResponse`: chat `history`, the generated `agent_yaml`, any
 * ejected `files` (path → content), and the deploy job id once deployed.
 */
export interface BuilderSession {
  id: string;
  team: string;
  engine: "claude" | "codex";
  agent_yaml: string | null;
  files: Record<string, string>;
  history: { role: string; content: string }[];
}

/** A single file produced/updated by an eject turn (the `file_change` SSE frame). */
export interface BuilderFileChange {
  path: string;
  diff: string;
  content: string;
}

// ---------------------------------------------------------------------------
// Analytics funnel types (W4)
// ---------------------------------------------------------------------------

/** One step of the conversational-builder conversion funnel. */
export interface FunnelStage {
  key: string;
  label: string;
  count: number;
  /** Percentage of sessions lost relative to the previous stage. */
  dropoff_pct: number;
}

/** Per coding-engine quality scorecard (claude vs codex, etc.). */
export interface EngineScorecard {
  engine: string;
  samples: number;
  spec_validity_rate: number;
  deploy_success_rate: number;
  turns_to_spec: number;
  hallucinated_field_rate: number;
}

/** Aggregate builder funnel metrics for a period. */
export interface FunnelMetrics {
  period: string;
  time_to_first_deploy_p50_s: number | null;
  time_to_first_deploy_p90_s: number | null;
  stages: FunnelStage[];
  engines: EngineScorecard[];
}

/**
 * Fire-and-forget POST of an analytics event to the ingest endpoint. Never
 * throws and never blocks the caller — failures are swallowed. PII-free: only
 * the event name, an optional engine tag, and non-sensitive props are sent.
 * Used by the `track()` seam in `analytics.ts`.
 */
export function ingestAnalytics(
  event: string,
  props: Record<string, unknown> = {},
): void {
  try {
    void fetch(`${BASE}/analytics/events`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ event, engine: props.engine ?? null, props }),
      keepalive: true,
    }).catch(() => {
      /* analytics is best-effort — never surface failures */
    });
  } catch {
    /* fetch unavailable (SSR) — ignore */
  }
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
    update: (
      id: string,
      body: {
        version?: string;
        description?: string;
        endpoint_url?: string;
        status?: AgentStatus;
        tags?: string[];
      }
    ) =>
      request<Agent>(`/agents/${id}`, {
        method: "PUT",
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
    versions: (id: string) =>
      request<AgentVersionEntry[]>(`/agents/${id}/versions`),
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
    render: (
      id: string,
      data: { user_message: string; model: string; temperature?: number }
    ) =>
      request<PromptRenderResponse>(`/registry/prompts/${id}/render`, {
        method: "POST",
        body: JSON.stringify(data),
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
    pullModel: (id: string, model: string) =>
      fetch(`${BASE}/providers/${id}/pull-model`, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({ model }),
      }),
    /**
     * Auto-detect a local Ollama instance, register it as a provider, and
     * discover + register all locally-available models.
     * Maps to `POST /api/v1/providers/detect-ollama`.
     */
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
  playground: {
    chat: (body: PlaygroundChatRequest) =>
      request<PlaygroundChatResponse>("/playground/chat", {
        method: "POST",
        body: JSON.stringify(body),
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
  builders: {
    recommend: (input: RecommendInput) =>
      request<Recommendation>("/builders/recommend", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    /** Drive one turn of the conversational agent builder (BYO Claude key).
     *  The key is never sent from the browser — it is read server-side from
     *  the workspace secrets backend. */
    chat: (messages: ChatBuildMessage[]) =>
      request<ChatBuildResult>("/builders/chat", {
        method: "POST",
        body: JSON.stringify({ messages }),
      }),
    /** Streaming variant of `chat` — uses SSE so tokens arrive incrementally.
     *  Calls `onEvent("token", { text })` per chunk and `onEvent("done", { agent_yaml })` at end. */
    chatStream: (
      messages: ChatBuildMessage[],
      onEvent: (event: string, data: unknown) => void,
    ) =>
      streamSSE(
        "/builders/chat/stream",
        { method: "POST", body: JSON.stringify({ messages }) },
        onEvent,
      ),
  },
  builderSessions: {
    create: (engine: "claude" | "codex" = "claude") =>
      request<BuilderSession>("/builder/sessions", {
        method: "POST",
        body: JSON.stringify({ engine }),
      }),
    get: (id: string) => request<BuilderSession>(`/builder/sessions/${id}`),
    list: () => request<BuilderSession[]>("/builder/sessions"),
    sendMessage: (
      id: string,
      content: string,
      onEvent: (event: string, data: unknown) => void,
    ) =>
      streamSSE(
        `/builder/sessions/${id}/messages`,
        { method: "POST", body: JSON.stringify({ content }) },
        onEvent,
      ),
    eject: (
      id: string,
      instruction: string,
      onEvent: (event: string, data: unknown) => void,
      engine?: "claude" | "codex",
    ) =>
      streamSSE(
        `/builder/sessions/${id}/eject`,
        { method: "POST", body: JSON.stringify({ instruction, engine }) },
        onEvent,
      ),
  },
  analytics: {
    funnel: (period: string = "7d") =>
      request<FunnelMetrics>(`/analytics/funnel?period=${encodeURIComponent(period)}`),
  },
  secrets: {
    workspace: (workspace?: string) =>
      request<WorkspaceBackendInfo>(
        `/secrets/workspace${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`,
      ),
    setBackend: (
      body: { backend: string; options?: Record<string, string> },
      workspace?: string,
    ) =>
      request<WorkspaceBackendInfo>(
        `/secrets/workspace${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`,
        { method: "PUT", body: JSON.stringify(body) },
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
  deployments: {
    cloudRequirements: (cloud: "aws" | "gcp" | "azure", mode: "simple" | "full" = "simple") =>
      request<{
        fields: { name: string; required: boolean; description: string }[];
      }>(`/deployments/cloud-requirements/${cloud}?mode=${mode}`),

    validateInfra: (body: {
      cloud: "aws" | "gcp" | "azure";
      region: string;
      team_id: string;
      mode: "simple" | "full";
      fields: Record<string, string>;
    }) =>
      request<{
        valid: boolean;
        checks: { resource: string; status: string; detail: string }[];
      }>("/deployments/validate-infra", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),

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
