import type { DeployEvent, DeployJobStatus } from "@/lib/deploy-events";

export type Cloud = "aws" | "gcp" | "azure";
export type Step = 1 | 2 | 3 | 4 | 5;
export type Origin = "sidebar" | "agent-detail" | "deploys" | "builder";
export type InfraMode = "byo" | "provision";

export interface AgentSnapshot {
  id: string;
  name: string;
  framework: string;
  version: string;
  team: string;
  requiresApproval: boolean;
  declaresMemory: boolean;
}

export interface ValidationCheck {
  resource: string;
  status: string;
  detail: string;
}

export interface ValidationResult {
  valid: boolean;
  checks: ValidationCheck[];
}

export interface EnvVar {
  key: string;
  value: string;
}

export interface Scaling {
  min: number;
  max: number;
  cpuTargetPct: number;
}

export interface DeployWizardState {
  step: Step;
  agentId: string | null;
  agentSnapshot: AgentSnapshot | null;
  cloud: Cloud | null;
  region: string | null;
  infraMode: InfraMode | null;
  byoFields: Record<string, string>;
  validateResult: ValidationResult | null;
  provisionAck: boolean;
  envVars: EnvVar[];
  secrets: string[];
  scaling: Scaling;
  dbTier: string | null;
  jobId: string | null;
  jobStatus: DeployJobStatus | null;
  endpointUrl: string | null;
  approvalPending: boolean;
  origin: Origin;
  draftSavedAt: number | null;
}

export const initialState: DeployWizardState = {
  step: 1,
  agentId: null,
  agentSnapshot: null,
  cloud: null,
  region: null,
  infraMode: null,
  byoFields: {},
  validateResult: null,
  provisionAck: false,
  envVars: [],
  secrets: [],
  scaling: { min: 1, max: 3, cpuTargetPct: 70 },
  dbTier: null,
  jobId: null,
  jobStatus: null,
  endpointUrl: null,
  approvalPending: false,
  origin: "sidebar",
  draftSavedAt: null,
};

export type Action =
  | { type: "HYDRATE_FROM_DRAFT"; state: Partial<DeployWizardState> }
  | { type: "PREFILL_FROM_QUERY"; agentId?: string; from?: Origin; step?: Step }
  | { type: "GOTO"; step: Step }
  | { type: "SET_AGENT"; agent: AgentSnapshot }
  | { type: "SET_CLOUD_REGION"; cloud: Cloud; region: string }
  | { type: "SET_INFRA_MODE"; mode: InfraMode }
  | { type: "SET_BYO_FIELD"; key: string; value: string }
  | { type: "SET_VALIDATION"; result: ValidationResult }
  | { type: "ACK_PROVISION" }
  | { type: "SET_ENV_VAR"; key: string; value: string }
  | { type: "REMOVE_ENV_VAR"; key: string }
  | { type: "SET_SECRETS"; refs: string[] }
  | { type: "SET_SCALING"; scaling: Scaling }
  | { type: "SET_DB_TIER"; tier: string }
  | { type: "SUBMIT_DEPLOY"; jobId: string; pendingApproval: boolean }
  | { type: "SSE_EVENT"; event: DeployEvent }
  | { type: "RESET" };

export function reducer(state: DeployWizardState, action: Action): DeployWizardState {
  switch (action.type) {
    case "HYDRATE_FROM_DRAFT":
      return { ...state, ...action.state };

    case "PREFILL_FROM_QUERY":
      return {
        ...state,
        agentId: action.agentId ?? state.agentId,
        origin: action.from ?? state.origin,
        step: action.step ?? state.step,
      };

    case "GOTO":
      return { ...state, step: action.step };

    case "SET_AGENT":
      // Picking a different agent invalidates downstream choices.
      return {
        ...initialState,
        agentId: action.agent.id,
        agentSnapshot: action.agent,
        origin: state.origin,
        step: 2,
      };

    case "SET_CLOUD_REGION":
      return {
        ...state,
        cloud: action.cloud,
        region: action.region,
        byoFields: {},
        validateResult: null,
        provisionAck: false,
      };

    case "SET_INFRA_MODE":
      return { ...state, infraMode: action.mode };

    case "SET_BYO_FIELD":
      return {
        ...state,
        byoFields: { ...state.byoFields, [action.key]: action.value },
        validateResult: null,
      };

    case "SET_VALIDATION":
      return { ...state, validateResult: action.result };

    case "ACK_PROVISION":
      return { ...state, provisionAck: true };

    case "SET_ENV_VAR":
      return {
        ...state,
        envVars: [
          ...state.envVars.filter((e) => e.key !== action.key),
          { key: action.key, value: action.value },
        ],
      };

    case "REMOVE_ENV_VAR":
      return { ...state, envVars: state.envVars.filter((e) => e.key !== action.key) };

    case "SET_SECRETS":
      return { ...state, secrets: action.refs };

    case "SET_SCALING":
      return { ...state, scaling: action.scaling };

    case "SET_DB_TIER":
      return { ...state, dbTier: action.tier };

    case "SUBMIT_DEPLOY":
      return {
        ...state,
        step: 5,
        jobId: action.jobId,
        approvalPending: action.pendingApproval,
        jobStatus: action.pendingApproval ? "pending_approval" : "pending",
      };

    case "SSE_EVENT": {
      const { event } = action;
      if (event.type === "phase" && event.phase) {
        return { ...state, jobStatus: event.phase as DeployJobStatus };
      }
      if (event.type === "complete") {
        return { ...state, jobStatus: "completed", endpointUrl: event.endpoint_url };
      }
      if (event.type === "error") {
        return { ...state, jobStatus: "failed" };
      }
      return state;
    }

    case "RESET":
      return initialState;
  }
}

export function canAdvance(state: DeployWizardState, target: Step): boolean {
  if (target > 5 || target < 1) return false; // bounds check first
  if (target <= state.step) return true; // backwards always allowed
  if (target >= 2 && !state.agentSnapshot) return false;
  if (target >= 3 && (!state.cloud || !state.region)) return false;
  if (target >= 4) {
    if (!state.infraMode) return false;
    if (state.infraMode === "byo" && !state.validateResult?.valid) return false;
    if (state.infraMode === "provision" && !state.provisionAck) return false;
  }
  if (target === 5) {
    return !!state.jobId;
  }
  return true;
}
