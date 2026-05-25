/**
 * Tests for Step4Create — emit YAML, validate, create agent, navigate.
 */
import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { initialState, type AgentWizardState, type AgentWizardAction } from "@/lib/agent-wizard-state";
import { Step4Create } from "./Step4Create";
import type { Recommendation } from "@/lib/api";
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Mock API and Auth
// ---------------------------------------------------------------------------

vi.mock("@/lib/api", () => ({
  api: {
    agents: {
      validate: vi.fn(),
      fromYaml: vi.fn(),
    },
  },
}));

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: { id: "1", email: "alice@example.com", name: "Alice", role: "admin", team: "engineering" },
    token: "test-token",
    isLoading: false,
    login: vi.fn(),
    register: vi.fn(),
    changePassword: vi.fn(),
    logout: vi.fn(),
  }),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MOCK_RECOMMENDATION: Recommendation = {
  framework: "langgraph",
  code_tier: "low_code",
  model_primary: "claude-sonnet-4",
  rag: "vector",
  memory: "redis",
  mcp_a2a: "none",
  deploy_target: "ecs_fargate",
  eval_dimensions: ["latency"],
  reasoning: {},
};

function makeFilledState(overrides: Partial<AgentWizardState> = {}): AgentWizardState {
  return {
    ...initialState,
    businessGoal: "Handle support tickets",
    workflow: "1. Receive\n2. Respond",
    recommendation: MOCK_RECOMMENDATION,
    framework: "langgraph",
    modelPrimary: "claude-sonnet-4",
    deployCloud: "aws",
    name: "my-support-agent",
    version: "1.0.0",
    team: "engineering",
    owner: "alice@example.com",
    ...overrides,
  };
}

function makeDispatch() {
  return vi.fn() as React.Dispatch<AgentWizardAction>;
}

function renderWithRouter(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/agents/new"]}>
        <Routes>
          <Route path="/agents/new" element={ui} />
          <Route path="/agents/:id" element={<div data-testid="agent-detail">Agent Detail</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Step4Create", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders name, version, team, owner fields", () => {
    renderWithRouter(<Step4Create state={makeFilledState()} dispatch={makeDispatch()} />);
    expect(screen.getByTestId("agentName")).toBeInTheDocument();
    expect(screen.getByTestId("version")).toBeInTheDocument();
    expect(screen.getByTestId("team")).toBeInTheDocument();
    expect(screen.getByTestId("owner")).toBeInTheDocument();
  });

  it("prefills owner from auth user email via dispatch on mount", async () => {
    const dispatch = makeDispatch();
    renderWithRouter(<Step4Create state={makeFilledState({ owner: "" })} dispatch={dispatch} />);
    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledWith({
        type: "SET_FIELD",
        field: "owner",
        value: "alice@example.com",
      });
    });
  });

  it("shows the Create button disabled when name is empty", () => {
    renderWithRouter(<Step4Create state={makeFilledState({ name: "" })} dispatch={makeDispatch()} />);
    expect(screen.getByTestId("createAgent")).toBeDisabled();
  });

  it("shows the Create button disabled when owner is invalid email", () => {
    renderWithRouter(
      <Step4Create state={makeFilledState({ owner: "not-an-email" })} dispatch={makeDispatch()} />,
    );
    expect(screen.getByTestId("createAgent")).toBeDisabled();
  });

  it("enables Create button when name is valid slug and owner is valid email", () => {
    renderWithRouter(<Step4Create state={makeFilledState()} dispatch={makeDispatch()} />);
    expect(screen.getByTestId("createAgent")).not.toBeDisabled();
  });

  it("calls validate then fromYaml on Create click, then navigates", async () => {
    vi.mocked(api.agents.validate).mockResolvedValue({
      data: { valid: true, errors: [], warnings: [] },
      meta: { page: 1, per_page: 20, total: 0 },
      errors: [],
    });
    vi.mocked(api.agents.fromYaml).mockResolvedValue({
      data: {
        id: "new-agent-id",
        name: "my-support-agent",
        version: "1.0.0",
        description: "",
        team: "engineering",
        owner: "alice@example.com",
        framework: "langgraph",
        model_primary: "claude-sonnet-4",
        model_fallback: null,
        endpoint_url: null,
        status: "stopped",
        tags: [],
        config_snapshot: {},
        created_at: "2026-05-25T00:00:00Z",
        updated_at: "2026-05-25T00:00:00Z",
      },
      meta: { page: 1, per_page: 20, total: 0 },
      errors: [],
    });

    renderWithRouter(<Step4Create state={makeFilledState()} dispatch={makeDispatch()} />);
    fireEvent.click(screen.getByTestId("createAgent"));

    await waitFor(() => {
      expect(api.agents.validate).toHaveBeenCalledOnce();
      expect(api.agents.fromYaml).toHaveBeenCalledOnce();
    });

    // After success, should navigate to /agents/new-agent-id
    await waitFor(() => {
      expect(screen.getByTestId("agent-detail")).toBeInTheDocument();
    });
  });

  it("shows validation errors and does NOT call fromYaml when validate returns invalid", async () => {
    vi.mocked(api.agents.validate).mockResolvedValue({
      data: {
        valid: false,
        errors: [
          { path: "name", message: "Invalid slug", suggestion: "Use lowercase" },
        ],
        warnings: [],
      },
      meta: { page: 1, per_page: 20, total: 0 },
      errors: [],
    });

    renderWithRouter(<Step4Create state={makeFilledState()} dispatch={makeDispatch()} />);
    fireEvent.click(screen.getByTestId("createAgent"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-errors")).toBeInTheDocument();
      expect(screen.getByTestId("validation-errors")).toHaveTextContent("name: Invalid slug");
    });

    expect(api.agents.fromYaml).not.toHaveBeenCalled();
  });

  it("dispatches SET_FIELD when name input changes", () => {
    const dispatch = makeDispatch();
    renderWithRouter(<Step4Create state={makeFilledState()} dispatch={dispatch} />);
    fireEvent.change(screen.getByTestId("agentName"), { target: { value: "new-name" } });
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_FIELD",
      field: "name",
      value: "new-name",
    });
  });

  it("shows slug hint text", () => {
    renderWithRouter(<Step4Create state={makeFilledState()} dispatch={makeDispatch()} />);
    expect(screen.getByText(/lowercase letters, numbers, hyphens/i)).toBeInTheDocument();
  });
});
