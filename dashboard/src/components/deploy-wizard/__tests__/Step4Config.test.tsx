import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Step4Config } from "@/components/deploy-wizard/Step4Config";
import {
  initialState,
  type AgentSnapshot,
  type DeployWizardState,
} from "@/lib/deploy-wizard-state";

const agent: AgentSnapshot = {
  id: "a-1",
  name: "demo",
  framework: "langgraph",
  version: "1.0.0",
  team: "t1",
  requiresApproval: false,
  declaresMemory: true,
};

function state(extra: Partial<DeployWizardState> = {}): DeployWizardState {
  return {
    ...initialState,
    agentId: "a-1",
    agentSnapshot: agent,
    cloud: "gcp",
    region: "us-central1",
    infraMode: "provision",
    provisionAck: true,
    step: 4,
    ...extra,
  };
}

vi.mock("@/lib/api", () => ({
  api: {
    deployments: {
      createJob: vi.fn().mockResolvedValue({
        data: { job_id: "j-1", pending_approval: false },
      }),
    },
  },
}));

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("Step4Config", () => {
  it("renders env var add/remove", () => {
    wrap(<Step4Config state={state()} dispatch={() => {}} />);
    expect(screen.getByRole("button", { name: /Add env var/i })).toBeTruthy();
  });

  it("Deploy button label is 'Deploy' when approval not required", () => {
    wrap(<Step4Config state={state()} dispatch={() => {}} />);
    expect(screen.getByRole("button", { name: /^Deploy$/i })).toBeTruthy();
  });

  it("Deploy button label flips to 'Submit for approval' when agent requires approval", () => {
    const approvalAgent: AgentSnapshot = { ...agent, requiresApproval: true };
    wrap(<Step4Config state={state({ agentSnapshot: approvalAgent })} dispatch={() => {}} />);
    expect(screen.getByRole("button", { name: /Submit for approval/i })).toBeTruthy();
  });

  it("clicking Deploy dispatches SET_IDEMPOTENCY_KEY (lazily) then SUBMIT_DEPLOY", async () => {
    const dispatch = vi.fn();
    wrap(<Step4Config state={state()} dispatch={dispatch} />);
    fireEvent.click(screen.getByRole("button", { name: /^Deploy$/i }));
    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledWith(
        expect.objectContaining({ type: "SUBMIT_DEPLOY", jobId: "j-1" }),
      );
    });
    // SET_IDEMPOTENCY_KEY must have been dispatched before SUBMIT_DEPLOY.
    const types = dispatch.mock.calls.map((c) => c[0].type);
    const idx_key = types.indexOf("SET_IDEMPOTENCY_KEY");
    const idx_submit = types.indexOf("SUBMIT_DEPLOY");
    expect(idx_key).toBeGreaterThanOrEqual(0);
    expect(idx_submit).toBeGreaterThan(idx_key);
  });

  it("db tier selector only shown when agent declares memory", () => {
    const noMemAgent: AgentSnapshot = { ...agent, declaresMemory: false };
    wrap(
      <Step4Config state={state({ agentSnapshot: noMemAgent })} dispatch={() => {}} />,
    );
    expect(screen.queryByLabelText(/DB tier/i)).not.toBeTruthy();
  });

  it("db tier selector shown when agent declares memory", () => {
    wrap(<Step4Config state={state()} dispatch={() => {}} />);
    expect(screen.getByLabelText(/DB tier/i)).toBeTruthy();
  });
});
