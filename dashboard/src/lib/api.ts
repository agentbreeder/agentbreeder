/** API client for Agent Garden backend. */

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
  created_at: string;
  updated_at: string | null;
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
  chunk_strategy: string;
  chunk_size: number;
  chunk_overlap: number;
  dimensions: number;
  source: string;
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
  search: (q: string) =>
    request<SearchResult[]>(`/registry/search?q=${encodeURIComponent(q)}`),
  health: () => fetch("/health").then((r) => r.json()),
};
