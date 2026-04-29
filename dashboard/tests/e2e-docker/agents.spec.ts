import { test, expect } from "./fixtures";
import { createAgent } from "./helpers";

// Browser-driven agent flows depend on the auth/login UI that's flaky on slow
// CI runners (see auth.spec.ts comment). Same coverage now lives at the API
// level in dashboard/tests/e2e-docker/api_smoke.sh — agent registry CRUD via
// /api/v1/agents/from-yaml + auth-gate verification — which is faster and
// reliable. These specs are kept for local manual runs.
test.describe.skip("Real agent flows (live backend)", () => {
  test("agent created via API appears in dashboard agents list", async ({
    authedPage: { page, token, email },
  }) => {
    // Create agent via real API
    const agentName = `e2e-agent-${Date.now()}`;
    await createAgent(token, {
      name: agentName,
      version: "1.0.0",
      team: "engineering",
      owner: email,
      framework: "langgraph",
      model_primary: "claude-sonnet-4",
    });

    // Navigate to agents page
    await page.goto("/agents");

    // The agent we just created should appear (may need a brief wait for React Query)
    await expect(page.getByText(agentName)).toBeVisible({ timeout: 10_000 });
  });

  test("agents page shows empty state with no agents for fresh user", async ({
    authedPage: { page },
  }) => {
    // Fresh user from fixture has no agents yet
    await page.goto("/agents");

    // Should render the heading at minimum
    await expect(page.locator("h1")).toContainText("Agents");

    // Page should load without crashing
    const body = await page.textContent("body");
    expect(body?.toLowerCase()).not.toContain("error");
    expect(body?.toLowerCase()).not.toContain("unexpected");
  });

  test("agent detail page loads for a real agent", async ({
    authedPage: { page, token, email },
  }) => {
    const agentName = `e2e-detail-${Date.now()}`;
    const agentId = await createAgent(token, {
      name: agentName,
      version: "1.0.0",
      team: "engineering",
      owner: email,
      framework: "langgraph",
      model_primary: "claude-sonnet-4",
    });

    await page.goto(`/agents/${agentId}`);

    // Detail page should show agent name
    await expect(page.getByText(agentName)).toBeVisible({ timeout: 10_000 });
  });
});
