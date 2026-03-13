import { test, expect } from "./fixtures";

test.describe("Deploys Page", () => {
  test("renders deploys page with heading", async ({ authedPage: page }) => {
    await page.goto("/deploys");
    await expect(page.locator("h1")).toContainText("Deploys");
  });

  test("has status filter dropdown", async ({ authedPage: page }) => {
    await page.goto("/deploys");
    await expect(page.locator("select")).toBeVisible();
    // Verify it contains deploy-specific options
    const options = await page.locator("select option").allTextContents();
    expect(options.some((o) => o.includes("Completed"))).toBeTruthy();
    expect(options.some((o) => o.includes("Failed"))).toBeTruthy();
    expect(options.some((o) => o.includes("Building"))).toBeTruthy();
  });

  test("shows column headers", async ({ authedPage: page }) => {
    await page.goto("/deploys");
    const headers = page.locator(".rounded-lg.border .uppercase.tracking-wider");
    await expect(headers).toBeVisible();
    const text = await headers.textContent();
    expect(text?.toLowerCase()).toContain("agent");
  });

  test("shows empty or error state gracefully", async ({ authedPage: page }) => {
    await page.goto("/deploys");
    await page.waitForTimeout(2000);
    const content = await page.textContent("body");
    expect(
      content?.includes("No deploy") ||
        content?.includes("Failed to load") ||
        content?.includes("garden deploy")
    ).toBeTruthy();
  });

  test("navigates to deploys from sidebar", async ({ authedPage: page }) => {
    await page.goto("/");
    await page.locator('aside a[href="/deploys"]').click();
    await expect(page).toHaveURL("/deploys");
    await expect(page.locator("h1")).toContainText("Deploys");
  });
});
