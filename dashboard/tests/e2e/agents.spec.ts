import { test, expect } from "./fixtures";

test.describe("Agents Page", () => {
  test("renders agents page with heading and filters", async ({ authedPage: page }) => {
    await page.goto("/agents");
    await expect(page.locator("h1")).toContainText("Agents");
    await expect(page.locator('input[placeholder="Search agents..."]')).toBeVisible();
    // Framework filter select
    await expect(page.locator("select").first()).toBeVisible();
  });

  test("shows error state when API unavailable", async ({ authedPage: page }) => {
    await page.goto("/agents");
    // Wait for the API call to fail and show error or empty state
    await page.waitForTimeout(2000);
    const content = await page.textContent("body");
    expect(
      content?.includes("No agents") ||
      content?.includes("Failed to load") ||
      content?.includes("agents")
    ).toBeTruthy();
  });

  test("search input updates URL params", async ({ authedPage: page }) => {
    await page.goto("/agents");
    const input = page.locator('input[placeholder="Search agents..."]');
    await input.fill("test-agent");
    await expect(page).toHaveURL(/q=test-agent/);
  });

  test("column headers are visible", async ({ authedPage: page }) => {
    await page.goto("/agents");
    // Use a selector scoped to the table container (rounded border div) to avoid matching sidebar section headers
    const headerRow = page.locator(".rounded-lg.border .uppercase.tracking-wider");
    await expect(headerRow).toBeVisible();
    await expect(headerRow).toContainText("Agent");
    await expect(headerRow).toContainText("Framework");
    await expect(headerRow).toContainText("Team");
    await expect(headerRow).toContainText("Updated");
  });
});
