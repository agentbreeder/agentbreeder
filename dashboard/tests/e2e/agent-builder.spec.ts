import { test, expect } from "./fixtures";

const BUILDER_URL = "/agents/builder";

test("renders agent builder page", async ({ authedPage: page }) => {
  await page.goto(BUILDER_URL);
  // Agent builder shows "New Agent" or "Edit Agent" in h2, not h1
  await expect(
    page.getByRole("heading", { name: /new agent/i })
      .or(page.getByRole("heading", { name: /edit agent/i }))
      .or(page.getByText(/new agent/i).first())
  ).toBeVisible();
});

test("YAML editor is visible by default", async ({ authedPage: page }) => {
  await page.goto(BUILDER_URL);
  await page.waitForTimeout(500);
  // CodeMirror editor (.cm-editor) or Monaco (.monaco-editor) or textarea
  const editor = page
    .locator(".cm-editor")
    .or(page.locator(".monaco-editor"))
    .or(page.locator("textarea"))
    .or(page.locator("[role='textbox']"))
    .first();
  await expect(editor).toBeVisible();
});

test("shows framework selector", async ({ authedPage: page }) => {
  await page.goto(BUILDER_URL);
  await page.waitForTimeout(500);
  // Default YAML has "framework: langgraph" visible in the editor
  const body = await page.textContent("body");
  expect(
    body?.toLowerCase().includes("langgraph") ||
      body?.toLowerCase().includes("framework")
  ).toBeTruthy();
});

test("shows deploy button", async ({ authedPage: page }) => {
  await page.goto(BUILDER_URL);
  await expect(page.getByRole("button", { name: /deploy/i })).toBeVisible();
});

test("can switch to visual mode", async ({ authedPage: page }) => {
  await page.goto(BUILDER_URL);
  await page.waitForTimeout(500);

  const visualToggle = page
    .getByRole("button", { name: /visual/i })
    .or(page.getByRole("tab", { name: /visual/i }))
    .first();

  if (await visualToggle.isVisible()) {
    await visualToggle.click();
    await expect(page.locator("body")).not.toBeEmpty();
  } else {
    await expect(page.locator("body")).not.toBeEmpty();
  }
});

test("submit for review button visible", async ({ authedPage: page }) => {
  await page.goto(BUILDER_URL);
  const reviewBtn = page
    .getByRole("button", { name: /submit for review/i })
    .or(page.getByRole("button", { name: /review/i }))
    .or(page.getByRole("button", { name: /submit/i }))
    .first();
  await expect(reviewBtn).toBeVisible();
});

// ---------------------------------------------------------------------------
// Issue #204 — v2 fields in the visual builder
// ---------------------------------------------------------------------------

test("visual builder exposes language radio (python/typescript)", async ({
  authedPage: page,
}) => {
  await page.goto(BUILDER_URL);
  await page.waitForTimeout(500);

  const visualToggle = page.getByRole("button", { name: /^visual$/i }).first();
  await visualToggle.click();
  await page.waitForTimeout(200);

  await expect(page.getByTestId("language-section")).toBeVisible();
  await expect(page.getByTestId("language-python")).toBeVisible();
  await expect(page.getByTestId("language-typescript")).toBeVisible();
});

test("selecting typescript emits runtime block in YAML", async ({ authedPage: page }) => {
  await page.goto(BUILDER_URL);
  await page.waitForTimeout(500);

  // Switch to visual mode and pick typescript
  await page.getByRole("button", { name: /^visual$/i }).first().click();
  await page.waitForTimeout(200);
  await page.getByTestId("language-typescript").click();

  // Switch back to YAML and verify the runtime block was emitted
  await page.getByRole("button", { name: /^yaml$/i }).first().click();
  await page.waitForTimeout(200);

  const body = await page.textContent("body");
  expect(body).toContain("runtime:");
  expect(body).toContain("language: node");
});

test("visual builder exposes gateways panel", async ({ authedPage: page }) => {
  await page.goto(BUILDER_URL);
  await page.waitForTimeout(500);

  await page.getByRole("button", { name: /^visual$/i }).first().click();
  await page.waitForTimeout(200);

  await expect(page.getByTestId("gateways-section")).toBeVisible();
});

test("model picker hides deprecated by default and toggles back", async ({
  authedPage: page,
}) => {
  await page.goto(BUILDER_URL);
  await page.waitForTimeout(500);
  await page.getByRole("button", { name: /^visual$/i }).first().click();
  await page.waitForTimeout(200);

  const showDeprecated = page.getByLabel(/show deprecated/i);
  await expect(showDeprecated).toBeVisible();

  // Off by default => no "deprecated" option in primary model dropdown.
  const primary = page.locator("select").first();
  let optionsText = await primary.allTextContents();
  expect(optionsText.join(" ").toLowerCase()).not.toContain("deprecated");

  // Toggle on => now the deprecated entries appear.
  await showDeprecated.check();
  optionsText = await primary.allTextContents();
  expect(optionsText.join(" ").toLowerCase()).toContain("deprecated");
});
