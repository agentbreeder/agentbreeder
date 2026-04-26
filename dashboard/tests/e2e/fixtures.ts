import { test as base, type Page } from "@playwright/test";

/** Seed localStorage with a fake token and mock /me endpoint so route guards pass. */
async function setupAuth(page: Page) {
  // Inject the token before the first navigation so RequireAuth never sees an
  // unauthenticated state — avoids a /login round-trip per test.
  await page.addInitScript(() => {
    window.localStorage.setItem("ag-token", "test-token");
  });

  // Mock /me to return a valid user
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          id: "11111111-1111-1111-1111-111111111111",
          email: "test@test.com",
          name: "Test User",
          role: "viewer",
          team: "engineering",
          is_active: true,
          created_at: "2026-03-09T00:00:00Z",
        },
        meta: { page: 1, per_page: 20, total: 0 },
        errors: [],
      }),
    }),
  );

  // Catch-all for git/prs — many pages render an approvals badge that fetches
  // this endpoint. Without a mock the request leaks through Vite's proxy to the
  // non-existent backend and pollutes CI logs with ECONNREFUSED noise.
  await page.route("**/api/v1/git/prs**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: { prs: [], total: 0, page: 1, per_page: 20 },
        meta: { page: 1, per_page: 20, total: 0 },
        errors: [],
      }),
    }),
  );
}

/** Extended test fixture that pre-authenticates every test. */
export const test = base.extend<{ authedPage: Page }>({
  authedPage: async ({ page }, use) => {
    await setupAuth(page);
    await use(page);
  },
});

export { expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Shared mock data factories
// ---------------------------------------------------------------------------

export const MOCK_AGENT = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  name: "customer-support-agent",
  version: "1.0.0",
  description: "Handles support tickets",
  team: "engineering",
  owner: "alice@test.com",
  framework: "langgraph",
  model_primary: "claude-sonnet-4",
  model_fallback: "gpt-4o",
  status: "running",
  tags: ["support"],
  guardrails: [],
  is_favorite: false,
  created_at: "2026-03-01T00:00:00Z",
  updated_at: "2026-03-10T00:00:00Z",
  deploy: { cloud: "aws", runtime: "ecs-fargate", region: "us-east-1" },
};

export const MOCK_PROVIDER = {
  id: "prov-0001",
  name: "OpenAI Production",
  type: "openai",
  base_url: "https://api.openai.com/v1",
  status: "healthy",
  is_default: true,
  created_at: "2026-03-01T00:00:00Z",
};

export const MOCK_PR = {
  id: "pr-0001",
  title: "Update customer-support-agent prompts",
  resource_type: "agent",
  resource_name: "customer-support-agent",
  status: "submitted",
  submitter: "alice@test.com",
  reviewer: null,
  description: "Improved tone and accuracy",
  comments: [],
  created_at: "2026-03-10T00:00:00Z",
  updated_at: "2026-03-10T00:00:00Z",
};

export const MOCK_TRACE = {
  id: "trace-0001",
  agent_id: "aaaaaaaa-0000-0000-0000-000000000001",
  agent_name: "customer-support-agent",
  status: "success",
  duration_ms: 1234,
  token_count: 542,
  cost_usd: 0.0023,
  model: "claude-sonnet-4",
  started_at: "2026-03-10T10:00:00Z",
  ended_at: "2026-03-10T10:00:01Z",
  span_count: 5,
  error_message: null,
};

export const MOCK_TEAM = {
  id: "team-0001",
  name: "engineering",
  display_name: "Engineering",
  description: "Core engineering team",
  member_count: 5,
  agent_count: 3,
  cost_this_month: 12.5,
  created_at: "2026-01-01T00:00:00Z",
};

export const MOCK_TEMPLATE = {
  id: "tmpl-0001",
  name: "customer-support-starter",
  display_name: "Customer Support Starter",
  description: "A starter template for customer support agents",
  framework: "langgraph",
  category: "support",
  tags: ["support", "starter"],
  star_count: 42,
  download_count: 200,
  author: "agentbreeder-team",
  created_at: "2026-01-01T00:00:00Z",
};

export const MOCK_EVAL_RUN = {
  id: "run-0001",
  dataset_id: "ds-0001",
  agent_id: "aaaaaaaa-0000-0000-0000-000000000001",
  agent_name: "customer-support-agent",
  status: "completed",
  pass_count: 45,
  fail_count: 5,
  total_count: 50,
  pass_rate: 0.9,
  avg_latency_ms: 800,
  total_cost_usd: 0.25,
  started_at: "2026-03-10T09:00:00Z",
  completed_at: "2026-03-10T09:05:00Z",
};

/** Shorthand to fulfill a route with standard API response shape. */
export function apiOk(data: unknown, total = 0): string {
  return JSON.stringify({
    data,
    meta: { page: 1, per_page: 20, total },
    errors: [],
  });
}
