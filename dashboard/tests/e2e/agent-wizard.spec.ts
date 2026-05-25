/**
 * E2E tests for the Agent Wizard (/agents/new)
 *
 * All backend routes are mocked via page.route(). Tests do NOT require a
 * running API server.
 *
 * @deferred-browser-verification Task 7 Step 3 (manual check) is done by the
 * controller after this spec is committed. The spec itself runs in headless
 * Playwright.
 */
import { test, expect, apiOk, MOCK_AGENT } from "./fixtures";

// ---------------------------------------------------------------------------
// Shared mock helpers
// ---------------------------------------------------------------------------

const MOCK_RECOMMENDATION = {
  framework: "langgraph",
  code_tier: "low_code",
  model_primary: "claude-sonnet-4",
  rag: "vector",
  memory: "redis",
  mcp_a2a: "none",
  deploy_target: "ecs_fargate",
  eval_dimensions: ["latency", "accuracy"],
  reasoning: {
    framework: "LangGraph handles stateful workflows well",
    model_primary: "Claude Sonnet 4 is the best general model",
    rag: "Vector search is ideal for document retrieval",
    memory: "Redis provides low-latency state storage",
    mcp_a2a: "No MCP/A2A needed for this use case",
    eval_dimensions: "Latency and accuracy are key metrics",
  },
};

async function mockWizardAPIs(page: Parameters<typeof test.fn>[0]["page"]) {
  // Playwright route handlers use LIFO — last registered wins for a given URL.
  // Register broad catch-alls FIRST (lowest priority), specifics LAST (highest).

  // Broad catch-all: agents list / search
  await page.route("**/api/v1/agents**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk([MOCK_AGENT], 1),
    }),
  );

  // Specific: builders recommend
  await page.route("**/api/v1/builders/recommend", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk(MOCK_RECOMMENDATION),
    }),
  );

  // Specific: validate — registered after catch-all so it wins for this URL
  await page.route("**/api/v1/agents/validate", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk({ valid: true, errors: [], warnings: [] }),
    }),
  );

  // Specific: from-yaml
  await page.route("**/api/v1/agents/from-yaml", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk({ ...MOCK_AGENT, id: "wizard-created-agent-id" }),
    }),
  );

  // Specific: agent detail after navigation
  await page.route("**/api/v1/agents/wizard-created-agent-id", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk({ ...MOCK_AGENT, id: "wizard-created-agent-id", name: "my-test-agent" }),
    }),
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Agent Wizard (/agents/new)", () => {
  test("renders the wizard with 4-step StepIndicator and 'Create an agent' heading", async ({
    authedPage: page,
  }) => {
    await mockWizardAPIs(page);
    await page.goto("/agents/new");

    await expect(page.locator("h1")).toContainText("Create an agent");
    await expect(page.getByRole("list", { name: /Wizard steps/i })).toBeVisible();

    // All 4 step buttons
    await expect(page.getByRole("button", { name: /Step 1: Goal/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Step 2: Workflow/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Step 3: Stack/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Step 4: Create/i })).toBeVisible();
  });

  test("step 1 — Next button disabled until businessGoal is filled", async ({
    authedPage: page,
  }) => {
    await mockWizardAPIs(page);
    await page.goto("/agents/new");

    const nextBtn = page.getByTestId("nextBtn");
    await expect(nextBtn).toBeDisabled();

    await page.getByTestId("businessGoal").fill(
      "Automatically triage and respond to customer support tickets",
    );
    await expect(nextBtn).not.toBeDisabled();
  });

  test("step 1 → step 2 → step 3: full recommendation flow", async ({
    authedPage: page,
  }) => {
    await mockWizardAPIs(page);
    await page.goto("/agents/new");

    // Step 1: fill goal and advance
    await page.getByTestId("businessGoal").fill("Handle support tickets automatically");
    await page.getByTestId("nextBtn").click();

    // Step 2: fill workflow and advance
    await expect(page.getByTestId("workflow")).toBeVisible();
    await page.getByTestId("workflow").fill("1. Receive ticket\n2. Classify\n3. Respond");
    await page.getByTestId("nextBtn").click();

    // Step 3: recommendation fires, editable fields seeded
    await expect(page.getByTestId("framework")).toBeVisible();
    // The recommend API call should populate fields from MOCK_RECOMMENDATION
    await expect(page.getByTestId("framework")).toHaveValue("langgraph");

    // Guidance cards visible — use testid to avoid ambiguity with sidebar nav
    await expect(page.getByTestId("guidance-rag-knowledge")).toBeVisible();
    await expect(page.getByTestId("guidance-memory")).toBeVisible();
  });

  test("step 3 — editable framework field updates on change", async ({
    authedPage: page,
  }) => {
    await mockWizardAPIs(page);
    await page.goto("/agents/new");

    // Navigate to step 3
    await page.getByTestId("businessGoal").fill("Build a data pipeline agent");
    await page.getByTestId("nextBtn").click();
    await page.getByTestId("workflow").fill("1. Extract\n2. Transform\n3. Load");
    await page.getByTestId("nextBtn").click();

    // Wait for recommendation to load
    await expect(page.getByTestId("framework")).toBeVisible();

    // Change the framework
    await page.getByTestId("framework").selectOption("crewai");
    await expect(page.getByTestId("framework")).toHaveValue("crewai");
  });

  test("step 4 — Create button calls validate + from-yaml and navigates", async ({
    authedPage: page,
  }) => {
    await mockWizardAPIs(page);

    // Track API calls
    const apiCalls: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/api/v1/agents/")) {
        apiCalls.push(req.url());
      }
    });

    await page.goto("/agents/new");

    // Step 1
    await page.getByTestId("businessGoal").fill("Handle support tickets");
    await page.getByTestId("nextBtn").click();

    // Step 2
    await page.getByTestId("workflow").fill("1. Receive\n2. Respond");
    await page.getByTestId("nextBtn").click();

    // Step 3 — advance to step 4 once recommendation loads
    await expect(page.getByTestId("framework")).toBeVisible();
    await page.getByTestId("nextBtn").click();

    // Step 4 — fill required fields
    await expect(page.getByTestId("agentName")).toBeVisible();
    await page.getByTestId("agentName").fill("my-test-agent");
    // version, team have defaults — owner should be prefilled from auth
    const ownerField = page.getByTestId("owner");
    // Clear and fill in case prefill didn't fire in headless
    await ownerField.fill("test@test.com");

    // Click Create
    const createBtn = page.getByTestId("createAgent");
    await expect(createBtn).not.toBeDisabled();
    await createBtn.click();

    // Should navigate to /agents/wizard-created-agent-id
    await expect(page).toHaveURL(/\/agents\/wizard-created-agent-id/);
  });

  test("step 4 — shows validation errors when validate returns invalid", async ({
    authedPage: page,
  }) => {
    await mockWizardAPIs(page);

    // Override validate to return errors
    await page.route("**/api/v1/agents/validate", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: apiOk({
          valid: false,
          errors: [{ path: "name", message: "Name must be a valid slug", suggestion: "" }],
          warnings: [],
        }),
      }),
    );

    await page.goto("/agents/new");

    // Navigate to step 4
    await page.getByTestId("businessGoal").fill("Handle support tickets");
    await page.getByTestId("nextBtn").click();
    await page.getByTestId("workflow").fill("1. Receive\n2. Respond");
    await page.getByTestId("nextBtn").click();
    await expect(page.getByTestId("framework")).toBeVisible();
    await page.getByTestId("nextBtn").click();

    await page.getByTestId("agentName").fill("my-test-agent");
    await page.getByTestId("owner").fill("test@test.com");

    await page.getByTestId("createAgent").click();

    // Validation errors panel should appear
    await expect(page.getByTestId("validation-errors")).toBeVisible();
    await expect(page.getByTestId("validation-errors")).toContainText("Name must be a valid slug");
  });
});
