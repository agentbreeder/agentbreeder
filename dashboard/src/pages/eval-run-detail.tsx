import { useQuery } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import {
  ArrowLeft,
  Target,
  CheckCircle2,
  XCircle,
  Clock,
  DollarSign,
  ChevronDown,
  ChevronUp,
  GitCompareArrows,
} from "lucide-react";
import {
  api,
  type EvalRun,
  type EvalRunResult,
  type EvalRunStatus,
  type EvalScoreTrend,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useState, useMemo } from "react";
import { SkeletonTableRows } from "@/components/ui/skeleton-table";
import { EmptyState } from "@/components/ui/empty-state";

const STATUS_VARIANTS: Record<EvalRunStatus, { label: string; className: string }> = {
  pending: {
    label: "Pending",
    className: "bg-muted text-muted-foreground border-border",
  },
  running: {
    label: "Running",
    className: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20 animate-pulse",
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

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

function ScoreCard({
  title,
  score,
  p95,
  color,
}: {
  title: string;
  score: number | undefined | null;
  p95?: number | null;
  color: string;
}) {
  const displayScore = score != null ? `${Math.round(score * 100)}%` : "--";
  const displayP95 = p95 != null ? `p95: ${Math.round(p95 * 100)}%` : undefined;

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="text-xs text-muted-foreground">{title}</div>
      <div className={cn("mt-1 text-2xl font-semibold tracking-tight", color)}>
        {displayScore}
      </div>
      {displayP95 && (
        <div className="mt-0.5 text-[10px] text-muted-foreground">{displayP95}</div>
      )}
      {score != null && (
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
          <div
            className={cn("h-full rounded-full transition-all", getBarColor(score))}
            style={{ width: `${Math.round(score * 100)}%` }}
          />
        </div>
      )}
    </div>
  );
}

function getBarColor(score: number): string {
  if (score >= 0.8) return "bg-emerald-500";
  if (score >= 0.6) return "bg-amber-500";
  return "bg-red-500";
}

function getTextColor(score: number): string {
  if (score >= 0.8) return "text-emerald-600 dark:text-emerald-400";
  if (score >= 0.6) return "text-amber-600 dark:text-amber-400";
  return "text-red-600 dark:text-red-400";
}

function ScoreTrendChart({ trends }: { trends: EvalScoreTrend[] }) {
  if (trends.length < 2) {
    return (
      <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">
        Need at least 2 runs to show trend
      </div>
    );
  }

  const maxScore = Math.max(...trends.map((t) => t.overall_score), 0.01);

  return (
    <div className="flex h-32 items-end gap-1">
      {trends.map((t, i) => {
        const heightPct = Math.max((t.overall_score / maxScore) * 100, 2);
        const pct = Math.round(t.overall_score * 100);
        return (
          <div
            key={t.run_id}
            className="group relative flex-1"
            title={`Run ${i + 1}: ${pct}%`}
          >
            <div
              className={cn(
                "w-full rounded-t transition-colors",
                getBarColor(t.overall_score),
                "opacity-70 group-hover:opacity-100"
              )}
              style={{ height: `${heightPct}%` }}
            />
            <div className="pointer-events-none absolute -top-10 left-1/2 z-10 hidden -translate-x-1/2 rounded border border-border bg-card px-2 py-1 text-xs shadow-lg group-hover:block">
              <div className="font-medium">{pct}%</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ResultRow({ result }: { result: EvalRunResult }) {
  const [expanded, setExpanded] = useState(false);
  const correctness = result.scores?.correctness;
  const relevance = result.scores?.relevance;

  return (
    <div className="border-b border-border/50 last:border-0">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-4 px-6 py-3 text-left transition-colors hover:bg-muted/30"
      >
        <div className="min-w-0 flex-1">
          <pre className="truncate font-mono text-xs text-foreground">
            {JSON.stringify(result.input).length > 60
              ? JSON.stringify(result.input).slice(0, 60) + "..."
              : JSON.stringify(result.input)}
          </pre>
        </div>

        <span className="w-28 truncate text-xs text-muted-foreground">
          {result.expected_output.length > 20
            ? result.expected_output.slice(0, 20) + "..."
            : result.expected_output}
        </span>

        <span className="w-28 truncate text-xs text-muted-foreground">
          {result.actual_output.length > 20
            ? result.actual_output.slice(0, 20) + "..."
            : result.actual_output}
        </span>

        <span className="w-14 text-center">
          {correctness != null ? (
            <span className={cn("font-mono text-xs font-medium", getTextColor(correctness))}>
              {Math.round(correctness * 100)}%
            </span>
          ) : (
            <span className="text-[10px] text-muted-foreground">--</span>
          )}
        </span>

        <span className="w-14 text-center">
          {relevance != null ? (
            <span className={cn("font-mono text-xs font-medium", getTextColor(relevance))}>
              {Math.round(relevance * 100)}%
            </span>
          ) : (
            <span className="text-[10px] text-muted-foreground">--</span>
          )}
        </span>

        <span className="w-16 text-right font-mono text-[10px] text-muted-foreground">
          {formatDuration(result.latency_ms)}
        </span>

        <span className="w-14 text-center">
          {result.status === "passed" ? (
            <CheckCircle2 className="mx-auto size-3.5 text-emerald-500" />
          ) : (
            <XCircle className="mx-auto size-3.5 text-red-500" />
          )}
        </span>

        {expanded ? (
          <ChevronUp className="size-3.5 text-muted-foreground" />
        ) : (
          <ChevronDown className="size-3.5 text-muted-foreground" />
        )}
      </button>

      {expanded && (
        <div className="space-y-3 border-t border-border/30 bg-muted/10 px-6 py-4">
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Input
            </div>
            <pre className="rounded-md border border-border bg-card p-3 font-mono text-xs">
              {JSON.stringify(result.input, null, 2)}
            </pre>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                Expected Output
              </div>
              <div className="rounded-md border border-border bg-card p-3 text-xs">
                {result.expected_output}
              </div>
            </div>
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                Actual Output
              </div>
              <div className="rounded-md border border-border bg-card p-3 text-xs">
                {result.actual_output}
              </div>
            </div>
          </div>
          {result.error && (
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-destructive">
                Error
              </div>
              <div className="rounded-md border border-destructive/20 bg-destructive/5 p-3 text-xs text-destructive">
                {result.error}
              </div>
            </div>
          )}
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Scores
            </div>
            <div className="flex gap-3">
              {Object.entries(result.scores).map(([key, val]) => (
                <div key={key} className="rounded-md border border-border bg-card px-3 py-2">
                  <div className="text-[10px] text-muted-foreground">{key}</div>
                  <div className={cn("font-mono text-sm font-medium", getTextColor(val as number))}>
                    {Math.round((val as number) * 100)}%
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function EvalRunDetailPage() {
  const { id } = useParams<{ id: string }>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["eval-run", id],
    queryFn: () => api.evals.runs.get(id!),
    enabled: !!id,
    refetchInterval: (query) => {
      const run = query.state.data?.data;
      if (run && (run.status === "pending" || run.status === "running")) {
        return 3_000;
      }
      return false;
    },
  });

  const run: (EvalRun & { results: EvalRunResult[] }) | null = data?.data ?? null;

  // Fetch score trend for the agent
  const { data: trendData } = useQuery({
    queryKey: ["eval-score-trend", run?.agent_name],
    queryFn: () => api.evals.scores.trend(run!.agent_name, { limit: 20 }),
    enabled: !!run?.agent_name,
    staleTime: 30_000,
  });
  const trends: EvalScoreTrend[] = trendData?.data ?? [];

  const summary = run?.summary;
  const results = run?.results ?? [];

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <div className="animate-pulse space-y-4">
          <div className="h-6 w-48 rounded bg-muted" />
          <div className="grid grid-cols-5 gap-4">
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="h-24 rounded-lg border border-border bg-card" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (error || !run) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <div className="text-sm text-destructive">
          {error ? `Failed to load run: ${(error as Error).message}` : "Run not found"}
        </div>
      </div>
    );
  }

  const statusVariant = STATUS_VARIANTS[run.status] ?? STATUS_VARIANTS.pending;

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
      <div className="mb-6 flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-semibold tracking-tight">{run.agent_name}</h1>
            <Badge variant="outline" className={cn("text-[10px]", statusVariant.className)}>
              {statusVariant.label}
            </Badge>
          </div>
          <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono">Run: {run.id.slice(0, 12)}...</span>
            <span className="text-border">|</span>
            <span>Dataset: {run.dataset_id.slice(0, 12)}...</span>
          </div>
        </div>
        {trends.length >= 2 && (
          <Link to={`/evals/compare?runA=${trends[trends.length - 2]?.run_id}&runB=${run.id}`}>
            <Button size="sm" variant="outline" className="h-7 gap-1 text-xs">
              <GitCompareArrows className="size-3" />
              Compare with previous
            </Button>
          </Link>
        )}
      </div>

      {/* Summary Cards */}
      <div className="mb-6 grid grid-cols-5 gap-4">
        <ScoreCard
          title="Overall Score"
          score={summary?.overall_score}
          color={summary?.overall_score != null ? getTextColor(summary.overall_score) : "text-muted-foreground"}
        />
        <ScoreCard
          title="Correctness"
          score={summary?.metrics?.correctness?.mean}
          p95={summary?.metrics?.correctness?.p95}
          color={summary?.metrics?.correctness?.mean != null ? getTextColor(summary.metrics.correctness.mean) : "text-muted-foreground"}
        />
        <ScoreCard
          title="Relevance"
          score={summary?.metrics?.relevance?.mean}
          p95={summary?.metrics?.relevance?.p95}
          color={summary?.metrics?.relevance?.mean != null ? getTextColor(summary.metrics.relevance.mean) : "text-muted-foreground"}
        />
        <ScoreCard
          title="Latency Score"
          score={summary?.metrics?.latency?.mean}
          p95={summary?.metrics?.latency?.p95}
          color={summary?.metrics?.latency?.mean != null ? getTextColor(summary.metrics.latency.mean) : "text-muted-foreground"}
        />
        <ScoreCard
          title="Cost Score"
          score={summary?.metrics?.cost?.mean}
          p95={summary?.metrics?.cost?.p95}
          color={summary?.metrics?.cost?.mean != null ? getTextColor(summary.metrics.cost.mean) : "text-muted-foreground"}
        />
      </div>

      {/* Score Trend */}
      {trends.length > 0 && (
        <div className="mb-6 rounded-lg border border-border bg-card p-5">
          <h2 className="mb-3 text-sm font-medium">Score Trend</h2>
          <ScoreTrendChart trends={trends} />
          <div className="mt-2 flex items-center justify-between text-[10px] text-muted-foreground">
            <span>Oldest</span>
            <span>Most recent</span>
          </div>
        </div>
      )}

      {/* Results Table */}
      <div className="rounded-lg border border-border">
        <div className="border-b border-border bg-muted/30 px-6 py-3">
          <h2 className="text-sm font-medium">
            Results
            {summary && (
              <span className="ml-2 text-xs text-muted-foreground">
                {summary.passed_rows} passed / {summary.failed_rows} failed of {summary.total_rows}
              </span>
            )}
          </h2>
        </div>

        <div className="flex items-center gap-4 border-b border-border bg-muted/30 px-6 py-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          <span className="flex-1">Input</span>
          <span className="w-28">Expected</span>
          <span className="w-28">Actual</span>
          <span className="w-14 text-center">Correct</span>
          <span className="w-14 text-center">Relevance</span>
          <span className="w-16 text-right">Latency</span>
          <span className="w-14 text-center">Status</span>
          <span className="w-3.5" />
        </div>

        {results.length === 0 ? (
          <EmptyState
            icon={Target}
            title={run.status === "running" ? "Running evaluation..." : "No results yet"}
            description={
              run.status === "running"
                ? "Results will appear here as test cases complete."
                : "This run has no results."
            }
          />
        ) : (
          results.map((result) => <ResultRow key={result.id} result={result} />)
        )}
      </div>
    </div>
  );
}
