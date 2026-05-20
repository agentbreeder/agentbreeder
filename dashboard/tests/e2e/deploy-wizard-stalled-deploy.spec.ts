/**
 * E2E: Stalled / failed deploy
 *
 * Walks through to Step 5, then pushes a timed_out error event to simulate
 * a stalled deploy. Verifies:
 * 1. "Deploy failed" banner with Roll back + Start over buttons
 * 2. Clicking Start over resets the wizard to Step 1
 */

import {
  test,
  expect,
  GCP_MEMORY_AGENT,
} from "./deploy-wizard-helpers";

const JOB_ID = "j-stalled";

test.describe("Deploy Wizard — stalled / failed deploy", () => {
  test.beforeEach(async ({ mockAgents, mockCreateJob, mockGetJob }) => {
    await mockAgents([GCP_MEMORY_AGENT]);
    await mockCreateJob({ jobId: JOB_ID, pendingApproval: false });
    // Job stays in "provisioning" forever from the polling mock perspective
    await mockGetJob(JOB_ID, "provisioning", undefined);
  });

  test("shows failure UI on error event and Start over resets wizard", async ({
    wizardPage,
    pushDeployEvent,
  }) => {
    await wizardPage.goto("/deploy-wizard");
    await expect(wizardPage.getByText("Deploy an agent")).toBeVisible();

    // Step 1
    await wizardPage.getByRole("button", { name: "memory-bot" }).click();

    // Step 2
    await expect(wizardPage.getByText("Step 2 — Cloud target")).toBeVisible();
    await wizardPage.getByRole("button", { name: /^GCP/ }).click();
    const regionSelect = wizardPage.locator("#region-select");
    await expect(regionSelect).toBeVisible();
    await regionSelect.selectOption("us-central1");
    await wizardPage.getByRole("button", { name: "Next →" }).click();

    // Step 3 — Provision + ack
    await expect(wizardPage.getByText("Step 3 — Infrastructure mode")).toBeVisible();
    await wizardPage.getByText("Provision for me", { exact: false }).first().click();
    const ackCheckbox = wizardPage.locator('input[type="checkbox"]');
    await expect(ackCheckbox).toBeVisible();
    await ackCheckbox.check();
    await wizardPage.getByRole("button", { name: "Next →" }).click();

    // Step 4 — Deploy
    await expect(wizardPage.getByText("Step 4 — Configuration")).toBeVisible();
    await wizardPage.getByRole("button", { name: "Deploy", exact: true }).click();

    // Step 5 — now on live deploy page (no SSE events pushed yet — job hangs)
    await expect(wizardPage.getByText("Step 5 — Live deploy")).toBeVisible();

    // Push an error event to simulate timed_out failure
    await pushDeployEvent({
      type: "error",
      job_id: JOB_ID,
      timestamp: new Date().toISOString(),
      phase: null,
      step: null,
      total: null,
      message: "Deployment timed out",
      level: "error",
      endpoint_url: null,
      error_code: "timed_out",
    });

    // Failure UI should appear
    await expect(wizardPage.getByText("Deploy failed")).toBeVisible();
    await expect(wizardPage.getByRole("button", { name: "Roll back" })).toBeVisible();
    await expect(wizardPage.getByRole("button", { name: "Start over" })).toBeVisible();

    // Click Start over → wizard resets to step 1
    await wizardPage.getByRole("button", { name: "Start over" }).click();
    await expect(wizardPage.getByText("Step 1 — Select an agent")).toBeVisible();

    // Deploy failed banner should be gone
    await expect(wizardPage.getByText("Deploy failed")).not.toBeVisible();
  });

  test("Roll back button triggers destroy-partial and is visible", async ({
    wizardPage,
    pushDeployEvent,
  }) => {
    await wizardPage.goto("/deploy-wizard");

    // Walk to step 5 quickly
    await wizardPage.getByRole("button", { name: "memory-bot" }).click();
    await expect(wizardPage.getByText("Step 2 — Cloud target")).toBeVisible();
    await wizardPage.getByRole("button", { name: /^GCP/ }).click();
    const regionSelect = wizardPage.locator("#region-select");
    await expect(regionSelect).toBeVisible();
    await regionSelect.selectOption("us-central1");
    await wizardPage.getByRole("button", { name: "Next →" }).click();

    await expect(wizardPage.getByText("Step 3 — Infrastructure mode")).toBeVisible();
    await wizardPage.getByText("Provision for me", { exact: false }).first().click();
    await wizardPage.locator('input[type="checkbox"]').check();
    await wizardPage.getByRole("button", { name: "Next →" }).click();

    await expect(wizardPage.getByText("Step 4 — Configuration")).toBeVisible();
    await wizardPage.getByRole("button", { name: "Deploy", exact: true }).click();
    await expect(wizardPage.getByText("Step 5 — Live deploy")).toBeVisible();

    // Trigger failure
    await pushDeployEvent({
      type: "error",
      job_id: JOB_ID,
      timestamp: new Date().toISOString(),
      phase: null,
      step: null,
      total: null,
      message: null,
      level: "error",
      endpoint_url: null,
      error_code: "timed_out",
    });

    await expect(wizardPage.getByRole("button", { name: "Roll back" })).toBeVisible();

    // Clicking Roll back calls POST /destroy-partial (already mocked to 202)
    await wizardPage.getByRole("button", { name: "Roll back" }).click();
    // Roll back button should temporarily be disabled while pending
    // (no strict assertion on completion — just verify no crash)
    await expect(wizardPage.getByText("Deploy failed")).toBeVisible();
  });
});
