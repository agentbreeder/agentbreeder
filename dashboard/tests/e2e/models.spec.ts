import { test, expect } from "@playwright/test";

test.describe("Models Page", () => {
  test("renders models page with heading", async ({ page }) => {
    await page.goto("/models");
    await expect(page.locator("h1")).toContainText("Models");
  });

  test("has filter input and provider selector", async ({ page }) => {
    await page.goto("/models");
    await expect(
      page.locator('input[placeholder="Filter models..."]')
    ).toBeVisible();
    await expect(page.locator("select")).toBeVisible();
  });

  test("shows column headers", async ({ page }) => {
    await page.goto("/models");
    const headers = page.locator(
      ".uppercase.tracking-wider"
    );
    await expect(headers.first()).toBeVisible();
    const text = await headers.first().textContent();
    expect(text?.toLowerCase()).toContain("model");
  });

  test("shows empty or error state gracefully", async ({ page }) => {
    await page.goto("/models");
    await page.waitForTimeout(2000);
    const content = await page.textContent("body");
    expect(
      content?.includes("No models") ||
        content?.includes("Failed to load") ||
        content?.includes("LiteLLM")
    ).toBeTruthy();
  });

  test("navigates to models from sidebar", async ({ page }) => {
    await page.goto("/");
    await page.locator('aside a[href="/models"]').click();
    await expect(page).toHaveURL("/models");
    await expect(page.locator("h1")).toContainText("Models");
  });
});
