// Hand-written counterpart to api/models/deploy_events.py.
// The codegen output at dashboard/src/lib/deploy-events.gen.ts (Task A8)
// matches this shape; this file is the import target for the rest of the
// dashboard until we wire the codegen into the typecheck step.

export type DeployEventType = "phase" | "log" | "complete" | "error";

export type DeployPhase =
  | "provisioning"
  | "building"
  | "pushing"
  | "deploying"
  | "health_checking"
  | "registering";

export type DeployJobStatus =
  | "pending"
  | "parsing"
  | "pending_approval"
  | "provisioning"
  | "building"
  | "pushing"
  | "deploying"
  | "health_checking"
  | "registering"
  | "completed"
  | "failed"
  | "timed_out";

export interface DeployEvent {
  type: DeployEventType;
  job_id: string;
  timestamp: string;
  phase: DeployPhase | null;
  step: number | null;
  total: number | null;
  message: string | null;
  level: "info" | "warn" | "error" | null;
  endpoint_url: string | null;
  error_code: string | null;
}
