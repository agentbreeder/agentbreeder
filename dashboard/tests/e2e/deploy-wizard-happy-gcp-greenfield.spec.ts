/**
 * E2E: Happy path — GCP Greenfield (provision mode)
 *
 * Walks the full 5-step wizard for a memory-enabled GCP agent with
 * "Provision for me" infra mode, drives SSE events, and asserts the
 * endpoint URL and Copy button appear on success.
 */

import {
  test,
  expect,
  GCP_MEMORY_AGENT,
} from "./deploy-wizard-helpers";

const JOB_ID = "j-gcp-1";
const ENDPOINT_URL = "https://demo-uc.a.run.app";

test.describe("Deploy Wizard — GCP Greenfield happy path", () => {
  test.beforeEach(async ({ mockAgents, mockCreateJob, mockGetJob }) => {
    await mockAgents([GCP_MEMORY_AGENT]);
    await mockCreateJob({ jobId: JOB_ID, pendingApproval: false });
    await mockGetJob(JOB_ID, "completed", ENDPOINT_URL);
  });

  test("completes full wizard and shows endpoint URL", async ({
    wizardPage,
    pushDeployEvent,
  }) => {
    // Navigate to deploy wizard
    await wizardPage.goto("/deploy-wizard");
    await expect(wizardPage.getByText("Deploy an agent")).toBeVisible();

    // ---- Step 1: Select agent ----
    await expect(wizardPage.getByText("Step 1 — Select an agent")).toBeVisible();
    await wizardPage.getByRole("button", { name: "memory-bot" }).click();

    // Clicking an agent auto-advances to step 2 (SET_AGENT reducer jumps to step 2)
    await expect(wizardPage.getByText("Step 2 — Cloud target")).toBeVisible();

    // ---- Step 2: Cloud + region ----
    await wizardPage.getByRole("button", { name: /^GCP/ }).click();
    // Region select should appear after clicking GCP
    const regionSelect = wizardPage.locator("#region-select");
    await expect(regionSelect).toBeVisible();
    await regionSelect.selectOption("us-central1");

    // Click Next →
    await wizardPage.getByRole("button", { name: "Next →" }).click();

    // ---- Step 3: Infrastructure mode ----
    await expect(wizardPage.getByText("Step 3 — Infrastructure mode")).toBeVisible();

    // Pick "Provision for me"
    const provisionLabel = wizardPage.getByText("Provision for me", { exact: false }).first();
    await provisionLabel.click();

    // Ack checkbox appears inside ResourcePreviewTree
    const ackCheckbox = wizardPage.locator('input[type="checkbox"]');
    await expect(ackCheckbox).toBeVisible();
    await ackCheckbox.check();

    // Click Next →
    await wizardPage.getByRole("button", { name: "Next →" }).click();

    // ---- Step 4: Configuration + Deploy ----
    await expect(wizardPage.getByText("Step 4 — Configuration")).toBeVisible();

    // Since this agent requires no approval, button should read "Deploy".
    // Use exact:true to avoid matching the Step Indicator "Step 5: Deploy" button.
    const deployBtn = wizardPage.getByRole("button", { name: "Deploy", exact: true });
    await expect(deployBtn).toBeVisible();
    await deployBtn.click();

    // ---- Step 5: Live deploy ----
    await expect(wizardPage.getByText("Step 5 — Live deploy")).toBeVisible();

    // Push SSE phase events sequentially
    const phases = [
      "provisioning",
      "building",
      "pushing",
      "deploying",
      "health_checking",
      "registering",
    ] as const;

    for (const phase of phases) {
      await pushDeployEvent({
        type: "phase",
        job_id: JOB_ID,
        timestamp: new Date().toISOString(),
        phase,
        step: null,
        total: null,
        message: null,
        level: null,
        endpoint_url: null,
        error_code: null,
      });
      // Each phase name should become visible in the phase indicator
      await expect(wizardPage.getByText(phase)).toBeVisible();
    }

    // Push complete event with endpoint URL
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

    // Assert success state
    await expect(wizardPage.getByText(ENDPOINT_URL)).toBeVisible();
    await expect(wizardPage.getByRole("button", { name: "Copy" })).toBeVisible();

    // Do NOT click Copy — clipboard permission flake
  });
});
