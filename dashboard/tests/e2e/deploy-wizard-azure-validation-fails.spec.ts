/**
 * E2E: Azure BYO validation failure
 *
 * Walks to Step 3 BYO, triggers validation, and asserts that:
 * - A red ✗ row with the failing resource name appears
 * - The Next → button is disabled (canAdvance returns false when valid=false)
 */

import {
  test,
  expect,
  AZURE_AGENT,
} from "./deploy-wizard-helpers";

test.describe("Deploy Wizard — Azure BYO validation failure", () => {
  test.beforeEach(async ({ mockAgents, mockValidateInfra }) => {
    await mockAgents([AZURE_AGENT]);
    await mockValidateInfra({
      valid: false,
      checks: [
        {
          resource: "rg-test",
          status: "missing",
          detail: "NotFound",
        },
      ],
    });
  });

  test("shows red ✗ row and disables Next when validation fails", async ({
    wizardPage,
  }) => {
    await wizardPage.goto("/deploy-wizard");
    await expect(wizardPage.getByText("Deploy an agent")).toBeVisible();

    // Step 1 — select agent
    await wizardPage.getByRole("button", { name: "azure-bot" }).click();

    // Step 2 — pick Azure + region
    await expect(wizardPage.getByText("Step 2 — Cloud target")).toBeVisible();
    await wizardPage.getByRole("button", { name: /^Azure/ }).click();

    const regionSelect = wizardPage.locator("#region-select");
    await expect(regionSelect).toBeVisible();
    await regionSelect.selectOption("eastus");

    await wizardPage.getByRole("button", { name: "Next →" }).click();

    // Step 3 — BYO mode
    await expect(wizardPage.getByText("Step 3 — Infrastructure mode")).toBeVisible();
    await wizardPage.getByText("Bring Your Own Infrastructure").first().click();

    // Fill in the resource group field (first visible text input)
    await expect(wizardPage.getByText("BYO infrastructure fields")).toBeVisible();
    const textInput = wizardPage.locator('input[type="text"]').first();
    await expect(textInput).toBeVisible();
    await textInput.fill("rg-test");

    // Trigger validation
    await wizardPage.getByRole("button", { name: "Validate infrastructure" }).click();

    // Assert red ✗ row with the missing resource
    await expect(wizardPage.getByText(/✗/)).toBeVisible();
    await expect(wizardPage.getByText("rg-test")).toBeVisible();

    // Next button must be disabled because valid=false blocks canAdvance(state, 4)
    const nextBtn = wizardPage.getByRole("button", { name: "Next →" });
    await expect(nextBtn).toBeDisabled();
  });
});
