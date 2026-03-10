import {
  FileCode,
  Shield,
  Package,
  Server,
  Rocket,
  HeartPulse,
  BookMarked,
  CheckCircle2,
  XCircle,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { DeployJobStatus } from "@/lib/api";

interface PipelineStep {
  key: DeployJobStatus;
  label: string;
  icon: typeof FileCode;
}

const STEPS: PipelineStep[] = [
  { key: "pending", label: "Queued", icon: FileCode },
  { key: "parsing", label: "Parse", icon: FileCode },
  { key: "building", label: "Build", icon: Package },
  { key: "provisioning", label: "Provision", icon: Server },
  { key: "deploying", label: "Deploy", icon: Rocket },
  { key: "health_checking", label: "Health", icon: HeartPulse },
  { key: "registering", label: "Register", icon: BookMarked },
  { key: "completed", label: "Done", icon: CheckCircle2 },
];

const STATUS_ORDER: Record<DeployJobStatus, number> = {
  pending: 0,
  parsing: 1,
  building: 2,
  provisioning: 3,
  deploying: 4,
  health_checking: 5,
  registering: 6,
  completed: 7,
  failed: -1,
};

function getStepState(
  step: PipelineStep,
  currentStatus: DeployJobStatus,
  failedStep?: DeployJobStatus
): "completed" | "active" | "failed" | "pending" {
  if (currentStatus === "failed") {
    const failAt = failedStep ?? currentStatus;
    const failIdx = STATUS_ORDER[failAt];
    const stepIdx = STATUS_ORDER[step.key];
    if (stepIdx < failIdx) return "completed";
    if (stepIdx === failIdx) return "failed";
    return "pending";
  }

  const currentIdx = STATUS_ORDER[currentStatus];
  const stepIdx = STATUS_ORDER[step.key];

  if (stepIdx < currentIdx) return "completed";
  if (stepIdx === currentIdx) return "active";
  return "pending";
}

interface DeployPipelineProps {
  status: DeployJobStatus;
  failedStep?: DeployJobStatus;
  errorMessage?: string | null;
  compact?: boolean;
}

export function DeployPipeline({
  status,
  failedStep,
  errorMessage,
  compact = false,
}: DeployPipelineProps) {
  if (status === "completed" && compact) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="size-3.5" />
        <span className="font-medium">Deployed successfully</span>
      </div>
    );
  }

  if (status === "failed" && compact) {
    return (
      <div className="space-y-1">
        <div className="flex items-center gap-1.5 text-xs text-destructive">
          <XCircle className="size-3.5" />
          <span className="font-medium">Deploy failed</span>
        </div>
        {errorMessage && (
          <p className="text-[10px] text-muted-foreground line-clamp-1">
            {errorMessage}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Pipeline steps */}
      <div className="flex items-center gap-0">
        {STEPS.map((step, i) => {
          const state = getStepState(step, status, failedStep);
          const Icon = state === "failed" ? XCircle : step.icon;

          return (
            <div key={step.key} className="flex items-center">
              {/* Step node */}
              <div className="flex flex-col items-center gap-1">
                <div
                  className={cn(
                    "flex size-8 items-center justify-center rounded-full border-2 transition-all",
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
                  ) : (
                    <Icon className="size-3.5" />
                  )}
                </div>
                <span
                  className={cn(
                    "text-[9px] font-medium leading-none",
                    state === "completed" && "text-emerald-600 dark:text-emerald-400",
                    state === "active" && "text-amber-600 dark:text-amber-400",
                    state === "failed" && "text-destructive",
                    state === "pending" && "text-muted-foreground/40"
                  )}
                >
                  {step.label}
                </span>
              </div>

              {/* Connector line */}
              {i < STEPS.length - 1 && (
                <div
                  className={cn(
                    "mx-0.5 h-0.5 w-4 rounded-full transition-all sm:w-6",
                    state === "completed"
                      ? "bg-emerald-500/40"
                      : "bg-border"
                  )}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* Error message */}
      {status === "failed" && errorMessage && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2">
          <p className="text-xs text-destructive">{errorMessage}</p>
        </div>
      )}
    </div>
  );
}
