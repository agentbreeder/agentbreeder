/**
 * E2E: Approval-required agent
 *
 * Verifies that:
 * 1. Step 4 shows "Submit for approval" (not "Deploy") when agent.access.require_approval=true
 * 2. After submitting, Step 5 shows "Awaiting admin approval" banner
 * 3. When an admin eventually approves (simulated by pushing a complete event),
 *    the wizard transitions to the success state with endpoint URL.
 */

import {
  test,
  expect,
  APPROVAL_AGENT,
} from "./deploy-wizard-helpers";

const JOB_ID = "j-approval";
const ENDPOINT_URL = "https://approved-agent.run.app";

test.describe("Deploy Wizard — approval required", () => {
  test.beforeEach(async ({ mockAgents, mockCreateJob, mockGetJob }) => {
    await mockAgents([APPROVAL_AGENT]);
    // Job starts as pending_approval
    await mockCreateJob({ jobId: JOB_ID, pendingApproval: true });
    await mockGetJob(JOB_ID, "pending_approval", undefined);
  });

  test("shows Submit for approval button and approval banner", async ({
    wizardPage,
    pushDeployEvent,
  }) => {
    await wizardPage.goto("/deploy-wizard");
    await expect(wizardPage.getByText("Deploy an agent")).toBeVisible();

    // Step 1 — select the approval-required agent
    await wizardPage.getByRole("button", { name: "needs-approval-bot" }).click();

    // Step 2 — GCP + us-central1
    await expect(wizardPage.getByText("Step 2 — Cloud target")).toBeVisible();
    await wizardPage.getByRole("button", { name: /^GCP/ }).click();
    const regionSelect = wizardPage.locator("#region-select");
    await expect(regionSelect).toBeVisible();
    await regionSelect.selectOption("us-central1");
    await wizardPage.getByRole("button", { name: "Next →" }).click();

    // Step 3 — Provision for me + ack
    await expect(wizardPage.getByText("Step 3 — Infrastructure mode")).toBeVisible();
    await wizardPage.getByText("Provision for me", { exact: false }).first().click();
    const ackCheckbox = wizardPage.locator('input[type="checkbox"]');
    await expect(ackCheckbox).toBeVisible();
    await ackCheckbox.check();
    await wizardPage.getByRole("button", { name: "Next →" }).click();

    // Step 4 — button label must be "Submit for approval"
    await expect(wizardPage.getByText("Step 4 — Configuration")).toBeVisible();
    const submitBtn = wizardPage.getByRole("button", { name: "Submit for approval" });
    await expect(submitBtn).toBeVisible();
    await submitBtn.click();

    // Step 5 — approval banner
    await expect(wizardPage.getByText("Step 5 — Live deploy")).toBeVisible();
    await expect(wizardPage.getByText("Awaiting admin approval")).toBeVisible();

    // Simulate admin approving by pushing a complete event with endpoint URL
    await pushDeployEvent({
      type: "complete",
      job_id: JOB_ID,
      timestamp: new Date().toISOString(),
      phase: null,
      step: null,
      total: null,
      message: null,
      level: null,
      endpoint_url: ENDPOINT_URL,
      error_code: null,
    });

    // After approval + completion, endpoint URL should appear
    await expect(wizardPage.getByText(ENDPOINT_URL)).toBeVisible();
    await expect(wizardPage.getByRole("button", { name: "Copy" })).toBeVisible();
  });
});
