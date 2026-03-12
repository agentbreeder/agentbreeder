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

export type AgentStatus = "deploying" | "running" | "stopped" | "failed";

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
  search: (q: string) =>
    request<SearchResult[]>(`/registry/search?q=${encodeURIComponent(q)}`),
  health: () => fetch("/health").then((r) => r.json()),
};
