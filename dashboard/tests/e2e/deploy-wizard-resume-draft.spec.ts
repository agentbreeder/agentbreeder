/**
 * E2E: Resume draft
 *
 * Pre-seeds localStorage with a step-3 draft and verifies that:
 * 1. The "Resume previous deploy?" banner appears
 * 2. Clicking Resume jumps to step 3 with the Provision for me radio checked
 */

import { test, expect, GCP_MEMORY_AGENT } from "./deploy-wizard-helpers";

const DRAFT = {
  step: 3,
  agentId: "agent-gcp-memory",
  cloud: "gcp",
  region: "us-central1",
  infraMode: "provision",
  provisionAck: false,
  byoFields: {},
  validateResult: null,
  envVars: [],
  secrets: [],
  scaling: { min: 1, max: 3, cpuTargetPct: 70 },
  dbTier: null,
  jobId: null,
  jobStatus: null,
  endpointUrl: null,
  approvalPending: false,
  origin: "sidebar",
  draftSavedAt: Date.now(),
  idempotencyKey: null,
  agentSnapshot: {
    id: "agent-gcp-memory",
    name: "memory-bot",
    framework: "langgraph",
    version: "1.0.0",
    team: "engineering",
    requiresApproval: false,
    declaresMemory: true,
  },
};

test.describe("Deploy Wizard — resume draft", () => {
  test("shows resume banner and restores step 3 on Resume click", async ({
    wizardPage,
  }) => {
    // Seed the draft BEFORE navigation via addInitScript
    await wizardPage.addInitScript((draft) => {
      window.localStorage.setItem("deploy-wizard-draft", JSON.stringify(draft));
    }, DRAFT);

    await wizardPage.goto("/deploy-wizard");
    await expect(wizardPage.getByText("Deploy an agent")).toBeVisible();

    // Resume banner should appear
    await expect(wizardPage.getByText("Resume previous deploy?")).toBeVisible();
    await expect(wizardPage.getByText("unfinished wizard session")).toBeVisible();

    // Click Resume
    await wizardPage.getByRole("button", { name: "Resume" }).click();

    // Resume banner should be gone
    await expect(wizardPage.getByText("Resume previous deploy?")).not.toBeVisible();

    // Should be on step 3 — Infrastructure mode
    await expect(wizardPage.getByText("Step 3 — Infrastructure mode")).toBeVisible();

    // The Step Indicator button for step 3 should be active (aria-current="step")
    const step3Btn = wizardPage.getByRole("button", { name: "Step 3: Infra" });
    await expect(step3Btn).toHaveAttribute("aria-current", "step");

    // "Provision for me" radio should be checked (state.infraMode === "provision")
    const provisionRadio = wizardPage
      .locator('input[type="radio"][name="infra-mode"]')
      .nth(1); // second radio = provision
    await expect(provisionRadio).toBeChecked();
  });

  test("Start over clears draft and returns to step 1", async ({ wizardPage, mockAgents }) => {
    // Mock agents so Step 1 renders after reset (avoids ECONNREFUSED on the agents list)
    await mockAgents([GCP_MEMORY_AGENT]);

    await wizardPage.addInitScript((draft) => {
      window.localStorage.setItem("deploy-wizard-draft", JSON.stringify(draft));
    }, DRAFT);

    await wizardPage.goto("/deploy-wizard");
    await expect(wizardPage.getByText("Resume previous deploy?")).toBeVisible();

    // Click Start over on the resume banner
    // There are two "Start over" buttons possible (resume banner + step 5 failure).
    // On load with only the banner, the first one is the banner's button.
    await wizardPage.getByRole("button", { name: "Start over" }).first().click();

    // Banner should be gone and we should be back at step 1
    await expect(wizardPage.getByText("Resume previous deploy?")).not.toBeVisible();
    await expect(wizardPage.getByText("Step 1 — Select an agent")).toBeVisible();
  });
});
