import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Step1Agent } from "@/components/deploy-wizard/Step1Agent";
import { initialState, type DeployWizardState } from "@/lib/deploy-wizard-state";

vi.mock("@/lib/api", () => ({
  api: {
    agents: {
      list: () =>
        Promise.resolve({
          data: [
            {
              id: "a-1",
              name: "demo",
              framework: "langgraph",
              version: "1.0.0",
              team: "t1",
              description: "Demo agent",
              owner: "alice@example.com",
              model_primary: "claude-sonnet-4",
              model_fallback: null,
              endpoint_url: null,
              status: "stopped",
              tags: [],
              config_snapshot: {},
              created_at: "2026-05-19T00:00:00Z",
              updated_at: "2026-05-19T00:00:00Z",
            },
            {
              id: "a-2",
              name: "billing",
              framework: "crewai",
              version: "0.4.0",
              team: "t1",
              description: "Billing agent",
              owner: "bob@example.com",
              model_primary: "gpt-4o",
              model_fallback: null,
              endpoint_url: null,
              status: "running",
              tags: ["production"],
              config_snapshot: { requiresApproval: true, declaresMemory: true },
              created_at: "2026-05-19T00:00:00Z",
              updated_at: "2026-05-19T00:00:00Z",
            },
          ],
        }),
    },
  },
}));

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

describe("Step1Agent", () => {
  it("renders agents from the registry", async () => {
    wrap(<Step1Agent state={initialState} dispatch={() => {}} />);
    expect(await screen.findByText("demo")).toBeTruthy();
    expect(await screen.findByText("billing")).toBeTruthy();
  });

  it("dispatches SET_AGENT with agent snapshot derived from agent", async () => {
    const dispatch = vi.fn();
    wrap(<Step1Agent state={initialState} dispatch={dispatch} />);
    fireEvent.click(await screen.findByText("billing"));
    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledWith({
        type: "SET_AGENT",
        agent: expect.objectContaining({
          id: "a-2",
          name: "billing",
          framework: "crewai",
          version: "0.4.0",
          team: "t1",
        }),
      });
    });
  });

  it("highlights the currently selected agent with emerald accent", async () => {
    const state: DeployWizardState = { ...initialState, agentId: "a-1" };
    wrap(<Step1Agent state={state} dispatch={() => {}} />);
    const card = (await screen.findByText("demo")).closest("button");
    expect(card?.className).toMatch(/emerald/);
  });

  it("does not highlight unselected agents", async () => {
    const state: DeployWizardState = { ...initialState, agentId: "a-1" };
    wrap(<Step1Agent state={state} dispatch={() => {}} />);
    const card = (await screen.findByText("billing")).closest("button");
    expect(card?.className).not.toMatch(/emerald/);
  });

  it("shows loading state while fetching", () => {
    wrap(<Step1Agent state={initialState} dispatch={() => {}} />);
    expect(screen.getByText(/Loading agents/)).toBeTruthy();
  });

  it("shows error state and retry button on failure", async () => {
    vi.mocked(await import("@/lib/api")).api.agents.list = vi
      .fn()
      .mockRejectedValue(new Error("Network error"));
    wrap(<Step1Agent state={initialState} dispatch={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText(/Couldn't load agents/)).toBeTruthy();
    });
    expect(screen.getByText(/Retry/)).toBeTruthy();
  });
});
