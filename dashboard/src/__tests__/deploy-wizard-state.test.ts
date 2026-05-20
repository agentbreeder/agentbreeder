import { describe, expect, it } from "vitest";
import {
  initialState,
  reducer,
  canAdvance,
  type DeployWizardState,
} from "@/lib/deploy-wizard-state";

const baseAgent = {
  id: "a-1",
  name: "demo",
  framework: "langgraph",
  version: "1.0.0",
  team: "t1",
  requiresApproval: false,
  declaresMemory: false,
};

describe("reducer", () => {
  it("HYDRATE_FROM_DRAFT replaces top-level fields and preserves shape", () => {
    const s = reducer(initialState, {
      type: "HYDRATE_FROM_DRAFT",
      state: { step: 3, cloud: "gcp", region: "us-central1" },
    });
    expect(s.step).toBe(3);
    expect(s.cloud).toBe("gcp");
    expect(s.region).toBe("us-central1");
    expect(s.envVars).toEqual([]);
  });

  it("SET_AGENT writes snapshot, clears downstream choices, advances to step 2", () => {
    let s: DeployWizardState = {
      ...initialState,
      cloud: "gcp",
      region: "us-central1",
      envVars: [{ key: "X", value: "Y" }],
    };
    s = reducer(s, { type: "SET_AGENT", agent: baseAgent });
    expect(s.agentId).toBe("a-1");
    expect(s.agentSnapshot?.name).toBe("demo");
    expect(s.envVars).toEqual([]);
    expect(s.cloud).toBeNull();
    expect(s.step).toBe(2);
  });

  it("SET_CLOUD_REGION clears infra mode + validation", () => {
    let s: DeployWizardState = {
      ...initialState,
      infraMode: "byo",
      validateResult: { valid: true, checks: [] },
    };
    s = reducer(s, { type: "SET_CLOUD_REGION", cloud: "aws", region: "us-east-1" });
    expect(s.cloud).toBe("aws");
    expect(s.region).toBe("us-east-1");
    expect(s.validateResult).toBeNull();
    expect(s.byoFields).toEqual({});
    expect(s.provisionAck).toBe(false);
  });

  it("SET_BYO_FIELD merges field, clears prior validation", () => {
    let s = reducer(initialState, {
      type: "SET_BYO_FIELD",
      key: "AWS_ECS_CLUSTER",
      value: "c1",
    });
    s = reducer(s, {
      type: "SET_BYO_FIELD",
      key: "AWS_EXECUTION_ROLE_ARN",
      value: "arn:1",
    });
    expect(s.byoFields).toEqual({
      AWS_ECS_CLUSTER: "c1",
      AWS_EXECUTION_ROLE_ARN: "arn:1",
    });
  });

  it("SET_VALIDATION records result", () => {
    let s = reducer(initialState, { type: "SET_INFRA_MODE", mode: "byo" });
    s = reducer(s, {
      type: "SET_VALIDATION",
      result: { valid: true, checks: [] },
    });
    expect(s.validateResult?.valid).toBe(true);
    expect(s.provisionAck).toBe(false);
  });

  it("ACK_PROVISION flips ack", () => {
    let s = reducer(initialState, { type: "SET_INFRA_MODE", mode: "provision" });
    s = reducer(s, { type: "ACK_PROVISION" });
    expect(s.provisionAck).toBe(true);
  });

  it("SET_ENV_VAR + REMOVE_ENV_VAR", () => {
    let s = reducer(initialState, {
      type: "SET_ENV_VAR",
      key: "LOG_LEVEL",
      value: "info",
    });
    expect(s.envVars).toEqual([{ key: "LOG_LEVEL", value: "info" }]);
    // Same key updates in place.
    s = reducer(s, {
      type: "SET_ENV_VAR",
      key: "LOG_LEVEL",
      value: "debug",
    });
    expect(s.envVars).toEqual([{ key: "LOG_LEVEL", value: "debug" }]);
    s = reducer(s, { type: "REMOVE_ENV_VAR", key: "LOG_LEVEL" });
    expect(s.envVars).toEqual([]);
  });

  it("SET_SECRETS + SET_SCALING + SET_DB_TIER", () => {
    let s = reducer(initialState, {
      type: "SET_SECRETS",
      refs: ["secret-a", "secret-b"],
    });
    expect(s.secrets).toEqual(["secret-a", "secret-b"]);
    s = reducer(s, {
      type: "SET_SCALING",
      scaling: { min: 2, max: 5, cpuTargetPct: 80 },
    });
    expect(s.scaling).toEqual({ min: 2, max: 5, cpuTargetPct: 80 });
    s = reducer(s, { type: "SET_DB_TIER", tier: "db-g1-small" });
    expect(s.dbTier).toBe("db-g1-small");
  });

  it("SUBMIT_DEPLOY records jobId and advances to step 5", () => {
    const s = reducer(
      { ...initialState, step: 4 },
      { type: "SUBMIT_DEPLOY", jobId: "j-1", pendingApproval: false }
    );
    expect(s.jobId).toBe("j-1");
    expect(s.step).toBe(5);
    expect(s.approvalPending).toBe(false);
    expect(s.jobStatus).toBe("pending");
  });

  it("SUBMIT_DEPLOY with pendingApproval sets jobStatus to pending_approval", () => {
    const s = reducer(
      { ...initialState, step: 4 },
      { type: "SUBMIT_DEPLOY", jobId: "j-1", pendingApproval: true }
    );
    expect(s.approvalPending).toBe(true);
    expect(s.jobStatus).toBe("pending_approval");
  });

  it("SSE_EVENT phase updates jobStatus", () => {
    const s = reducer(
      { ...initialState, step: 5, jobId: "j-1" },
      {
        type: "SSE_EVENT",
        event: {
          type: "phase",
          job_id: "j-1",
          timestamp: "",
          phase: "building",
          step: null,
          total: null,
          message: null,
          level: null,
          endpoint_url: null,
          error_code: null,
        },
      }
    );
    expect(s.jobStatus).toBe("building");
  });

  it("SSE_EVENT complete sets endpointUrl and jobStatus=completed", () => {
    const s = reducer(
      { ...initialState, step: 5, jobId: "j-1" },
      {
        type: "SSE_EVENT",
        event: {
          type: "complete",
          job_id: "j-1",
          timestamp: "",
          phase: null,
          step: null,
          total: null,
          message: null,
          level: null,
          endpoint_url: "https://x.example.com",
          error_code: null,
        },
      }
    );
    expect(s.jobStatus).toBe("completed");
    expect(s.endpointUrl).toBe("https://x.example.com");
  });

  it("SSE_EVENT error sets jobStatus=failed", () => {
    const s = reducer(
      { ...initialState, step: 5, jobId: "j-1" },
      {
        type: "SSE_EVENT",
        event: {
          type: "error",
          job_id: "j-1",
          timestamp: "",
          phase: null,
          step: null,
          total: null,
          message: "boom",
          level: null,
          endpoint_url: null,
          error_code: "RuntimeError",
        },
      }
    );
    expect(s.jobStatus).toBe("failed");
  });

  it("PREFILL_FROM_QUERY sets agentId, origin, step", () => {
    const s = reducer(initialState, {
      type: "PREFILL_FROM_QUERY",
      agentId: "a-99",
      from: "agent-detail",
      step: 3,
    });
    expect(s.agentId).toBe("a-99");
    expect(s.origin).toBe("agent-detail");
    expect(s.step).toBe(3);
  });

  it("GOTO changes step directly", () => {
    const s = reducer(initialState, { type: "GOTO", step: 4 });
    expect(s.step).toBe(4);
  });

  it("SET_INFRA_MODE sets mode", () => {
    const s = reducer(initialState, { type: "SET_INFRA_MODE", mode: "byo" });
    expect(s.infraMode).toBe("byo");
  });

  it("RESET returns to initialState", () => {
    const s = reducer(
      { ...initialState, step: 5, jobId: "j-1" },
      { type: "RESET" }
    );
    expect(s).toEqual(initialState);
  });

  it("SET_IDEMPOTENCY_KEY records the key", () => {
    const s = reducer(initialState, { type: "SET_IDEMPOTENCY_KEY", key: "uuid-1" });
    expect(s.idempotencyKey).toBe("uuid-1");
  });

  it("RESET clears the idempotencyKey", () => {
    const s = reducer(
      { ...initialState, idempotencyKey: "uuid-1" },
      { type: "RESET" }
    );
    expect(s.idempotencyKey).toBeNull();
  });

  it("multiple SET_ENV_VAR calls accumulate different keys", () => {
    let s = reducer(initialState, {
      type: "SET_ENV_VAR",
      key: "KEY_A",
      value: "val_a",
    });
    s = reducer(s, {
      type: "SET_ENV_VAR",
      key: "KEY_B",
      value: "val_b",
    });
    s = reducer(s, {
      type: "SET_ENV_VAR",
      key: "KEY_C",
      value: "val_c",
    });
    expect(s.envVars).toHaveLength(3);
    expect(s.envVars.map((e) => e.key)).toEqual(["KEY_A", "KEY_B", "KEY_C"]);
  });

  it("SET_ENV_VAR with existing key replaces value", () => {
    let s = reducer(initialState, {
      type: "SET_ENV_VAR",
      key: "KEY",
      value: "v1",
    });
    s = reducer(s, { type: "SET_ENV_VAR", key: "KEY", value: "v2" });
    s = reducer(s, { type: "SET_ENV_VAR", key: "KEY", value: "v3" });
    expect(s.envVars).toHaveLength(1);
    expect(s.envVars[0].value).toBe("v3");
  });

  it("REMOVE_ENV_VAR on non-existent key is safe", () => {
    const s = reducer(initialState, {
      type: "REMOVE_ENV_VAR",
      key: "NONEXISTENT",
    });
    expect(s.envVars).toEqual([]);
  });

  it("SET_SCALING partial update (only min)", () => {
    const s = reducer(initialState, {
      type: "SET_SCALING",
      scaling: { min: 5, max: 10, cpuTargetPct: 75 },
    });
    expect(s.scaling.min).toBe(5);
    expect(s.scaling.max).toBe(10);
    expect(s.scaling.cpuTargetPct).toBe(75);
  });

  it("SSE_EVENT with unknown event type is ignored", () => {
    const originalState = { ...initialState, step: 5, jobId: "j-1" };
    const s = reducer(originalState, {
      type: "SSE_EVENT",
      event: {
        type: "log",
        job_id: "j-1",
        timestamp: "",
        phase: null,
        step: null,
        total: null,
        message: "some log",
        level: "info",
        endpoint_url: null,
        error_code: null,
      },
    });
    expect(s.jobStatus).toBe(originalState.jobStatus);
  });
});

describe("canAdvance", () => {
  it("blocks Step 1 → 2 without agent", () => {
    expect(canAdvance(initialState, 2)).toBe(false);
  });

  it("allows Step 1 → 2 with agent", () => {
    const s = reducer(initialState, { type: "SET_AGENT", agent: baseAgent });
    expect(canAdvance(s, 2)).toBe(true);
  });

  it("blocks Step 2 → 3 without region", () => {
    const s: DeployWizardState = {
      ...initialState,
      agentId: "a-1",
      agentSnapshot: baseAgent,
      cloud: "gcp",
    };
    expect(canAdvance(s, 3)).toBe(false);
  });

  it("allows Step 2 → 3 with cloud and region", () => {
    const s: DeployWizardState = {
      ...initialState,
      agentId: "a-1",
      agentSnapshot: baseAgent,
      cloud: "gcp",
      region: "us-central1",
    };
    expect(canAdvance(s, 3)).toBe(true);
  });

  it("blocks Step 3 → 4 in BYO mode without successful validation", () => {
    const s: DeployWizardState = {
      ...initialState,
      agentId: "a-1",
      agentSnapshot: baseAgent,
      cloud: "gcp",
      region: "us-central1",
      infraMode: "byo",
      validateResult: { valid: false, checks: [] },
    };
    expect(canAdvance(s, 4)).toBe(false);
  });

  it("allows Step 3 → 4 in BYO mode with successful validation", () => {
    const s: DeployWizardState = {
      ...initialState,
      agentId: "a-1",
      agentSnapshot: baseAgent,
      cloud: "gcp",
      region: "us-central1",
      infraMode: "byo",
      validateResult: { valid: true, checks: [] },
    };
    expect(canAdvance(s, 4)).toBe(true);
  });

  it("blocks Step 3 → 4 in provision mode without ack", () => {
    const s: DeployWizardState = {
      ...initialState,
      agentId: "a-1",
      agentSnapshot: baseAgent,
      cloud: "gcp",
      region: "us-central1",
      infraMode: "provision",
    };
    expect(canAdvance(s, 4)).toBe(false);
  });

  it("allows Step 3 → 4 in provision mode with ack", () => {
    const s: DeployWizardState = {
      ...initialState,
      agentId: "a-1",
      agentSnapshot: baseAgent,
      cloud: "gcp",
      region: "us-central1",
      infraMode: "provision",
      provisionAck: true,
    };
    expect(canAdvance(s, 4)).toBe(true);
  });

  it("backwards-jump always allowed", () => {
    expect(canAdvance({ ...initialState, step: 5 }, 2)).toBe(true);
  });

  it("blocks step=5 from empty state (jobId required)", () => {
    expect(canAdvance(initialState, 5)).toBe(false);
  });

  it("allows step=5 once jobId is set", () => {
    const s: DeployWizardState = {
      ...initialState,
      agentId: "a-1",
      agentSnapshot: baseAgent,
      cloud: "gcp",
      region: "us-central1",
      infraMode: "provision",
      provisionAck: true,
      jobId: "j-1",
    };
    expect(canAdvance(s, 5)).toBe(true);
  });

  it("blocks advance when infraMode is not set (step >= 4)", () => {
    const s: DeployWizardState = {
      ...initialState,
      agentId: "a-1",
      agentSnapshot: baseAgent,
      cloud: "gcp",
      region: "us-central1",
      infraMode: null,
    };
    expect(canAdvance(s, 4)).toBe(false);
  });

  it("allows jump from step 1 to step 2 (forward with agent)", () => {
    const s = reducer(initialState, { type: "SET_AGENT", agent: baseAgent });
    expect(canAdvance(s, 2)).toBe(true);
  });

  it("blocks jump from step 1 to step 3 (missing cloud/region)", () => {
    const s = reducer(initialState, { type: "SET_AGENT", agent: baseAgent });
    expect(canAdvance(s, 3)).toBe(false);
  });

  it("allows jump from step 5 back to step 1", () => {
    const s: DeployWizardState = {
      ...initialState,
      step: 5,
      jobId: "j-1",
    };
    expect(canAdvance(s, 1)).toBe(true);
  });

  it("target=0 or target=6 returns false", () => {
    expect(canAdvance(initialState, 0 as any)).toBe(false);
    expect(canAdvance(initialState, 6 as any)).toBe(false);
  });

  it("canAdvance step 2→3 with cloud but no region", () => {
    const s: DeployWizardState = {
      ...initialState,
      agentId: "a-1",
      agentSnapshot: baseAgent,
      cloud: "aws",
      region: null,
    };
    expect(canAdvance(s, 3)).toBe(false);
  });

  it("canAdvance step 2→3 with region but no cloud", () => {
    const s: DeployWizardState = {
      ...initialState,
      agentId: "a-1",
      agentSnapshot: baseAgent,
      cloud: null,
      region: "us-east-1",
    };
    expect(canAdvance(s, 3)).toBe(false);
  });
});
