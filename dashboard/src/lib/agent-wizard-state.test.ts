import { describe, it, expect } from "vitest";
import {
  initialState,
  reducer,
  canAdvance,
  canCreate,
  deployTargetToCloud,
  recommendationToFormData,
  type AgentWizardState,
} from "./agent-wizard-state";
import type { Recommendation } from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeState(overrides: Partial<AgentWizardState> = {}): AgentWizardState {
  return { ...initialState, ...overrides };
}

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
    framework: "CrewAI is best for multi-agent orchestration",
    model_primary: "GPT-4o handles complex reasoning well",
  },
};

// ---------------------------------------------------------------------------
// initialState
// ---------------------------------------------------------------------------

describe("initialState", () => {
  it("starts at step 1", () => {
    expect(initialState.step).toBe(1);
  });

  it("has empty businessGoal and workflow", () => {
    expect(initialState.businessGoal).toBe("");
    expect(initialState.workflow).toBe("");
  });

  it("has null recommendation", () => {
    expect(initialState.recommendation).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// canAdvance
// ---------------------------------------------------------------------------

describe("canAdvance", () => {
  it("allows backward navigation always", () => {
    const state = makeState({ step: 3, businessGoal: "", workflow: "" });
    expect(canAdvance(state, 1)).toBe(true);
    expect(canAdvance(state, 2)).toBe(true);
  });

  it("blocks advance to step 2 when businessGoal is empty", () => {
    const state = makeState({ step: 1, businessGoal: "" });
    expect(canAdvance(state, 2)).toBe(false);
  });

  it("allows advance to step 2 when businessGoal is filled", () => {
    const state = makeState({ step: 1, businessGoal: "Handle support tickets" });
    expect(canAdvance(state, 2)).toBe(true);
  });

  it("blocks advance to step 3 when workflow is empty", () => {
    const state = makeState({
      step: 2,
      businessGoal: "Handle support tickets",
      workflow: "",
    });
    expect(canAdvance(state, 3)).toBe(false);
  });

  it("allows advance to step 3 when workflow is filled", () => {
    const state = makeState({
      step: 2,
      businessGoal: "Handle support tickets",
      workflow: "1. Receive ticket\n2. Classify\n3. Respond",
    });
    expect(canAdvance(state, 3)).toBe(true);
  });

  it("blocks advance to step 4 when recommendation is null", () => {
    const state = makeState({
      step: 3,
      businessGoal: "Handle support tickets",
      workflow: "1. Receive ticket",
      recommendation: null,
    });
    expect(canAdvance(state, 4)).toBe(false);
  });

  it("allows advance to step 4 when recommendation is set", () => {
    const state = makeState({
      step: 3,
      businessGoal: "Handle support tickets",
      workflow: "1. Receive ticket",
      recommendation: MOCK_RECOMMENDATION,
    });
    expect(canAdvance(state, 4)).toBe(true);
  });

  it("rejects out-of-bounds targets", () => {
    expect(canAdvance(initialState, 0 as 1)).toBe(false);
    expect(canAdvance(initialState, 5 as 4)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// canCreate
// ---------------------------------------------------------------------------

describe("canCreate", () => {
  it("blocks when name is empty", () => {
    const state = makeState({ name: "", owner: "alice@example.com" });
    expect(canCreate(state)).toBe(false);
  });

  it("blocks when name is not a valid slug", () => {
    const state = makeState({ name: "My Agent!", owner: "alice@example.com" });
    expect(canCreate(state)).toBe(false);
  });

  it("blocks when owner is not a valid email", () => {
    const state = makeState({ name: "my-agent", owner: "not-an-email" });
    expect(canCreate(state)).toBe(false);
  });

  it("allows when name is valid slug and owner is valid email", () => {
    const state = makeState({ name: "my-agent", owner: "alice@example.com" });
    expect(canCreate(state)).toBe(true);
  });

  it("allows single-char slug", () => {
    const state = makeState({ name: "a", owner: "alice@example.com" });
    expect(canCreate(state)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// deployTargetToCloud
// ---------------------------------------------------------------------------

describe("deployTargetToCloud", () => {
  it("maps ecs_fargate → aws", () => {
    expect(deployTargetToCloud("ecs_fargate")).toBe("aws");
  });

  it("maps cloud_run → gcp", () => {
    expect(deployTargetToCloud("cloud_run")).toBe("gcp");
  });

  it("maps azure_container_apps → azure", () => {
    expect(deployTargetToCloud("azure_container_apps")).toBe("azure");
  });

  it("maps docker_compose → local", () => {
    expect(deployTargetToCloud("docker_compose")).toBe("local");
  });

  it("returns local for unknown targets", () => {
    expect(deployTargetToCloud("unknown_target")).toBe("local");
  });
});

// ---------------------------------------------------------------------------
// reducer
// ---------------------------------------------------------------------------

describe("reducer", () => {
  it("SET_FIELD updates a top-level field", () => {
    const next = reducer(initialState, { type: "SET_FIELD", field: "businessGoal", value: "hello" });
    expect(next.businessGoal).toBe("hello");
  });

  it("SET_RECOMMENDATION seeds framework, modelPrimary, deployCloud", () => {
    const next = reducer(initialState, {
      type: "SET_RECOMMENDATION",
      recommendation: MOCK_RECOMMENDATION,
    });
    expect(next.recommendation).toBe(MOCK_RECOMMENDATION);
    expect(next.framework).toBe("crewai");
    expect(next.modelPrimary).toBe("gpt-4o");
    expect(next.deployCloud).toBe("gcp"); // cloud_run → gcp
  });

  it("GOTO_STEP updates the step", () => {
    const next = reducer(initialState, { type: "GOTO_STEP", step: 3 });
    expect(next.step).toBe(3);
  });

  it("RESET returns initial state", () => {
    const dirty = makeState({ businessGoal: "something", step: 3 });
    const next = reducer(dirty, { type: "RESET" });
    expect(next).toEqual(initialState);
  });
});

// ---------------------------------------------------------------------------
// recommendationToFormData
// ---------------------------------------------------------------------------

describe("recommendationToFormData", () => {
  it("populates the 7 required AgentFormData leaves from wizard state", () => {
    const state = makeState({
      name: "my-support-agent",
      version: "1.0.0",
      team: "engineering",
      owner: "alice@example.com",
      businessGoal: "Handle support tickets automatically",
      framework: "crewai",
      modelPrimary: "gpt-4o",
      deployCloud: "gcp",
      recommendation: MOCK_RECOMMENDATION,
    });

    const formData = recommendationToFormData(state);

    // 7 required leaves
    expect(formData.name).toBe("my-support-agent");
    expect(formData.version).toBe("1.0.0");
    expect(formData.team).toBe("engineering");
    expect(formData.owner).toBe("alice@example.com");
    expect(formData.framework).toBe("crewai");
    expect(formData.model.primary).toBe("gpt-4o");
    expect(formData.deploy.cloud).toBe("gcp");
  });

  it("uses the first line of businessGoal as description", () => {
    const state = makeState({
      name: "my-agent",
      owner: "user@test.com",
      businessGoal: "Line one of goal\nLine two",
    });
    const formData = recommendationToFormData(state);
    expect(formData.description).toBe("Line one of goal");
  });

  it("sets tags to empty array", () => {
    const state = makeState({ name: "my-agent", owner: "user@test.com" });
    const formData = recommendationToFormData(state);
    expect(formData.tags).toEqual([]);
  });

  it("sets sensible runtime for gcp cloud", () => {
    const state = makeState({ name: "a", owner: "u@t.com", deployCloud: "gcp" });
    const formData = recommendationToFormData(state);
    expect(formData.deploy.runtime).toBe("cloud-run");
  });

  it("sets sensible runtime for aws cloud", () => {
    const state = makeState({ name: "a", owner: "u@t.com", deployCloud: "aws" });
    const formData = recommendationToFormData(state);
    expect(formData.deploy.runtime).toBe("ecs-fargate");
  });

  it("uses typescript language when languagePreference is typescript", () => {
    const state = makeState({ name: "a", owner: "u@t.com", languagePreference: "typescript" });
    const formData = recommendationToFormData(state);
    expect(formData.language).toBe("typescript");
  });

  it("uses python language for python or none preference", () => {
    const s1 = makeState({ name: "a", owner: "u@t.com", languagePreference: "python" });
    const s2 = makeState({ name: "a", owner: "u@t.com", languagePreference: "none" });
    expect(recommendationToFormData(s1).language).toBe("python");
    expect(recommendationToFormData(s2).language).toBe("python");
  });
});
