import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Step3Infra } from "@/components/deploy-wizard/Step3Infra";
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
    step: 3,
    ...extra,
  };
}

vi.mock("@/lib/api", () => ({
  api: {
    deployments: {
      cloudRequirements: () =>
        Promise.resolve({
          data: {
            fields: [
              {
                name: "GOOGLE_CLOUD_PROJECT",
                required: true,
                description: "Project ID",
              },
              {
                name: "GCP_REGION",
                required: false,
                description: "Region",
              },
            ],
          },
        }),
      validateInfra: () =>
        Promise.resolve({
          data: {
            valid: true,
            checks: [
              {
                resource: "project:test",
                status: "found",
                detail: "ACTIVE",
              },
            ],
          },
        }),
    },
  },
}));

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>
  );
}

describe("Step3Infra", () => {
  it("renders both mode radios", () => {
    wrap(<Step3Infra state={state()} dispatch={() => {}} />);
    expect(screen.getByLabelText(/Bring Your Own/i)).toBeTruthy();
    expect(screen.getByLabelText(/Provision for me/i)).toBeTruthy();
  });

  it("dispatches SET_INFRA_MODE when a mode is picked", () => {
    const dispatch = vi.fn();
    wrap(<Step3Infra state={state()} dispatch={dispatch} />);
    fireEvent.click(screen.getByLabelText(/Provision for me/i));
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_INFRA_MODE",
      mode: "provision",
    });
  });

  it("BYO mode renders the validate panel with fetched fields", async () => {
    wrap(
      <Step3Infra state={state({ infraMode: "byo" })} dispatch={() => {}} />
    );
    expect(await screen.findByText(/GOOGLE_CLOUD_PROJECT/i)).toBeTruthy();
  });

  it("Provision mode renders the resource preview tree + ack checkbox", () => {
    wrap(
      <Step3Infra state={state({ infraMode: "provision" })} dispatch={() => {}} />
    );
    expect(screen.getByText(/will create/i)).toBeTruthy();
    expect(screen.getByLabelText(/I understand/i)).toBeTruthy();
  });

  it("ack checkbox dispatches ACK_PROVISION when checked", () => {
    const dispatch = vi.fn();
    wrap(
      <Step3Infra state={state({ infraMode: "provision" })} dispatch={dispatch} />
    );
    fireEvent.click(screen.getByLabelText(/I understand/i));
    expect(dispatch).toHaveBeenCalledWith({ type: "ACK_PROVISION" });
  });
});
