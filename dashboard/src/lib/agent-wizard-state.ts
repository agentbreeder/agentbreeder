/**
 * Agent Wizard state — useReducer state, actions, canAdvance, and the
 * recommendation → AgentFormData mapping.
 *
 * Mirrors the deploy-wizard-state.ts pattern.
 */
import type { Recommendation } from "@/lib/api";
import { emptyFormData, type AgentFormData } from "@/lib/agent-yaml-emit";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type WizardStep = 1 | 2 | 3 | 4;

export interface AgentWizardState {
  step: WizardStep;
  // Step 1 — Goal
  businessGoal: string;
  cloudPreference: "aws" | "gcp" | "azure" | "local";
  scaleProfile: "realtime" | "batch" | "event_driven" | "low_volume";
  languagePreference: "python" | "typescript" | "none";
  // Step 2 — Workflow
  workflow: string; // textarea, one step per line
  stateFlags: string[]; // subset of a..e
  dataFlags: string[]; // subset of a..e
  // Step 3 — Recommendation (editable)
  recommendation: Recommendation | null;
  framework: string; // editable, seeded from recommendation
  modelPrimary: string; // editable
  deployCloud: string; // editable, mapped from deploy_target
  // Step 4 — Name & create
  name: string;
  version: string;
  team: string;
  owner: string;
}

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

export const initialState: AgentWizardState = {
  step: 1,
  businessGoal: "",
  cloudPreference: "aws",
  scaleProfile: "realtime",
  languagePreference: "python",
  workflow: "",
  stateFlags: [],
  dataFlags: [],
  recommendation: null,
  framework: "langgraph",
  modelPrimary: "claude-sonnet-4",
  deployCloud: "aws",
  name: "",
  version: "1.0.0",
  team: "engineering",
  owner: "",
};

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

export type AgentWizardAction =
  | { type: "SET_FIELD"; field: keyof AgentWizardState; value: AgentWizardState[keyof AgentWizardState] }
  | { type: "SET_RECOMMENDATION"; recommendation: Recommendation }
  | { type: "GOTO_STEP"; step: WizardStep }
  | { type: "RESET" };

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

export function reducer(state: AgentWizardState, action: AgentWizardAction): AgentWizardState {
  switch (action.type) {
    case "SET_FIELD":
      return { ...state, [action.field]: action.value };

    case "SET_RECOMMENDATION": {
      const rec = action.recommendation;
      return {
        ...state,
        recommendation: rec,
        framework: rec.framework,
        modelPrimary: rec.model_primary,
        deployCloud: deployTargetToCloud(rec.deploy_target),
      };
    }

    case "GOTO_STEP":
      return { ...state, step: action.step };

    case "RESET":
      return initialState;
  }
}

// ---------------------------------------------------------------------------
// canAdvance guard
// ---------------------------------------------------------------------------

/** Returns true if the wizard can move to the target step given the current state. */
export function canAdvance(state: AgentWizardState, target: WizardStep): boolean {
  // Bounds check
  if (target < 1 || target > 4) return false;
  // Backwards navigation always allowed
  if (target <= state.step) return true;
  // To step 2: need businessGoal
  if (target >= 2 && !state.businessGoal.trim()) return false;
  // To step 3: need workflow
  if (target >= 3 && !state.workflow.trim()) return false;
  // To step 4: need a completed recommendation
  if (target >= 4 && state.recommendation === null) return false;
  return true;
}

/** Returns true if the final Create action can proceed. */
export function canCreate(state: AgentWizardState): boolean {
  if (!state.name.trim()) return false;
  if (!SLUG_RE.test(state.name.trim())) return false;
  if (!state.owner.trim()) return false;
  if (!EMAIL_RE.test(state.owner.trim())) return false;
  return true;
}

// Regex constants — used both in canCreate and in Step4 for validation hints.
export const SLUG_RE = /^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$/;
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// ---------------------------------------------------------------------------
// deploy_target → deploy.cloud mapping
// ---------------------------------------------------------------------------

const DEPLOY_TARGET_MAP: Record<string, string> = {
  ecs_fargate: "aws",
  cloud_run: "gcp",
  azure_container_apps: "azure",
  docker_compose: "local",
};

export function deployTargetToCloud(target: string): string {
  return DEPLOY_TARGET_MAP[target] ?? "local";
}

// ---------------------------------------------------------------------------
// recommendationToFormData — assemble the AgentFormData for YAML emit
// ---------------------------------------------------------------------------

export function recommendationToFormData(state: AgentWizardState): AgentFormData {
  const data = emptyFormData();

  data.name = state.name.trim() || "my-agent";
  data.version = state.version.trim() || "1.0.0";
  data.description = state.businessGoal.split("\n")[0].trim();
  data.team = state.team.trim() || "engineering";
  data.owner = state.owner.trim() || "user@example.com";
  data.tags = [];

  // Framework / language — the wizard only supports python workflows for now
  // (all 5 recommend frameworks are Python-side). Typescript paths stay possible.
  data.language = state.languagePreference === "typescript" ? "typescript" : "python";
  data.framework = state.framework || "langgraph";

  data.model.primary = state.modelPrimary || "claude-sonnet-4";

  data.deploy.cloud = state.deployCloud || "local";
  // Set a sensible default runtime per cloud
  const CLOUD_RUNTIME: Record<string, string> = {
    aws: "ecs-fargate",
    gcp: "cloud-run",
    azure: "container-apps",
    local: "docker-compose",
  };
  data.deploy.runtime = CLOUD_RUNTIME[data.deploy.cloud] ?? "docker-compose";

  return data;
}
