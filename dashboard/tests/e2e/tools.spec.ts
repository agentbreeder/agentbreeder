import { test, expect } from "./fixtures";

test.describe("Tools Page", () => {
  test("renders tools page with heading", async ({ authedPage: page }) => {
    await page.goto("/tools");
    await expect(page.locator("h1")).toContainText("Tools");
  });

  test("has filter input and type selector", async ({ authedPage: page }) => {
    await page.goto("/tools");
    await expect(page.locator('input[placeholder="Filter tools..."]')).toBeVisible();
    await expect(page.locator("select")).toBeVisible();
  });

  test("shows empty or error state gracefully", async ({ authedPage: page }) => {
    await page.goto("/tools");
    await page.waitForTimeout(2000);
    const content = await page.textContent("body");
    expect(
      content?.includes("No tools") ||
      content?.includes("Failed to load") ||
      content?.includes("agentbreeder scan")
    ).toBeTruthy();
  });
});
