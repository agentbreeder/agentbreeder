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

// --- Model types ---

export interface Model {
  id: string;
  name: string;
  provider: string;
  description: string;
  status: string;
  source: string;
  created_at: string;
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
  search: (q: string) =>
    request<SearchResult[]>(`/registry/search?q=${encodeURIComponent(q)}`),
  health: () => fetch("/health").then((r) => r.json()),
};
