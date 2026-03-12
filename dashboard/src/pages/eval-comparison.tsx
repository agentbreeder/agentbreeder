import { useQuery } from "@tanstack/react-query";
import { useSearchParams, Link } from "react-router-dom";
import {
  ArrowLeft,
  GitCompareArrows,
  TrendingUp,
  TrendingDown,
  Minus,
} from "lucide-react";
import {
  api,
  type EvalComparison,
  type EvalRunStatus,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const STATUS_VARIANTS: Record<EvalRunStatus, { label: string; className: string }> = {
  pending: {
    label: "Pending",
    className: "bg-muted text-muted-foreground border-border",
  },
  running: {
    label: "Running",
    className: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  },
  completed: {
    label: "Completed",
    className: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  },
  failed: {
    label: "Failed",
    className: "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20",
  },
  cancelled: {
    label: "Cancelled",
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
  },
};

function getScoreColor(score: number): string {
  if (score >= 0.8) return "text-emerald-600 dark:text-emerald-400";
  if (score >= 0.6) return "text-amber-600 dark:text-amber-400";
  return "text-red-600 dark:text-red-400";
}

function getBarColor(score: number): string {
  if (score >= 0.8) return "bg-emerald-500";
  if (score >= 0.6) return "bg-amber-500";
  return "bg-red-500";
}

function DeltaIndicator({ delta }: { delta: number }) {
  const absDelta = Math.abs(Math.round(delta * 100));
  if (absDelta === 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <Minus className="size-3" />
        0%
      </span>
    );
  }
  if (delta > 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600 dark:text-emerald-400">
        <TrendingUp className="size-3" />
        +{absDelta}%
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-red-600 dark:text-red-400">
      <TrendingDown className="size-3" />
      -{absDelta}%
    </span>
  );
}

function RunSummaryPanel({
  label,
  run,
}: {
  label: string;
  run: EvalComparison["run_a"];
}) {
  const statusVariant = STATUS_VARIANTS[run.status] ?? STATUS_VARIANTS.pending;
  const summary = run.summary;

  return (
    <div className="flex-1 rounded-lg border border-border bg-card p-5">
      <div className="mb-4 flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
        <Badge variant="outline" className={cn("text-[10px]", statusVariant.className)}>
          {statusVariant.label}
        </Badge>
      </div>

      <div className="space-y-3">
        <div>
          <div className="text-xs text-muted-foreground">Agent</div>
          <div className="text-sm font-medium">{run.agent_name}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Dataset</div>
          <div className="font-mono text-xs">{run.dataset_id.slice(0, 12)}...</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Overall Score</div>
          <div className={cn("text-2xl font-semibold", summary?.overall_score != null ? getScoreColor(summary.overall_score) : "text-muted-foreground")}>
            {summary?.overall_score != null ? `${Math.round(summary.overall_score * 100)}%` : "--"}
          </div>
          {summary?.overall_score != null && (
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
              <div
                className={cn("h-full rounded-full", getBarColor(summary.overall_score))}
                style={{ width: `${Math.round(summary.overall_score * 100)}%` }}
              />
            </div>
          )}
        </div>

        {/* Per-metric scores */}
        {summary?.metrics && Object.entries(summary.metrics).length > 0 && (
          <div className="space-y-2 pt-2">
            <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Metrics
            </div>
            {Object.entries(summary.metrics).map(([key, val]) => (
              <div key={key} className="flex items-center justify-between">
                <span className="text-xs capitalize text-muted-foreground">{key}</span>
                <span className={cn("font-mono text-xs font-medium", getScoreColor(val.mean))}>
                  {Math.round(val.mean * 100)}%
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function EvalComparisonPage() {
  const [searchParams] = useSearchParams();
  const runAId = searchParams.get("runA") ?? "";
  const runBId = searchParams.get("runB") ?? "";

  const { data, isLoading, error } = useQuery({
    queryKey: ["eval-comparison", runAId, runBId],
    queryFn: () => api.evals.scores.compare(runAId, runBId),
    enabled: !!(runAId && runBId),
  });

  const comparison: EvalComparison | null = data?.data ?? null;

  if (!runAId || !runBId) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <div className="text-sm text-muted-foreground">
          Select two runs to compare. Use the ?runA and ?runB query parameters.
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <div className="animate-pulse space-y-4">
          <div className="h-6 w-48 rounded bg-muted" />
          <div className="grid grid-cols-2 gap-6">
            <div className="h-64 rounded-lg border border-border bg-card" />
            <div className="h-64 rounded-lg border border-border bg-card" />
          </div>
        </div>
      </div>
    );
  }

  if (error || !comparison) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <div className="text-sm text-destructive">
          {error
            ? `Failed to load comparison: ${(error as Error).message}`
            : "Comparison data not found"}
        </div>
      </div>
    );
  }

  // Collect all metric keys from both runs
  const allMetricKeys = new Set<string>();
  if (comparison.run_a.summary?.metrics) {
    for (const key of Object.keys(comparison.run_a.summary.metrics)) allMetricKeys.add(key);
  }
  if (comparison.run_b.summary?.metrics) {
    for (const key of Object.keys(comparison.run_b.summary.metrics)) allMetricKeys.add(key);
  }

  return (
    <div className="mx-auto max-w-5xl p-6">
      {/* Back link */}
      <Link
        to="/evals/runs"
        className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3" />
        Back to runs
      </Link>

      {/* Header */}
      <div className="mb-6 flex items-center gap-3">
        <GitCompareArrows className="size-5 text-muted-foreground" />
        <h1 className="text-lg font-semibold tracking-tight">Run Comparison</h1>
      </div>

      {/* Side by side */}
      <div className="mb-6 grid grid-cols-2 gap-6">
        <RunSummaryPanel label="Run A" run={comparison.run_a} />
        <RunSummaryPanel label="Run B" run={comparison.run_b} />
      </div>

      {/* Deltas Table */}
      <div className="rounded-lg border border-border bg-card p-5">
        <h2 className="mb-4 text-sm font-medium">Score Deltas (B - A)</h2>

        {Object.keys(comparison.deltas).length === 0 && allMetricKeys.size === 0 ? (
          <div className="py-6 text-center text-xs text-muted-foreground">
            No metrics to compare
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="pb-2 font-medium">Metric</th>
                <th className="pb-2 text-right font-medium">Run A</th>
                <th className="pb-2 text-right font-medium">Run B</th>
                <th className="pb-2 text-right font-medium">Delta</th>
              </tr>
            </thead>
            <tbody>
              {/* Overall score */}
              <tr className="border-b border-border/50">
                <td className="py-2.5 font-medium">Overall Score</td>
                <td className="py-2.5 text-right font-mono text-xs">
                  {comparison.run_a.summary?.overall_score != null
                    ? `${Math.round(comparison.run_a.summary.overall_score * 100)}%`
                    : "--"}
                </td>
                <td className="py-2.5 text-right font-mono text-xs">
                  {comparison.run_b.summary?.overall_score != null
                    ? `${Math.round(comparison.run_b.summary.overall_score * 100)}%`
                    : "--"}
                </td>
                <td className="py-2.5 text-right">
                  {comparison.deltas.overall_score != null ? (
                    <DeltaIndicator delta={comparison.deltas.overall_score} />
                  ) : (
                    <span className="text-[10px] text-muted-foreground">--</span>
                  )}
                </td>
              </tr>

              {/* Per-metric rows */}
              {[...allMetricKeys].sort().map((key) => {
                const aVal = comparison.run_a.summary?.metrics?.[key]?.mean;
                const bVal = comparison.run_b.summary?.metrics?.[key]?.mean;
                const delta = comparison.deltas[key];

                return (
                  <tr key={key} className="border-b border-border/50 last:border-0">
                    <td className="py-2.5 capitalize">{key}</td>
                    <td className="py-2.5 text-right font-mono text-xs">
                      {aVal != null ? `${Math.round(aVal * 100)}%` : "--"}
                    </td>
                    <td className="py-2.5 text-right font-mono text-xs">
                      {bVal != null ? `${Math.round(bVal * 100)}%` : "--"}
                    </td>
                    <td className="py-2.5 text-right">
                      {delta != null ? (
                        <DeltaIndicator delta={delta} />
                      ) : (
                        <span className="text-[10px] text-muted-foreground">--</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
