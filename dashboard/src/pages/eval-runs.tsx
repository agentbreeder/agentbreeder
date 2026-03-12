import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useSearchParams, Link } from "react-router-dom";
import {
  PlayCircle,
  Search,
  Filter,
  Plus,
  ChevronRight,
} from "lucide-react";
import {
  api,
  type EvalRun,
  type EvalRunStatus,
  type EvalDataset,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { useSortable } from "@/hooks/use-sortable";
import { SortableColumnHeader } from "@/components/ui/sortable-header";
import { SkeletonTableRows } from "@/components/ui/skeleton-table";
import { EmptyState } from "@/components/ui/empty-state";
import { useToast } from "@/hooks/use-toast";

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

function timeSince(dateStr: string): string {
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (seconds < 60) return "now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function ScoreBadge({ score }: { score: number | undefined | null }) {
  if (score == null) return <span className="text-[10px] text-muted-foreground">--</span>;
  const pct = Math.round(score * 100);
  const color =
    pct >= 80
      ? "text-emerald-600 dark:text-emerald-400"
      : pct >= 60
        ? "text-amber-600 dark:text-amber-400"
        : "text-red-600 dark:text-red-400";
  return <span className={cn("font-mono text-xs font-medium", color)}>{pct}%</span>;
}

function RunRow({ run }: { run: EvalRun }) {
  const variant = STATUS_VARIANTS[run.status] ?? STATUS_VARIANTS.pending;

  return (
    <Link
      to={`/evals/runs/${run.id}`}
      className="group flex items-center gap-4 border-b border-border/50 px-6 py-3.5 transition-colors last:border-0 hover:bg-muted/30"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium group-hover:text-primary">
            {run.agent_name}
          </span>
          <Badge variant="outline" className={cn("text-[10px]", variant.className)}>
            {variant.label}
          </Badge>
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono text-[10px]">
            {run.id.length > 12 ? run.id.slice(0, 8) + "..." : run.id}
          </span>
          <span className="text-border">|</span>
          <span>dataset: {run.dataset_id.slice(0, 8)}...</span>
        </div>
      </div>

      <div className="w-16 text-center">
        <ScoreBadge score={run.summary?.overall_score} />
      </div>

      <span className="w-20 text-right font-mono text-[10px] text-muted-foreground">
        {timeSince(run.started_at)}
      </span>

      {run.completed_at && (
        <span className="w-20 text-right font-mono text-[10px] text-muted-foreground">
          {timeSince(run.completed_at)}
        </span>
      )}

      <ChevronRight className="size-3.5 text-muted-foreground/50" />
    </Link>
  );
}

function NewRunDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [agentName, setAgentName] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const queryClient = useQueryClient();
  const { toast } = useToast();

  // Fetch datasets for dropdown
  const { data: datasetsData } = useQuery({
    queryKey: ["eval-datasets-for-selector"],
    queryFn: () => api.evals.datasets.list({ per_page: 100 }),
    staleTime: 30_000,
  });
  const datasets: EvalDataset[] = datasetsData?.data ?? [];

  const createMutation = useMutation({
    mutationFn: () =>
      api.evals.runs.create({
        agent_name: agentName,
        dataset_id: datasetId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["eval-runs"] });
      toast({ title: "Eval run started", variant: "success" });
      onOpenChange(false);
      setAgentName("");
      setDatasetId("");
    },
    onError: (err: Error) => {
      toast({ title: "Failed to start run", description: err.message, variant: "error" });
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New Eval Run</DialogTitle>
          <DialogDescription>
            Run an evaluation against an agent using a dataset.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Agent Name</label>
            <Input
              placeholder="e.g. customer-support-agent"
              value={agentName}
              onChange={(e) => setAgentName(e.target.value)}
              className="mt-1 h-8 text-xs"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Dataset</label>
            <select
              value={datasetId}
              onChange={(e) => setDatasetId(e.target.value)}
              className="mt-1 h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
            >
              <option value="">Select a dataset...</option>
              {datasets.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.row_count} rows)
                </option>
              ))}
            </select>
          </div>
        </div>
        <DialogFooter>
          <Button
            size="sm"
            disabled={!agentName.trim() || !datasetId || createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            {createMutation.isPending ? "Starting..." : "Start Run"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function EvalRunsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState(searchParams.get("q") ?? "");
  const [statusFilter, setStatusFilter] = useState(searchParams.get("status") ?? "");
  const [agentFilter, setAgentFilter] = useState(searchParams.get("agent_name") ?? "");
  const [newRunOpen, setNewRunOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["eval-runs", { statusFilter, agentFilter }],
    queryFn: () =>
      api.evals.runs.list({
        status: (statusFilter as EvalRunStatus) || undefined,
        agent_name: agentFilter || undefined,
      }),
    staleTime: 5_000,
  });

  const runs = data?.data ?? [];
  const total = data?.meta?.total ?? runs.length;
  const hasFilter = !!(search || statusFilter || agentFilter);

  // Client-side search
  const filtered = search
    ? runs.filter((r) =>
        r.agent_name.toLowerCase().includes(search.toLowerCase()) ||
        r.id.toLowerCase().includes(search.toLowerCase())
      )
    : runs;

  const { sortedData, sortKey, sortDirection, toggleSort } = useSortable(
    filtered as unknown as Record<string, unknown>[],
    "started_at",
    "desc"
  );
  const sortedRuns = sortedData as unknown as EvalRun[];

  // Unique agent names for filter
  const agentNames = [...new Set(runs.map((r) => r.agent_name))].sort();

  const handleSearch = (value: string) => {
    setSearch(value);
    const sp = new URLSearchParams(searchParams);
    if (value) sp.set("q", value);
    else sp.delete("q");
    setSearchParams(sp, { replace: true });
  };

  return (
    <div className="mx-auto max-w-5xl p-6">
      {/* Header */}
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Evaluation Runs</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {total} run{total !== 1 ? "s" : ""}
          </p>
        </div>
        <Button size="sm" className="h-8 gap-1.5 text-xs" onClick={() => setNewRunOpen(true)}>
          <Plus className="size-3.5" />
          New Run
        </Button>
      </div>

      <NewRunDialog open={newRunOpen} onOpenChange={setNewRunOpen} />

      {/* Filters */}
      <div className="mb-4 flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search runs..."
            value={search}
            onChange={(e) => handleSearch(e.target.value)}
            className="h-8 pl-9 text-xs"
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter className="size-3.5 text-muted-foreground" />
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none"
          >
            <option value="">All agents</option>
            {agentNames.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none"
          >
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-lg border border-border">
        <div className="flex items-center gap-4 border-b border-border bg-muted/30 px-6 py-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          <span className="flex-1">
            <SortableColumnHeader
              sortKey="agent_name"
              currentSortKey={sortKey}
              currentDirection={sortDirection}
              onSort={toggleSort}
            >
              Run
            </SortableColumnHeader>
          </span>
          <span className="w-16 text-center">Score</span>
          <span className="w-20 text-right">
            <SortableColumnHeader
              sortKey="started_at"
              currentSortKey={sortKey}
              currentDirection={sortDirection}
              onSort={toggleSort}
            >
              Started
            </SortableColumnHeader>
          </span>
          <span className="w-20 text-right">Completed</span>
          <span className="w-3.5" />
        </div>

        {isLoading ? (
          <SkeletonTableRows rows={6} columns={4} />
        ) : error ? (
          <div className="px-6 py-12 text-center text-sm text-destructive">
            Failed to load runs: {(error as Error).message}
          </div>
        ) : sortedRuns.length === 0 ? (
          <EmptyState
            icon={PlayCircle}
            title={hasFilter ? "No runs match your filters" : "No evaluation runs yet"}
            description={
              hasFilter
                ? "Try adjusting your search or filters."
                : "Create a dataset and start an eval run to measure agent performance."
            }
          />
        ) : (
          sortedRuns.map((run) => <RunRow key={run.id} run={run} />)
        )}
      </div>
    </div>
  );
}
