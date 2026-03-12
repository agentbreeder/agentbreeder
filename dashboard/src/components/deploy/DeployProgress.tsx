/**
 * DeployProgress — 8-step pipeline visualization with real-time status updates.
 * Each step shows pending/running/success/failed with appropriate icons.
 */

import {
  FileCode,
  Shield,
  GitBranch,
  Package,
  Server,
  Rocket,
  HeartPulse,
  BookMarked,
  CheckCircle2,
  XCircle,
  Loader2,
  Circle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { DeployJobStatus } from "@/lib/api";

interface PipelineStep {
  key: string;
  dbStatus: DeployJobStatus;
  label: string;
  description: string;
  icon: typeof FileCode;
  order: number;
}

const STEPS: PipelineStep[] = [
  { key: "parsing", dbStatus: "parsing", label: "Parse", description: "Validate YAML schema", icon: FileCode, order: 0 },
  { key: "rbac", dbStatus: "parsing", label: "RBAC", description: "Check permissions", icon: Shield, order: 1 },
  { key: "resolving", dbStatus: "parsing", label: "Resolve", description: "Fetch dependencies", icon: GitBranch, order: 2 },
  { key: "building", dbStatus: "building", label: "Build", description: "Build container image", icon: Package, order: 3 },
  { key: "provisioning", dbStatus: "provisioning", label: "Provision", description: "Create infrastructure", icon: Server, order: 4 },
  { key: "deploying", dbStatus: "deploying", label: "Deploy", description: "Deploy container", icon: Rocket, order: 5 },
  { key: "health_checking", dbStatus: "health_checking", label: "Health", description: "Verify health", icon: HeartPulse, order: 6 },
  { key: "registering", dbStatus: "registering", label: "Register", description: "Update registry", icon: BookMarked, order: 7 },
];

// Map the DB status to a step order for comparison
const STATUS_ORDER: Record<string, number> = {
  pending: -1,
  parsing: 2,   // parsing covers steps 0-2
  building: 3,
  provisioning: 4,
  deploying: 5,
  health_checking: 6,
  registering: 7,
  completed: 8,
  failed: -2,
};

function getStepState(
  step: PipelineStep,
  currentStatus: DeployJobStatus,
  failedStep?: string,
): "completed" | "active" | "failed" | "pending" {
  if (currentStatus === "completed") return "completed";

  if (currentStatus === "failed") {
    // Determine which step failed
    const failOrder = failedStep
      ? (STEPS.find((s) => s.key === failedStep)?.order ?? STATUS_ORDER[failedStep] ?? -1)
      : STATUS_ORDER[currentStatus];
    if (step.order < failOrder) return "completed";
    if (step.order === failOrder) return "failed";
    return "pending";
  }

  const currentOrder = STATUS_ORDER[currentStatus] ?? -1;

  // For the "parsing" status which covers 3 sub-steps
  if (currentStatus === "parsing") {
    if (step.order < 2) return "completed";
    if (step.order === 2) return "active";
    return "pending";
  }

  if (step.order < currentOrder) return "completed";
  if (step.order === currentOrder) return "active";
  return "pending";
}

interface DeployProgressProps {
  status: DeployJobStatus;
  failedStep?: string;
  errorMessage?: string | null;
}

export function DeployProgress({
  status,
  failedStep,
  errorMessage,
}: DeployProgressProps) {
  const isComplete = status === "completed";
  const isFailed = status === "failed";

  return (
    <div className="space-y-3">
      {/* Step list */}
      <div className="space-y-0">
        {STEPS.map((step, i) => {
          const state = getStepState(step, status, failedStep);
          const StepIcon = step.icon;

          return (
            <div key={step.key} className="flex items-start gap-3">
              {/* Vertical line + node */}
              <div className="flex flex-col items-center">
                <div
                  className={cn(
                    "flex size-7 items-center justify-center rounded-full border-2 transition-all",
                    state === "completed" &&
                      "border-emerald-500 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
                    state === "active" &&
                      "border-amber-500 bg-amber-500/10 text-amber-600 dark:text-amber-400",
                    state === "failed" &&
                      "border-destructive bg-destructive/10 text-destructive",
                    state === "pending" &&
                      "border-border bg-muted/30 text-muted-foreground/40"
                  )}
                >
                  {state === "active" ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : state === "completed" ? (
                    <CheckCircle2 className="size-3.5" />
                  ) : state === "failed" ? (
                    <XCircle className="size-3.5" />
                  ) : (
                    <Circle className="size-2.5" />
                  )}
                </div>
                {/* Connector line */}
                {i < STEPS.length - 1 && (
                  <div
                    className={cn(
                      "w-0.5 h-5 transition-all",
                      state === "completed"
                        ? "bg-emerald-500/30"
                        : "bg-border"
                    )}
                  />
                )}
              </div>

              {/* Label */}
              <div className="min-w-0 flex-1 pb-3">
                <div className="flex items-center gap-2">
                  <StepIcon
                    className={cn(
                      "size-3 shrink-0",
                      state === "completed" && "text-emerald-600 dark:text-emerald-400",
                      state === "active" && "text-amber-600 dark:text-amber-400",
                      state === "failed" && "text-destructive",
                      state === "pending" && "text-muted-foreground/40"
                    )}
                  />
                  <span
                    className={cn(
                      "text-[11px] font-medium",
                      state === "completed" && "text-emerald-600 dark:text-emerald-400",
                      state === "active" && "text-amber-600 dark:text-amber-400",
                      state === "failed" && "text-destructive",
                      state === "pending" && "text-muted-foreground/40"
                    )}
                  >
                    {step.label}
                  </span>
                  <span
                    className={cn(
                      "text-[10px]",
                      state === "pending" ? "text-muted-foreground/30" : "text-muted-foreground"
                    )}
                  >
                    {step.description}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Completion / Error message */}
      {isComplete && (
        <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2">
          <div className="flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="size-3.5" />
            <span className="font-medium">Deployment successful</span>
          </div>
        </div>
      )}

      {isFailed && errorMessage && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2">
          <p className="text-xs text-destructive">{errorMessage}</p>
        </div>
      )}
    </div>
  );
}
