/**
 * E2E tests for ModelPathChooser on the /models page (Phase 3).
 *
 * These tests mock all backend routes so they run without a live API server.
 * authedPage fixture (from fixtures.ts) seeds localStorage with a fake JWT
 * and mocks /api/v1/auth/me so route guards pass.
 *
 * NOTE: Step 3 (manual browser verification against the live dev server) is
 * deferred to the controller — these specs exercise the mocked routes only.
 */
import { test, expect, apiOk } from "./fixtures";

// ---------------------------------------------------------------------------
// Shared route mocks
// ---------------------------------------------------------------------------

const EMPTY_MODELS_RESPONSE = apiOk([], 0);
const EMPTY_CATALOG_RESPONSE = apiOk([]);
const EMPTY_CATALOG_STATUS_RESPONSE = apiOk({});
const EMPTY_PROVIDERS_RESPONSE = apiOk([]);

async function mockModelsPageRoutes(page: import("@playwright/test").Page) {
  // Skip the welcome tour — it intercepts pointer events in tests.
  // The tour hook reads localStorage key "ag-tour-completed-v1"; pre-seeding it
  // here (before the first navigation) prevents the modal from appearing.
  await page.addInitScript(() => {
    // The use-tour hook compares against "1" (not "true")
    window.localStorage.setItem("ag-tour-completed-v1", "1");
  });

  // Model registry table
  await page.route("**/api/v1/registry/models**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: EMPTY_MODELS_RESPONSE }),
  );
  // Provider catalog
  await page.route("**/api/v1/providers/catalog**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: EMPTY_CATALOG_RESPONSE }),
  );
  // Catalog status
  await page.route("**/api/v1/providers/catalog/status**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: EMPTY_CATALOG_STATUS_RESPONSE }),
  );
  // Provider list (used elsewhere on the page)
  await page.route("**/api/v1/providers**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: EMPTY_PROVIDERS_RESPONSE }),
  );
  // Model sync
  await page.route("**/api/v1/models/sync**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: apiOk({ synced: 0 }) }),
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("ModelPathChooser — /models page", () => {
  test("chooser renders with three path cards", async ({ authedPage: page }) => {
    await mockModelsPageRoutes(page);
    await page.goto("/models");

    await expect(page.getByTestId("model-path-chooser")).toBeVisible();

    await expect(page.getByTestId("path-card-local")).toBeVisible();
    await expect(page.getByTestId("path-card-gateway")).toBeVisible();
    await expect(page.getByTestId("path-card-direct")).toBeVisible();
  });

  test("gateway path is selected by default", async ({ authedPage: page }) => {
    await mockModelsPageRoutes(page);
    await page.goto("/models");

    await expect(page.getByTestId("path-card-gateway")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("path-card-local")).toHaveAttribute("aria-pressed", "false");
    await expect(page.getByTestId("path-card-direct")).toHaveAttribute("aria-pressed", "false");
  });

  test("gateway panel renders when gateway card is selected", async ({ authedPage: page }) => {
    await mockModelsPageRoutes(page);
    await page.goto("/models");

    await expect(page.getByTestId("gateway-path-panel")).toBeVisible();
    await expect(page.getByTestId("local-path-panel")).toHaveCount(0);
    await expect(page.getByTestId("direct-path-panel")).toHaveCount(0);
  });

  test("clicking Local card switches to local panel", async ({ authedPage: page }) => {
    await mockModelsPageRoutes(page);
    await page.goto("/models");

    await page.getByTestId("path-card-local").click();

    await expect(page.getByTestId("local-path-panel")).toBeVisible();
    await expect(page.getByTestId("gateway-path-panel")).toHaveCount(0);
  });

  test("clicking Direct card switches to direct panel", async ({ authedPage: page }) => {
    await mockModelsPageRoutes(page);
    await page.goto("/models");

    await page.getByTestId("path-card-direct").click();

    await expect(page.getByTestId("direct-path-panel")).toBeVisible();
    await expect(page.getByTestId("gateway-path-panel")).toHaveCount(0);
  });

  test("Local panel shows Detect Ollama button", async ({ authedPage: page }) => {
    await mockModelsPageRoutes(page);
    await page.goto("/models");

    await page.getByTestId("path-card-local").click();

    await expect(page.getByTestId("local-detect-btn")).toBeVisible();
    await expect(page.getByTestId("local-detect-btn")).toHaveText(/detect ollama/i);
  });

  test("Local panel — successful Ollama detection shows discovered models", async ({
    authedPage: page,
  }) => {
    await mockModelsPageRoutes(page);

    // Mock the detect-ollama endpoint
    await page.route("**/api/v1/providers/detect-ollama**", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: {
            provider: { id: "p-local", name: "Ollama (local)", provider_type: "ollama" },
            models: [
              { id: "llama3.2", name: "llama3.2", context_window: null, max_output_tokens: null, input_price_per_million: null, output_price_per_million: null, capabilities: [] },
              { id: "mistral", name: "mistral", context_window: null, max_output_tokens: null, input_price_per_million: null, output_price_per_million: null, capabilities: [] },
            ],
            created: true,
          },
          meta: { page: 1, per_page: 20, total: 0 },
          errors: [],
        }),
      }),
    );

    await page.goto("/models");
    await page.getByTestId("path-card-local").click();
    await page.getByTestId("local-detect-btn").click();

    await expect(page.getByTestId("local-detect-result")).toBeVisible({ timeout: 5000 });
    await expect(page.getByText("llama3.2")).toBeVisible();
    await expect(page.getByText("mistral")).toBeVisible();
  });

  test("Local panel — failed detection shows error message", async ({
    authedPage: page,
  }) => {
    await mockModelsPageRoutes(page);

    // Mock detect-ollama to return an error
    await page.route("**/api/v1/providers/detect-ollama**", (r) =>
      r.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Connection refused" }),
      }),
    );

    await page.goto("/models");
    await page.getByTestId("path-card-local").click();
    await page.getByTestId("local-detect-btn").click();

    await expect(page.getByTestId("local-detect-error")).toBeVisible({ timeout: 5000 });
  });

  test("Direct panel — Settings link points to /settings", async ({ authedPage: page }) => {
    await mockModelsPageRoutes(page);
    await page.goto("/models");

    await page.getByTestId("path-card-direct").click();

    const link = page.getByTestId("direct-settings-link");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "/settings");
  });

  test("question heading is visible", async ({ authedPage: page }) => {
    await mockModelsPageRoutes(page);
    await page.goto("/models");

    await expect(page.getByText("How do you want to run models?")).toBeVisible();
  });

  test("Sync button is rendered inside the chooser", async ({ authedPage: page }) => {
    await mockModelsPageRoutes(page);
    await page.goto("/models");

    const chooser = page.getByTestId("model-path-chooser");
    const syncBtn = page.getByTestId("models-sync-btn");
    await expect(syncBtn).toBeVisible();
    // Sync button must be a descendant of the chooser container
    await expect(chooser).toContainText("Sync");
  });

  test("existing model registry table and filter pills still render below chooser", async ({
    authedPage: page,
  }) => {
    await mockModelsPageRoutes(page);
    await page.goto("/models");

    // Wait for the chooser to be present — confirms the page has loaded
    await expect(page.getByTestId("model-path-chooser")).toBeVisible();

    // Provider filter pills are rendered as plain <button> elements with pill-style classes.
    // Use text locators since these are lowercase/mixed-case text content buttons.
    await expect(page.locator("button", { hasText: "All" }).first()).toBeVisible();

    // Filter input from the model registry table
    await expect(page.locator('input[placeholder="Filter models..."]')).toBeVisible();
  });
});
