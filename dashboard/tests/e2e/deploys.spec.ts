import { test, expect } from "@playwright/test";

test.describe("Deploys Page", () => {
  test("renders deploys page with heading", async ({ page }) => {
    await page.goto("/deploys");
    await expect(page.locator("h1")).toContainText("Deploys");
  });

  test("has status filter dropdown", async ({ page }) => {
    await page.goto("/deploys");
    await expect(page.locator("select")).toBeVisible();
    // Verify it contains deploy-specific options
    const options = await page.locator("select option").allTextContents();
    expect(options.some((o) => o.includes("Completed"))).toBeTruthy();
    expect(options.some((o) => o.includes("Failed"))).toBeTruthy();
    expect(options.some((o) => o.includes("Building"))).toBeTruthy();
  });

  test("shows column headers", async ({ page }) => {
    await page.goto("/deploys");
    const headers = page.locator(".uppercase.tracking-wider");
    await expect(headers.first()).toBeVisible();
    const text = await headers.first().textContent();
    expect(text?.toLowerCase()).toContain("agent");
  });

  test("shows empty or error state gracefully", async ({ page }) => {
    await page.goto("/deploys");
    await page.waitForTimeout(2000);
    const content = await page.textContent("body");
    expect(
      content?.includes("No deploy") ||
        content?.includes("Failed to load") ||
        content?.includes("garden deploy")
    ).toBeTruthy();
  });

  test("navigates to deploys from sidebar", async ({ page }) => {
    await page.goto("/");
    await page.locator('aside a[href="/deploys"]').click();
    await expect(page).toHaveURL("/deploys");
    await expect(page.locator("h1")).toContainText("Deploys");
  });
});
