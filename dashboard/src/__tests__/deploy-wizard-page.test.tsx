import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import DeployWizardPage from "@/pages/deploy-wizard";

vi.mock("@/lib/api", () => ({
  api: {
    agents: {
      list: () => Promise.resolve({ data: [] }),
    },
    deployments: {
      cloudRequirements: () => Promise.resolve({ data: { fields: [] } }),
      validateInfra: vi.fn(),
      createJob: vi.fn(),
      getJob: vi.fn(),
      destroyPartial: vi.fn(),
    },
  },
}));

// Mock useDeployStream so we don't try to open a real EventSource in tests.
vi.mock("@/hooks/useDeployStream", () => ({
  useDeployStream: () => ({ status: "open" }),
}));

beforeEach(() => {
  localStorage.clear();
});

function wrap(initialEntries: string[] = ["/deploy-wizard"]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <DeployWizardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DeployWizardPage", () => {
  it("renders Step 1 by default", async () => {
    wrap();
    // Wait for the step to render (Step1Agent loads agents via query)
    const heading = await screen.findByText(/Step 1 — Select an agent/i);
    expect(heading).toBeInTheDocument();
  });

  it("clamps ?step=5 from empty state down to Step 1", async () => {
    wrap(["/deploy-wizard?step=5"]);
    const heading = await screen.findByText(/Step 1 — Select an agent/i);
    expect(heading).toBeInTheDocument();
  });

  it("persists state to localStorage on change", () => {
    vi.useFakeTimers();
    wrap();
    // The debounced sync writes after 250ms.
    vi.advanceTimersByTime(500);
    const raw = localStorage.getItem("deploy-wizard-draft");
    expect(raw).toBeTruthy();
    vi.useRealTimers();
  });

  it("shows a Resume prompt when a draft exists in localStorage", () => {
    localStorage.setItem(
      "deploy-wizard-draft",
      JSON.stringify({
        step: 3,
        agentId: "a-99",
        cloud: "gcp",
        region: "us-central1",
        agentSnapshot: {
          id: "a-99",
          name: "saved",
          framework: "x",
          version: "1.0",
          team: "t1",
          requiresApproval: false,
          declaresMemory: false,
        },
      }),
    );
    wrap();
    expect(screen.getByText(/Resume previous/i)).toBeInTheDocument();
  });

  it("clicking 'Start over' on the Resume prompt clears the draft", () => {
    localStorage.setItem(
      "deploy-wizard-draft",
      JSON.stringify({ step: 3, agentId: "a-99" }),
    );
    wrap();
    fireEvent.click(screen.getByRole("button", { name: /Start over/i }));
    expect(localStorage.getItem("deploy-wizard-draft")).toBeNull();
  });
});
