/**
 * Unit tests for agent wizard Step1, Step2, StepIndicator components.
 * Step3 and Step4 tests are in their own focused test blocks here.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { initialState, type AgentWizardState, type AgentWizardAction } from "@/lib/agent-wizard-state";
import { Step1Goal } from "./Step1Goal";
import { Step2Workflow } from "./Step2Workflow";
import { StepIndicator } from "./StepIndicator";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeState(overrides: Partial<AgentWizardState> = {}): AgentWizardState {
  return { ...initialState, ...overrides };
}

function makeDispatch() {
  return vi.fn<[AgentWizardAction], void>();
}

function withQuery(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

// ---------------------------------------------------------------------------
// StepIndicator
// ---------------------------------------------------------------------------

describe("StepIndicator", () => {
  it("renders 4 step buttons with correct labels", () => {
    render(
      withQuery(
        <StepIndicator current={1} canAdvanceTo={() => true} onJump={vi.fn()} />,
      ),
    );
    expect(screen.getByRole("button", { name: /Step 1: Goal/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Step 2: Workflow/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Step 3: Stack/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Step 4: Create/i })).toBeInTheDocument();
  });

  it("marks the current step with aria-current=step", () => {
    render(
      withQuery(
        <StepIndicator current={2} canAdvanceTo={() => true} onJump={vi.fn()} />,
      ),
    );
    expect(screen.getByRole("button", { name: /Step 2: Workflow/i })).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(screen.getByRole("button", { name: /Step 1: Goal/i })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("disables unreachable steps", () => {
    render(
      withQuery(
        <StepIndicator
          current={1}
          canAdvanceTo={(n) => n <= 1}
          onJump={vi.fn()}
        />,
      ),
    );
    expect(screen.getByRole("button", { name: /Step 2: Workflow/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Step 1: Goal/i })).not.toBeDisabled();
  });

  it("calls onJump when a reachable step is clicked", () => {
    const onJump = vi.fn();
    render(
      withQuery(
        <StepIndicator current={1} canAdvanceTo={() => true} onJump={onJump} />,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /Step 3: Stack/i }));
    expect(onJump).toHaveBeenCalledWith(3);
  });
});

// ---------------------------------------------------------------------------
// Step1Goal
// ---------------------------------------------------------------------------

describe("Step1Goal", () => {
  it("renders the goal textarea", () => {
    render(withQuery(<Step1Goal state={makeState()} dispatch={makeDispatch()} />));
    expect(screen.getByTestId("businessGoal")).toBeInTheDocument();
  });

  it("dispatches SET_FIELD when typing in businessGoal", () => {
    const dispatch = makeDispatch();
    render(withQuery(<Step1Goal state={makeState()} dispatch={dispatch} />));
    fireEvent.change(screen.getByTestId("businessGoal"), {
      target: { value: "Handle support tickets" },
    });
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_FIELD",
      field: "businessGoal",
      value: "Handle support tickets",
    });
  });

  it("renders cloud, scale, and language selects", () => {
    render(withQuery(<Step1Goal state={makeState()} dispatch={makeDispatch()} />));
    expect(screen.getByTestId("cloudPreference")).toBeInTheDocument();
    expect(screen.getByTestId("scaleProfile")).toBeInTheDocument();
    expect(screen.getByTestId("languagePreference")).toBeInTheDocument();
  });

  it("dispatches SET_FIELD when changing cloud preference", () => {
    const dispatch = makeDispatch();
    render(withQuery(<Step1Goal state={makeState()} dispatch={dispatch} />));
    fireEvent.change(screen.getByTestId("cloudPreference"), { target: { value: "gcp" } });
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_FIELD",
      field: "cloudPreference",
      value: "gcp",
    });
  });

  it("dispatches SET_FIELD when changing scale profile", () => {
    const dispatch = makeDispatch();
    render(withQuery(<Step1Goal state={makeState()} dispatch={dispatch} />));
    fireEvent.change(screen.getByTestId("scaleProfile"), { target: { value: "batch" } });
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_FIELD",
      field: "scaleProfile",
      value: "batch",
    });
  });

  it("dispatches SET_FIELD when changing language preference", () => {
    const dispatch = makeDispatch();
    render(withQuery(<Step1Goal state={makeState()} dispatch={dispatch} />));
    fireEvent.change(screen.getByTestId("languagePreference"), {
      target: { value: "typescript" },
    });
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_FIELD",
      field: "languagePreference",
      value: "typescript",
    });
  });

  it("shows the current state value in the businessGoal textarea", () => {
    const state = makeState({ businessGoal: "My existing goal" });
    render(withQuery(<Step1Goal state={state} dispatch={makeDispatch()} />));
    expect(screen.getByTestId("businessGoal")).toHaveValue("My existing goal");
  });
});

// ---------------------------------------------------------------------------
// Step2Workflow
// ---------------------------------------------------------------------------

describe("Step2Workflow", () => {
  it("renders the workflow textarea", () => {
    render(withQuery(<Step2Workflow state={makeState()} dispatch={makeDispatch()} />));
    expect(screen.getByTestId("workflow")).toBeInTheDocument();
  });

  it("dispatches SET_FIELD when typing workflow", () => {
    const dispatch = makeDispatch();
    render(withQuery(<Step2Workflow state={makeState()} dispatch={makeDispatch()} />));
    render(withQuery(<Step2Workflow state={makeState()} dispatch={dispatch} />));
    fireEvent.change(screen.getAllByTestId("workflow")[1], {
      target: { value: "1. Receive\n2. Classify" },
    });
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_FIELD",
      field: "workflow",
      value: "1. Receive\n2. Classify",
    });
  });

  it("renders all 4 state flag checkboxes", () => {
    render(withQuery(<Step2Workflow state={makeState()} dispatch={makeDispatch()} />));
    expect(screen.getByTestId("stateFlag-a")).toBeInTheDocument();
    expect(screen.getByTestId("stateFlag-b")).toBeInTheDocument();
    expect(screen.getByTestId("stateFlag-c")).toBeInTheDocument();
    expect(screen.getByTestId("stateFlag-d")).toBeInTheDocument();
  });

  it("renders all 4 data flag checkboxes", () => {
    render(withQuery(<Step2Workflow state={makeState()} dispatch={makeDispatch()} />));
    expect(screen.getByTestId("dataFlag-a")).toBeInTheDocument();
    expect(screen.getByTestId("dataFlag-b")).toBeInTheDocument();
    expect(screen.getByTestId("dataFlag-c")).toBeInTheDocument();
    expect(screen.getByTestId("dataFlag-d")).toBeInTheDocument();
  });

  it("dispatches SET_FIELD with toggled stateFlags when checking a box", () => {
    const dispatch = makeDispatch();
    const state = makeState({ stateFlags: [] });
    render(withQuery(<Step2Workflow state={state} dispatch={dispatch} />));
    fireEvent.click(screen.getByTestId("stateFlag-a"));
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_FIELD",
      field: "stateFlags",
      value: ["a"],
    });
  });

  it("dispatches SET_FIELD removing a flag when unchecking", () => {
    const dispatch = makeDispatch();
    const state = makeState({ stateFlags: ["a", "b"] });
    render(withQuery(<Step2Workflow state={state} dispatch={dispatch} />));
    fireEvent.click(screen.getByTestId("stateFlag-a"));
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_FIELD",
      field: "stateFlags",
      value: ["b"],
    });
  });

  it("dispatches SET_FIELD for dataFlags when checking a box", () => {
    const dispatch = makeDispatch();
    const state = makeState({ dataFlags: [] });
    render(withQuery(<Step2Workflow state={state} dispatch={dispatch} />));
    fireEvent.click(screen.getByTestId("dataFlag-c"));
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_FIELD",
      field: "dataFlags",
      value: ["c"],
    });
  });

  it("shows currently checked state flags from state", () => {
    const state = makeState({ stateFlags: ["b", "d"] });
    render(withQuery(<Step2Workflow state={state} dispatch={makeDispatch()} />));
    expect(screen.getByTestId("stateFlag-b")).toBeChecked();
    expect(screen.getByTestId("stateFlag-d")).toBeChecked();
    expect(screen.getByTestId("stateFlag-a")).not.toBeChecked();
  });
});
