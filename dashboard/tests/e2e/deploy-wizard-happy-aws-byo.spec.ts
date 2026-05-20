/**
 * E2E: Happy path — AWS BYO (Bring Your Own Infrastructure)
 *
 * Walks the full wizard for an AWS agent, fills a BYO infra field,
 * validates successfully, deploys, and asserts the success state.
 */

import {
  test,
  expect,
  AWS_AGENT,
} from "./deploy-wizard-helpers";

const JOB_ID = "j-aws-1";
const ENDPOINT_URL = "https://agent.us-east-1.elb.amazonaws.com";

test.describe("Deploy Wizard — AWS BYO happy path", () => {
  test.beforeEach(async ({
    mockAgents,
    mockValidateInfra,
    mockCreateJob,
    mockGetJob,
  }) => {
    await mockAgents([AWS_AGENT]);
    await mockValidateInfra({
      valid: true,
      checks: [
        { resource: "vpc-123", status: "found", detail: "us-east-1a" },
        { resource: "ecs-cluster-prod", status: "found", detail: "active" },
      ],
    });
    await mockCreateJob({ jobId: JOB_ID, pendingApproval: false });
    await mockGetJob(JOB_ID, "completed", ENDPOINT_URL);
  });

  test("completes BYO wizard and shows endpoint URL after deploy", async ({
    wizardPage,
    pushDeployEvent,
  }) => {
    // Navigate directly with agentId pre-filled (simulates agent-detail → Deploy entry point)
    await wizardPage.goto("/deploy-wizard?agentId=agent-aws&from=agent-detail");
    await expect(wizardPage.getByText("Deploy an agent")).toBeVisible();

    // ---- Step 1: Agent pre-filled by query param but still on step 1 until SET_AGENT ----
    // The PREFILL_FROM_QUERY only sets agentId string, not agentSnapshot,
    // so the wizard waits for actual agent selection.
    await expect(wizardPage.getByText("Step 1 — Select an agent")).toBeVisible();
    await wizardPage.getByRole("button", { name: "aws-bot" }).click();

    // ---- Step 2: Cloud + region ----
    await expect(wizardPage.getByText("Step 2 — Cloud target")).toBeVisible();
    await wizardPage.getByRole("button", { name: /^AWS/ }).click();

    const regionSelect = wizardPage.locator("#region-select");
    await expect(regionSelect).toBeVisible();
    await regionSelect.selectOption("us-east-1");

    await wizardPage.getByRole("button", { name: "Next →" }).click();

    // ---- Step 3: BYO mode ----
    await expect(wizardPage.getByText("Step 3 — Infrastructure mode")).toBeVisible();

    // Click the BYO radio label
    const byoLabel = wizardPage.getByText("Bring Your Own Infrastructure").first();
    await byoLabel.click();

    // InfraValidatePanel loads — fill the AWS_ECS_CLUSTER field
    await expect(wizardPage.getByText("BYO infrastructure fields")).toBeVisible();

    const clusterInput = wizardPage.locator('input[type="text"]').filter({
      // The input is inside a label whose text contains AWS_ECS_CLUSTER
    }).first();
    // Use a more robust selector: find any text input that is now visible
    const textInput = wizardPage.locator('input[type="text"]').first();
    await expect(textInput).toBeVisible();
    await textInput.fill("ecs-cluster-prod");

    // Click Validate
    await wizardPage.getByRole("button", { name: "Validate infrastructure" }).click();

    // Wait for green ✓ row (there may be multiple checks; first() avoids strict-mode violation)
    await expect(wizardPage.getByText(/✓/).first()).toBeVisible();
    await expect(wizardPage.getByText("vpc-123")).toBeVisible();

    // Next should now be enabled
    await wizardPage.getByRole("button", { name: "Next →" }).click();

    // ---- Step 4: Deploy ----
    await expect(wizardPage.getByText("Step 4 — Configuration")).toBeVisible();
    await wizardPage.getByRole("button", { name: "Deploy", exact: true }).click();

    // ---- Step 5: Live deploy ----
    await expect(wizardPage.getByText("Step 5 — Live deploy")).toBeVisible();

    // Push complete event
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
  });
});
