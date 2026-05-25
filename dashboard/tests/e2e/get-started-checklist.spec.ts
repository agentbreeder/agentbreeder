/**
 * E2E spec for the Phase 4 "Get Started" onboarding checklist.
 *
 * Verifies:
 *   1. Checklist renders on Home when no agents exist, with "Create your first
 *      agent" CTA linking to /agents/builder.
 *   2. Welcome tour modal does NOT auto-open (no modal visible at / without
 *      seeding the tour-completed key — checklist is now the first-run guide).
 *   3. Dismiss control hides the checklist.
 *   4. Checklist is absent when dismissed flag is pre-set.
 *
 * Note: These specs require the dev server (npm run dev) — run with:
 *   npm run test:e2e -- get-started-checklist
 *
 * If the dev server is not available in CI these tests will be skipped via
 * the webServer config in playwright.config.ts.
 */
import { test, expect, apiOk } from "./fixtures";

// ---------------------------------------------------------------------------
// Shared route mocks for Home
// ---------------------------------------------------------------------------

// Helper: set up all API mocks needed for the Home page checklist.
async function setupHomeRoutes(page: import("@playwright/test").Page) {
  // Agents — empty
  await page.route("**/api/v1/agents**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk([], 0),
    }),
  );
  // Providers — empty (no active providers)
  await page.route("**/api/v1/providers**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk([], 0),
    }),
  );
  // Deploys — empty
  await page.route("**/api/v1/deploys**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk([], 0),
    }),
  );
  // Tools — empty (home stats)
  await page.route("**/api/v1/tools**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk([], 0),
    }),
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("checklist renders on Home with no agents", async ({ authedPage: page }) => {
  await setupHomeRoutes(page);
  await page.goto("/");

  // The checklist panel heading should be visible.
  await expect(
    page.getByText("Welcome — let's ship your first agent"),
  ).toBeVisible();

  // All four step testids should be present in the DOM.
  await expect(page.getByTestId("step-connect-model")).toBeVisible();
  await expect(page.getByTestId("step-create-agent")).toBeVisible();
  await expect(page.getByTestId("step-test-playground")).toBeVisible();
  await expect(page.getByTestId("step-deploy")).toBeVisible();
});

test("'Create your first agent' step is active and CTA links to /agents/builder when providers exist", async ({
  authedPage: page,
}) => {
  // Providers: 1 active → step 1 done, step 2 (create-agent) active.
  await page.route("**/api/v1/providers**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk([{ id: "p1", name: "OpenAI", status: "active" }], 1),
    }),
  );
  await page.route("**/api/v1/agents**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk([], 0),
    }),
  );
  await page.route("**/api/v1/deploys**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk([], 0),
    }),
  );
  await page.route("**/api/v1/tools**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk([], 0),
    }),
  );

  await page.goto("/");

  // cta-create-agent should be visible and point to /agents/builder.
  const cta = page.getByTestId("cta-create-agent");
  await expect(cta).toBeVisible();
  await expect(cta).toHaveAttribute("href", "/agents/builder");
});

test("welcome tour modal does NOT auto-open on first visit", async ({
  authedPage: page,
}) => {
  await setupHomeRoutes(page);

  // Do NOT seed ag-tour-completed-v2 — simulate a brand new user.
  await page.goto("/");

  // The welcome tour dialog should NOT be visible (checklist is the guide now).
  // The tour modal is a <dialog> or role="dialog" with tour-related text.
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // Checklist should be visible instead.
  await expect(
    page.getByText("Welcome — let's ship your first agent"),
  ).toBeVisible();
});

test("dismiss button hides the checklist and persists the flag", async ({
  authedPage: page,
}) => {
  await setupHomeRoutes(page);
  await page.goto("/");

  // Checklist should be present.
  await expect(
    page.getByText("Welcome — let's ship your first agent"),
  ).toBeVisible();

  // Click dismiss.
  await page.getByTestId("checklist-dismiss").click();

  // Checklist should disappear.
  await expect(
    page.getByText("Welcome — let's ship your first agent"),
  ).not.toBeVisible();

  // Reload and confirm still hidden (flag persisted).
  await page.reload();
  await expect(
    page.getByText("Welcome — let's ship your first agent"),
  ).not.toBeVisible();
});

test("checklist is absent when dismiss flag is pre-seeded", async ({
  authedPage: page,
}) => {
  // Pre-seed the dismiss flag before navigation.
  await page.addInitScript(() => {
    window.localStorage.setItem("ag-getstarted-dismissed-v1", "1");
  });

  await setupHomeRoutes(page);
  await page.goto("/");

  await expect(
    page.getByText("Welcome — let's ship your first agent"),
  ).not.toBeVisible();
});
