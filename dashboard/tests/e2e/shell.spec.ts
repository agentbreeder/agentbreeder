import { test, expect } from "@playwright/test";

test.describe("Dashboard Shell", () => {
  test("renders sidebar with navigation links", async ({ page }) => {
    await page.goto("/");
    // Sidebar brand
    await expect(page.getByText("Agent Garden", { exact: true })).toBeVisible();
    // Nav links
    await expect(page.locator('aside a[href="/agents"]')).toBeVisible();
    await expect(page.locator('aside a[href="/tools"]')).toBeVisible();
    await expect(page.locator('aside a[href="/models"]')).toBeVisible();
    await expect(page.locator('aside a[href="/prompts"]')).toBeVisible();
    await expect(page.locator('aside a[href="/deploys"]')).toBeVisible();
  });

  test("navigates to agents page on click", async ({ page }) => {
    await page.goto("/");
    await page.locator('aside a[href="/agents"]').click();
    await expect(page).toHaveURL("/agents");
    await expect(page.locator("h1")).toContainText("Agents");
  });

  test("navigates to tools page on click", async ({ page }) => {
    await page.goto("/");
    await page.locator('aside a[href="/tools"]').click();
    await expect(page).toHaveURL("/tools");
    await expect(page.locator("h1")).toContainText("Tools");
  });

  test("shows home overview on root route", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("h1")).toContainText("Overview");
  });

  test("command search opens via search button click", async ({ page }) => {
    await page.goto("/");
    // Click the search button in sidebar to open command search
    await page.locator("aside button", { hasText: "Search..." }).click();
    await expect(
      page.locator('input[placeholder="Search agents, tools, prompts..."]')
    ).toBeVisible();
  });

  test("command search closes with Escape", async ({ page }) => {
    await page.goto("/");
    // Open search via button click
    await page.locator("aside button", { hasText: "Search..." }).click();
    await expect(
      page.locator('input[placeholder="Search agents, tools, prompts..."]')
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(
      page.locator('input[placeholder="Search agents, tools, prompts..."]')
    ).not.toBeVisible();
  });

  test("theme switcher cycles through modes", async ({ page }) => {
    await page.goto("/");
    const html = page.locator("html");
    // Default is dark
    await expect(html).toHaveClass(/dark/);

    // Click to switch to light
    await page.locator("button", { hasText: /dark/i }).click();
    await expect(html).not.toHaveClass(/dark/);

    // Click to switch to system
    await page.locator("button", { hasText: /light/i }).click();
    await expect(page.locator("button", { hasText: /system/i })).toBeVisible();
  });

  test("breadcrumbs show on sub-pages", async ({ page }) => {
    await page.goto("/agents");
    const breadcrumb = page.locator("header nav");
    await expect(breadcrumb).toBeVisible();
    await expect(breadcrumb).toContainText("agents", { ignoreCase: true });
  });

  test("unknown routes redirect to home", async ({ page }) => {
    await page.goto("/nonexistent");
    await page.waitForURL("/");
    await expect(page.locator("h1")).toContainText("Overview");
  });
});
