import { test, expect } from "@playwright/test";

test.describe("Prompts Page", () => {
  test("renders prompts page with heading", async ({ page }) => {
    await page.goto("/prompts");
    await expect(page.locator("h1")).toContainText("Prompts");
  });

  test("has filter input and team selector", async ({ page }) => {
    await page.goto("/prompts");
    await expect(
      page.locator('input[placeholder="Filter prompts..."]')
    ).toBeVisible();
    await expect(page.locator("select")).toBeVisible();
  });

  test("shows empty or error state gracefully", async ({ page }) => {
    await page.goto("/prompts");
    await page.waitForTimeout(2000);
    const content = await page.textContent("body");
    expect(
      content?.includes("No prompts") ||
        content?.includes("Failed to load") ||
        content?.includes("Register prompt")
    ).toBeTruthy();
  });

  test("navigates to prompts from sidebar", async ({ page }) => {
    await page.goto("/");
    await page.locator('aside a[href="/prompts"]').click();
    await expect(page).toHaveURL("/prompts");
    await expect(page.locator("h1")).toContainText("Prompts");
  });
});
