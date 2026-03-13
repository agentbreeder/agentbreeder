import { test, expect } from "./fixtures";

test.describe("Models Page", () => {
  test("renders models page with heading", async ({ authedPage: page }) => {
    await page.goto("/models");
    await expect(page.locator("h1")).toContainText("Models");
  });

  test("has filter input and provider selector", async ({ authedPage: page }) => {
    await page.goto("/models");
    await expect(
      page.locator('input[placeholder="Filter models..."]')
    ).toBeVisible();
    // Provider filtering uses pill-style tab buttons instead of a select
    await expect(page.getByRole("button", { name: "All" })).toBeVisible();
    await expect(page.getByRole("button", { name: "OpenAI" })).toBeVisible();
  });

  test("shows column headers", async ({ authedPage: page }) => {
    await page.goto("/models");
    const headers = page.locator(".rounded-lg.border .uppercase.tracking-wider");
    await expect(headers).toBeVisible();
    const text = await headers.textContent();
    expect(text?.toLowerCase()).toContain("model");
  });

  test("shows empty or error state gracefully", async ({ authedPage: page }) => {
    await page.goto("/models");
    await page.waitForTimeout(2000);
    const content = await page.textContent("body");
    expect(
      content?.includes("No models") ||
        content?.includes("Failed to load") ||
        content?.includes("LiteLLM")
    ).toBeTruthy();
  });

  test("navigates to models from sidebar", async ({ authedPage: page }) => {
    await page.goto("/");
    await page.locator('aside a[href="/models"]').click();
    await expect(page).toHaveURL("/models");
    await expect(page.locator("h1")).toContainText("Models");
  });
});
