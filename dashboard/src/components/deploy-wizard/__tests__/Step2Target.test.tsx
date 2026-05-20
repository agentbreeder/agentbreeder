import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Step2Target } from "@/components/deploy-wizard/Step2Target";
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

function stateWithAgent(
  extra: Partial<DeployWizardState> = {}
): DeployWizardState {
  return {
    ...initialState,
    agentId: "a-1",
    agentSnapshot: agent,
    step: 2,
    ...extra,
  };
}

describe("Step2Target", () => {
  it("renders three cloud cards", () => {
    render(<Step2Target state={stateWithAgent()} dispatch={() => {}} />);
    expect(screen.getByText(/AWS/i)).toBeTruthy();
    expect(screen.getByText(/GCP/i)).toBeTruthy();
    expect(screen.getByText(/Azure/i)).toBeTruthy();
  });

  it("dispatches SET_CLOUD_REGION when cloud + region are picked", () => {
    const dispatch = vi.fn();
    render(<Step2Target state={stateWithAgent()} dispatch={dispatch} />);
    fireEvent.click(screen.getByText(/GCP/i));
    // Pick a region from the select.
    const select = screen.getByLabelText(/Region/i) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "us-central1" } });
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_CLOUD_REGION",
      cloud: "gcp",
      region: "us-central1",
    });
  });

  it("shows a cost estimate after cloud + region are selected", () => {
    const state = stateWithAgent({ cloud: "gcp", region: "us-central1" });
    render(<Step2Target state={state} dispatch={() => {}} />);
    // Cost preview includes the cost estimate header and range.
    expect(screen.getByText("Cost estimate")).toBeTruthy();
    const costElements = screen.getAllByText(/\$.*\/mo/i);
    expect(costElements.length).toBeGreaterThan(0);
  });

  it("shows 'unsupported' fallback when region isn't in COST_TABLE", () => {
    const state = stateWithAgent({ cloud: "aws", region: "ap-mars-1" });
    render(<Step2Target state={state} dispatch={() => {}} />);
    expect(screen.getByText(/Cost estimate unavailable/i)).toBeTruthy();
  });

  it("highlights the currently selected cloud", () => {
    const state = stateWithAgent({ cloud: "azure" });
    render(<Step2Target state={state} dispatch={() => {}} />);
    const card = screen.getByText(/Azure/i).closest("button");
    expect(card?.className).toMatch(/emerald/);
  });
});
