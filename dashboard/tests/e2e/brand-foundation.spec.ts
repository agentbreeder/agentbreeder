import { test, expect, apiOk } from "./fixtures";

test("app is dark-only", async ({ authedPage: page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveClass(/dark/);
  // Background resolves to the brand near-black (#09090b). Chromium may return
  // the color in oklch form when the CSS value is authored as oklch — both
  // representations are correct.
  const bg = await page.evaluate(() =>
    getComputedStyle(document.body).backgroundColor
  );
  // oklch(0.085 0 0) ≈ #09090b ≈ rgb(9,9,11)
  expect(bg === "rgb(9, 9, 11)" || bg === "oklch(0.085 0 0)").toBe(true);
});

test("no theme toggle is present", async ({ authedPage: page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: /theme/i })).toHaveCount(0);
});

test("page titles use the Bricolage display font", async ({ authedPage: page }) => {
  // Mock the models API so the page renders without a live backend.
  await page.route("**/registry/models**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: apiOk([], 0),
    }),
  );
  await page.goto("/models");
  const h1 = page.getByRole("heading", { level: 1 }).first();
  const family = await h1.evaluate((el) => getComputedStyle(el).fontFamily);
  expect(family.toLowerCase()).toContain("bricolage");
});
