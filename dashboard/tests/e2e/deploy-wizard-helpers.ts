/**
 * Shared helpers and mockOrchestrator fixture for deploy-wizard E2E specs.
 *
 * Usage:
 *   import { test, expect, GCP_MEMORY_AGENT, ... } from "./deploy-wizard-helpers";
 *
 *   test("...", async ({ wizardPage, mockAgents, mockCreateJob, pushDeployEvent }) => {
 *     await mockAgents([GCP_MEMORY_AGENT]);
 *     await mockCreateJob({ jobId: "j-1" });
 *     await wizardPage.goto("/deploy-wizard");
 *     ...
 *   });
 */

import { test as base, type Page } from "./fixtures";

// ---------------------------------------------------------------------------
// Shared mock agent data
// ---------------------------------------------------------------------------

export const GCP_MEMORY_AGENT = {
  id: "agent-gcp-memory",
  name: "memory-bot",
  framework: "langgraph",
  version: "1.0.0",
  team: "engineering",
  owner: "alice@test.com",
  status: "running",
  access: { require_approval: false },
  memory: { backend: "pgvector" },
  deploy: { cloud: "gcp", region: "us-central1" },
};

export const AWS_AGENT = {
  ...GCP_MEMORY_AGENT,
  id: "agent-aws",
  name: "aws-bot",
  memory: null,
  deploy: { cloud: "aws", region: "us-east-1" },
};

export const AZURE_AGENT = {
  ...GCP_MEMORY_AGENT,
  id: "agent-azure",
  name: "azure-bot",
  memory: null,
  deploy: { cloud: "azure", region: "eastus" },
};

export const APPROVAL_AGENT = {
  ...GCP_MEMORY_AGENT,
  id: "agent-approval",
  name: "needs-approval-bot",
  memory: null,
  access: { require_approval: true },
};

// ---------------------------------------------------------------------------
// Fixture interfaces
// ---------------------------------------------------------------------------

export interface DeployJobScript {
  jobId: string;
  pendingApproval?: boolean;
  endpointUrl?: string;
  initialStatus?: string;
}

export interface ValidateInfraScript {
  valid: boolean;
  checks: { resource: string; status: string; detail: string }[];
}

// ---------------------------------------------------------------------------
// FakeEventSource script (injected into page context)
// ---------------------------------------------------------------------------

/**
 * This script overrides window.EventSource with a fake implementation so tests
 * can drive SSE events synchronously via window.__pushDeployEvent(evt).
 * It must be injected via page.addInitScript BEFORE navigation.
 */
const FAKE_EVENT_SOURCE_SCRIPT = () => {
  class FakeEventSource {
    static instances: FakeEventSource[] = [];
    listeners: Record<string, (e: MessageEvent) => void> = {};
    readyState = 0;
    onopen: ((e: Event) => void) | null = null;
    onerror: ((e: Event) => void) | null = null;

    constructor(public url: string) {
      FakeEventSource.instances.push(this);
      // Open synchronously on next tick so listeners have time to attach.
      setTimeout(() => {
        this.readyState = 1;
        this.onopen?.(new Event("open"));
      }, 0);
    }

    addEventListener(type: string, cb: (e: MessageEvent) => void) {
      this.listeners[type] = cb;
    }

    removeEventListener(type: string, _cb: (e: MessageEvent) => void) {
      delete this.listeners[type];
    }

    close() {
      this.readyState = 2;
    }
  }

  (window as unknown as Record<string, unknown>).EventSource = FakeEventSource;

  (window as unknown as Record<string, unknown>).__pushDeployEvent = (
    evt: { type: string; [k: string]: unknown },
  ) => {
    const all = (FakeEventSource as unknown as { instances: FakeEventSource[] }).instances;
    const latest = all[all.length - 1];
    if (!latest) return;
    const cb = latest.listeners[evt.type];
    if (cb) {
      cb({ data: JSON.stringify(evt) } as MessageEvent);
    }
  };
};

// ---------------------------------------------------------------------------
// Extended fixture
// ---------------------------------------------------------------------------

export const test = base.extend<{
  wizardPage: Page;
  mockAgents: (agents: unknown[]) => Promise<void>;
  mockValidateInfra: (result: ValidateInfraScript) => Promise<void>;
  mockCreateJob: (script: DeployJobScript) => Promise<void>;
  mockGetJob: (jobId: string, status: string, endpointUrl?: string) => Promise<void>;
  pushDeployEvent: (evt: object) => Promise<void>;
}>({
  /**
   * wizardPage: authedPage with FakeEventSource injected + always-ok mocks for
   * cloud-requirements and destroy-partial (which all wizard tests share).
   */
  wizardPage: async ({ authedPage }, use) => {
    // Inject FakeEventSource BEFORE any navigation.
    await authedPage.addInitScript(FAKE_EVENT_SOURCE_SCRIPT);

    // Cloud requirements — return same generic field set for any cloud.
    await authedPage.route("**/api/v1/deployments/cloud-requirements/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: {
            fields: [
              {
                name: "GOOGLE_CLOUD_PROJECT",
                required: true,
                description: "GCP project ID",
              },
              {
                name: "AWS_ECS_CLUSTER",
                required: true,
                description: "ECS cluster name",
              },
              {
                name: "AZURE_RESOURCE_GROUP",
                required: true,
                description: "Resource group",
              },
            ],
          },
          meta: {},
          errors: [],
        }),
      }),
    );

    // Destroy-partial always succeeds for rollback tests.
    await authedPage.route("**/api/v1/deployments/*/destroy-partial", (route) =>
      route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          data: { job_id: "any", status: "rollback_started" },
          meta: {},
          errors: [],
        }),
      }),
    );

    await use(authedPage);
  },

  /** Call before goto to set up the agents list mock. */
  mockAgents: async ({ wizardPage }, use) => {
    await use(async (agents) => {
      await wizardPage.route("**/api/v1/agents*", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: agents, meta: {}, errors: [] }),
        }),
      );
    });
  },

  /** Call before goto to set up validate-infra mock. */
  mockValidateInfra: async ({ wizardPage }, use) => {
    await use(async (result) => {
      await wizardPage.route("**/api/v1/deployments/validate-infra", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ data: result, meta: {}, errors: [] }),
        }),
      );
    });
  },

  /** Call before goto to set up POST /deployments/ mock. */
  mockCreateJob: async ({ wizardPage }, use) => {
    await use(async (script) => {
      await wizardPage.route("**/api/v1/deployments/", (route) => {
        if (route.request().method() !== "POST") return route.continue();
        return route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              job_id: script.jobId,
              pending_approval: !!script.pendingApproval,
            },
            meta: {},
            errors: [],
          }),
        });
      });
    });
  },

  /** Call before goto (or between steps) to set up GET /deployments/{jobId} mock. */
  mockGetJob: async ({ wizardPage }, use) => {
    await use(async (jobId, status, endpointUrl) => {
      await wizardPage.route(`**/api/v1/deployments/${jobId}`, (route) => {
        if (route.request().method() !== "GET") return route.continue();
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: {
              job_id: jobId,
              status,
              endpoint_url: endpointUrl ?? null,
              pending_approval: status === "pending_approval",
              agent_id: "any",
              cloud: "gcp",
              region: "us-central1",
              team_id: "engineering",
              created_at: "2026-05-20T00:00:00Z",
            },
            meta: {},
            errors: [],
          }),
        });
      });
    });
  },

  /** Push a synthetic SSE event to the latest FakeEventSource instance. */
  pushDeployEvent: async ({ wizardPage }, use) => {
    await use(async (evt) => {
      await wizardPage.evaluate(
        (e) => (window as unknown as Record<string, (v: unknown) => void>).__pushDeployEvent(e),
        evt,
      );
    });
  },
});

export { expect } from "@playwright/test";
