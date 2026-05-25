/**
 * Tests for Step3Stack — the recommendation + editable stack step.
 */
import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { initialState, type AgentWizardState, type AgentWizardAction } from "@/lib/agent-wizard-state";
import { Step3Stack } from "./Step3Stack";
import type { Recommendation } from "@/lib/api";
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Mock API
// ---------------------------------------------------------------------------

const MOCK_RECOMMENDATION: Recommendation = {
  framework: "crewai",
  code_tier: "low_code",
  model_primary: "gpt-4o",
  rag: "vector",
  memory: "redis",
  mcp_a2a: "mcp",
  deploy_target: "cloud_run",
  eval_dimensions: ["latency", "accuracy"],
  reasoning: {
    framework: "CrewAI best for multi-agent orchestration",
    model_primary: "GPT-4o handles complex reasoning",
    rag: "Vector search fits unstructured docs",
    memory: "Redis for low-latency memory access",
    mcp_a2a: "MCP tooling is well-supported",
    eval_dimensions: "Latency and accuracy are key for this use case",
  },
};

vi.mock("@/lib/api", () => ({
  api: {
    builders: {
      recommend: vi.fn(),
    },
  },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeState(overrides: Partial<AgentWizardState> = {}): AgentWizardState {
  return {
    ...initialState,
    businessGoal: "Handle support tickets",
    workflow: "1. Receive\n2. Classify\n3. Respond",
    ...overrides,
  };
}

function makeDispatch() {
  return vi.fn() as React.Dispatch<AgentWizardAction>;
}

function withQuery(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Step3Stack", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.builders.recommend).mockResolvedValue({
      data: MOCK_RECOMMENDATION,
      meta: { page: 1, per_page: 20, total: 0 },
      errors: [],
    });
  });

  it("calls recommend on mount and seeds editable fields", async () => {
    const dispatch = makeDispatch();
    render(withQuery(<Step3Stack state={makeState()} dispatch={dispatch} />));

    // Wait for recommendation to resolve and dispatch to be called
    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "SET_RECOMMENDATION",
          recommendation: MOCK_RECOMMENDATION,
        }),
      );
    });
  });

  it("shows framework select with the seeded value", async () => {
    const state = makeState({
      framework: "crewai",
      recommendation: MOCK_RECOMMENDATION,
    });
    render(withQuery(<Step3Stack state={state} dispatch={makeDispatch()} />));
    const select = screen.getByTestId("framework") as HTMLSelectElement;
    expect(select.value).toBe("crewai");
  });

  it("shows modelPrimary input with the seeded value", () => {
    const state = makeState({
      modelPrimary: "gpt-4o",
      recommendation: MOCK_RECOMMENDATION,
    });
    render(withQuery(<Step3Stack state={state} dispatch={makeDispatch()} />));
    const input = screen.getByTestId("modelPrimary") as HTMLInputElement;
    expect(input.value).toBe("gpt-4o");
  });

  it("shows deployCloud select with the seeded value", () => {
    const state = makeState({
      deployCloud: "gcp",
      recommendation: MOCK_RECOMMENDATION,
    });
    render(withQuery(<Step3Stack state={state} dispatch={makeDispatch()} />));
    const select = screen.getByTestId("deployCloud") as HTMLSelectElement;
    expect(select.value).toBe("gcp");
  });

  it("dispatches SET_FIELD when framework is changed", () => {
    const dispatch = makeDispatch();
    const state = makeState({ framework: "langgraph", recommendation: MOCK_RECOMMENDATION });
    render(withQuery(<Step3Stack state={state} dispatch={dispatch} />));
    fireEvent.change(screen.getByTestId("framework"), { target: { value: "claude_sdk" } });
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_FIELD",
      field: "framework",
      value: "claude_sdk",
    });
  });

  it("dispatches SET_FIELD when modelPrimary is changed", () => {
    const dispatch = makeDispatch();
    const state = makeState({ modelPrimary: "gpt-4o", recommendation: MOCK_RECOMMENDATION });
    render(withQuery(<Step3Stack state={state} dispatch={dispatch} />));
    fireEvent.change(screen.getByTestId("modelPrimary"), {
      target: { value: "claude-opus-4" },
    });
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_FIELD",
      field: "modelPrimary",
      value: "claude-opus-4",
    });
  });

  it("shows guidance cards for rag and memory when recommendation is set", () => {
    const state = makeState({ recommendation: MOCK_RECOMMENDATION });
    render(withQuery(<Step3Stack state={state} dispatch={makeDispatch()} />));
    // Guidance cards rendered with data-testid
    expect(screen.getByTestId("guidance-rag-knowledge")).toBeInTheDocument();
    expect(screen.getByTestId("guidance-memory")).toBeInTheDocument();
  });

  it("shows reasoning text in guidance cards", () => {
    const state = makeState({ recommendation: MOCK_RECOMMENDATION });
    render(withQuery(<Step3Stack state={state} dispatch={makeDispatch()} />));
    expect(screen.getByText("Vector search fits unstructured docs")).toBeInTheDocument();
    expect(screen.getByText("Redis for low-latency memory access")).toBeInTheDocument();
  });

  it("does not show guidance cards when recommendation is null", () => {
    const state = makeState({ recommendation: null });
    render(withQuery(<Step3Stack state={state} dispatch={makeDispatch()} />));
    expect(screen.queryByTestId("guidance-rag-/-knowledge")).not.toBeInTheDocument();
  });
});
