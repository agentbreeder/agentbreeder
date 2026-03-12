/**
 * DeployDialog — Full deploy-from-dashboard modal.
 *
 * Features:
 * - Target selector (Local Docker / Google Cloud Run)
 * - Pre-deploy validation checklist
 * - 8-step pipeline visualization with real-time progress
 * - Log streaming panel
 * - Cancel / Rollback buttons
 */

import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Rocket,
  Monitor,
  Cloud,
  AlertCircle,
  CheckCircle2,
  AlertTriangle,
  RotateCcw,
  Square,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { api, type DeployJobStatus } from "@/lib/api";
import { DeployProgress } from "./DeployProgress";
import { DeployLogs, type LogEntry } from "./DeployLogs";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DeployTarget {
  value: string;
  label: string;
  icon: typeof Monitor;
  description: string;
  available: boolean;
}

const DEPLOY_TARGETS: DeployTarget[] = [
  {
    value: "local",
    label: "Local Docker",
    icon: Monitor,
    description: "Deploy to local Docker Compose environment",
    available: true,
  },
  {
    value: "gcp",
    label: "Google Cloud Run",
    icon: Cloud,
    description: "Deploy to GCP Cloud Run (serverless)",
    available: true,
  },
];

interface ValidationItem {
  key: string;
  label: string;
  status: "pass" | "fail" | "warn";
  message: string;
}

const ACTIVE_STATUSES = new Set<DeployJobStatus>([
  "pending",
  "parsing",
  "building",
  "provisioning",
  "deploying",
  "health_checking",
  "registering",
]);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parseYamlField(yaml: string, field: string): string {
  const regex = new RegExp(`^${field}:\\s*(.+)`, "m");
  const match = yaml.match(regex);
  return match ? match[1].trim().replace(/^"|"$/g, "") : "";
}

function parseNestedYamlField(yaml: string, section: string, field: string): string {
  const sectionRegex = new RegExp(`^${section}:\\s*\\n((?:\\s+.+\\n?)*)`, "m");
  const sectionMatch = yaml.match(sectionRegex);
  if (!sectionMatch) return "";
  const fieldRegex = new RegExp(`^\\s+${field}:\\s*(.+)`, "m");
  const fieldMatch = sectionMatch[1].match(fieldRegex);
  return fieldMatch ? fieldMatch[1].trim().replace(/^"|"$/g, "") : "";
}

function runPreDeployValidation(yaml: string): ValidationItem[] {
  const items: ValidationItem[] = [];

  // Name
  const name = parseYamlField(yaml, "name");
  if (!name) {
    items.push({ key: "name", label: "Agent Name", status: "fail", message: "Name is required" });
  } else {
    items.push({ key: "name", label: "Agent Name", status: "pass", message: name });
  }

  // Version
  const version = parseYamlField(yaml, "version");
  const versionValid = /^\d+\.\d+\.\d+$/.test(version);
  items.push({
    key: "version",
    label: "Version",
    status: versionValid ? "pass" : "fail",
    message: versionValid ? `v${version}` : "Must be semver",
  });

  // Model
  const model = parseNestedYamlField(yaml, "model", "primary");
  if (model) {
    items.push({ key: "model", label: "Model Available", status: "pass", message: model });
  } else {
    items.push({ key: "model", label: "Model Available", status: "fail", message: "No primary model" });
  }

  // Framework
  const framework = parseYamlField(yaml, "framework");
  const knownFrameworks = ["langgraph", "crewai", "claude_sdk", "openai_agents", "google_adk", "custom"];
  if (knownFrameworks.includes(framework)) {
    items.push({ key: "framework", label: "Framework", status: "pass", message: framework });
  } else {
    items.push({ key: "framework", label: "Framework", status: "fail", message: `Unknown: ${framework}` });
  }

  // Team / Owner
  const team = parseYamlField(yaml, "team");
  const owner = parseYamlField(yaml, "owner");
  if (team && owner) {
    items.push({ key: "team", label: "Team & Owner", status: "pass", message: `${team} / ${owner}` });
  } else {
    items.push({ key: "team", label: "Team & Owner", status: "warn", message: "Team or owner missing" });
  }

  // Registry refs (tools)
  const toolsMatch = yaml.match(/tools:\s*\[?\]?/);
  const toolRefs = yaml.match(/- ref:\s*(.+)/g);
  if (toolRefs && toolRefs.length > 0) {
    items.push({ key: "refs", label: "Registry Refs", status: "pass", message: `${toolRefs.length} tool ref(s) resolved` });
  } else if (toolsMatch) {
    items.push({ key: "refs", label: "Registry Refs", status: "warn", message: "No tool references" });
  }

  return items;
}

function ValidationStatusIcon({ status }: { status: "pass" | "fail" | "warn" }) {
  switch (status) {
    case "pass":
      return <CheckCircle2 className="size-3.5 text-emerald-500" />;
    case "fail":
      return <AlertCircle className="size-3.5 text-destructive" />;
    case "warn":
      return <AlertTriangle className="size-3.5 text-amber-500" />;
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface DeployDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  yaml: string;
}

export function DeployDialog({ open, onOpenChange, yaml }: DeployDialogProps) {
  const queryClient = useQueryClient();

  // State
  const [selectedTarget, setSelectedTarget] = useState("local");
  const [deployJobId, setDeployJobId] = useState<string | null>(null);
  const [deployStatus, setDeployStatus] = useState<DeployJobStatus | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [logsExpanded, setLogsExpanded] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Validation
  const validationItems = useMemo(() => runPreDeployValidation(yaml), [yaml]);
  const hasErrors = validationItems.some((i) => i.status === "fail");
  const agentName = parseYamlField(yaml, "name") || "untitled";
  const agentVersion = parseYamlField(yaml, "version") || "0.1.0";

  const isActive = deployStatus ? ACTIVE_STATUSES.has(deployStatus) : false;
  const isComplete = deployStatus === "completed";
  const isFailed = deployStatus === "failed";

  // Poll for deploy status
  const startPolling = useCallback(
    (jobId: string) => {
      if (pollRef.current) clearInterval(pollRef.current);

      pollRef.current = setInterval(async () => {
        try {
          const res = await api.deploys.getDetail(jobId);
          const job = res.data;
          setDeployStatus(job.status as DeployJobStatus);
          setErrorMessage(job.error_message ?? null);
          setLogs(job.logs ?? []);

          // Stop polling when done
          if (job.status === "completed" || job.status === "failed") {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            // Invalidate deploys query so the deploys page updates
            queryClient.invalidateQueries({ queryKey: ["deploys"] });
            queryClient.invalidateQueries({ queryKey: ["agents"] });
          }
        } catch {
          // Silently ignore poll errors
        }
      }, 800);
    },
    [queryClient]
  );

  // Start deploy
  const handleDeploy = useCallback(async () => {
    setIsSubmitting(true);
    setDeployStatus("pending");
    setErrorMessage(null);
    setLogs([]);

    try {
      const res = await api.deploys.create({
        config_yaml: yaml,
        target: selectedTarget,
      });
      const job = res.data;
      setDeployJobId(job.id);
      setDeployStatus(job.status);
      startPolling(job.id);
    } catch (err) {
      setDeployStatus("failed");
      setErrorMessage(err instanceof Error ? err.message : "Failed to start deployment");
    } finally {
      setIsSubmitting(false);
    }
  }, [yaml, selectedTarget, startPolling]);

  // Cancel deploy
  const handleCancel = useCallback(async () => {
    if (!deployJobId) return;
    try {
      await api.deploys.cancel(deployJobId);
      setDeployStatus("failed");
      setErrorMessage("Cancelled by user");
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    } catch {
      // ignore
    }
  }, [deployJobId]);

  // Rollback
  const handleRollback = useCallback(async () => {
    if (!deployJobId) return;
    try {
      await api.deploys.rollback(deployJobId);
      setLogs((prev) => [
        ...prev,
        {
          timestamp: new Date().toISOString(),
          level: "info",
          message: "Rollback initiated - agent status reset to stopped",
          step: null,
        },
      ]);
    } catch {
      // ignore
    }
  }, [deployJobId]);

  // Reset state on close
  useEffect(() => {
    if (!open) {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      // Small delay to avoid flash during close animation
      const t = setTimeout(() => {
        setDeployJobId(null);
        setDeployStatus(null);
        setErrorMessage(null);
        setLogs([]);
        setIsSubmitting(false);
      }, 300);
      return () => clearTimeout(t);
    }
  }, [open]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const showPipeline = deployStatus !== null;
  const showTargetSelector = !showPipeline;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-xl flex-col overflow-hidden p-0">
        {/* Header */}
        <DialogHeader className="px-5 pt-5 pb-0">
          <DialogTitle className="flex items-center gap-2">
            <Rocket className="size-4" />
            Deploy Agent
          </DialogTitle>
          <DialogDescription>
            Deploy{" "}
            <span className="font-medium text-foreground">{agentName}</span>{" "}
            v{agentVersion}
          </DialogDescription>
        </DialogHeader>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Target Selector */}
          {showTargetSelector && (
            <div>
              <h4 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                Deploy Target
              </h4>
              <div className="grid grid-cols-2 gap-2">
                {DEPLOY_TARGETS.map((target) => {
                  const Icon = target.icon;
                  const selected = selectedTarget === target.value;
                  return (
                    <button
                      key={target.value}
                      onClick={() => setSelectedTarget(target.value)}
                      disabled={!target.available}
                      className={cn(
                        "relative flex flex-col items-start gap-1 rounded-lg border p-3 text-left transition-all",
                        selected
                          ? "border-foreground/30 bg-foreground/5 ring-1 ring-foreground/10"
                          : "border-border hover:border-border/80 hover:bg-muted/30",
                        !target.available && "cursor-not-allowed opacity-50"
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <Icon className="size-4 text-muted-foreground" />
                        <span className="text-xs font-medium">{target.label}</span>
                      </div>
                      <p className="text-[10px] text-muted-foreground">
                        {target.description}
                      </p>
                      {selected && (
                        <div className="absolute right-2 top-2">
                          <CheckCircle2 className="size-3.5 text-foreground/50" />
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Pre-deploy checklist */}
          {showTargetSelector && (
            <div>
              <h4 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                Pre-deploy Validation
              </h4>
              <div className="space-y-1.5 rounded-lg border border-border bg-muted/10 p-3">
                {validationItems.map((item) => (
                  <div key={item.key} className="flex items-center gap-2 text-[11px]">
                    <ValidationStatusIcon status={item.status} />
                    <span className="font-medium">{item.label}</span>
                    <span
                      className={cn(
                        "truncate",
                        item.status === "fail"
                          ? "text-destructive"
                          : "text-muted-foreground"
                      )}
                    >
                      {item.message}
                    </span>
                  </div>
                ))}
              </div>
              {hasErrors && (
                <p className="mt-1.5 text-[10px] text-destructive">
                  Fix validation errors before deploying.
                </p>
              )}
            </div>
          )}

          {/* Pipeline Progress */}
          {showPipeline && (
            <div>
              <div className="mb-2 flex items-center justify-between">
                <h4 className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Deploy Pipeline
                </h4>
                <Badge
                  variant="outline"
                  className={cn(
                    "text-[9px]",
                    isActive && "border-amber-500/30 text-amber-600 dark:text-amber-400",
                    isComplete && "border-emerald-500/30 text-emerald-600 dark:text-emerald-400",
                    isFailed && "border-destructive/30 text-destructive"
                  )}
                >
                  {selectedTarget === "local" ? "Local Docker" : "Cloud Run"}
                </Badge>
              </div>

              <DeployProgress
                status={deployStatus!}
                errorMessage={errorMessage}
              />
            </div>
          )}
        </div>

        {/* Log panel (only when deploying) */}
        {showPipeline && (
          <DeployLogs
            logs={logs}
            expanded={logsExpanded}
            onToggle={() => setLogsExpanded(!logsExpanded)}
          />
        )}

        {/* Footer */}
        <DialogFooter className="border-t border-border px-5 py-3">
          {/* Pre-deploy state */}
          {!showPipeline && (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleDeploy}
                disabled={hasErrors || isSubmitting}
                className="gap-1.5"
              >
                <Rocket className="size-3.5" />
                {isSubmitting ? "Starting..." : "Deploy"}
              </Button>
            </>
          )}

          {/* Active deploy */}
          {showPipeline && isActive && (
            <>
              <Button
                variant="outline"
                onClick={handleCancel}
                className="gap-1.5 text-destructive hover:text-destructive"
              >
                <Square className="size-3" />
                Cancel Deploy
              </Button>
            </>
          )}

          {/* Completed */}
          {isComplete && (
            <Button onClick={() => onOpenChange(false)} className="gap-1.5">
              <CheckCircle2 className="size-3.5" />
              Done
            </Button>
          )}

          {/* Failed */}
          {isFailed && (
            <>
              <Button
                variant="outline"
                onClick={handleRollback}
                className="gap-1.5"
              >
                <RotateCcw className="size-3.5" />
                Rollback
              </Button>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Close
              </Button>
              <Button onClick={handleDeploy} className="gap-1.5">
                <Rocket className="size-3.5" />
                Retry
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
