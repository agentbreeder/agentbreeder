import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Activity,
  Circle,
  Clock,
  Target,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { api, type DeployJob, type DeployJobStatus } from "@/lib/api";
import { DeployPipeline } from "@/components/deploy-pipeline";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { useSortable } from "@/hooks/use-sortable";
import { SortableColumnHeader } from "@/components/ui/sortable-header";
import { SkeletonTableRows } from "@/components/ui/skeleton-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ExportDropdown } from "@/components/export-dropdown";

const STATUS_COLORS: Record<string, string> = {
  completed: "text-emerald-500",
  failed: "text-destructive",
  pending: "text-muted-foreground",
};

const ACTIVE_STATUSES = new Set<DeployJobStatus>([
  "pending",
  "parsing",
  "building",
  "provisioning",
  "deploying",
  "health_checking",
  "registering",
]);

const TARGET_COLORS: Record<string, string> = {
  local: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20",
  aws: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  gcp: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  kubernetes: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
};

function DeployRow({ job }: { job: DeployJob }) {
  const [expanded, setExpanded] = useState(false);
  const isActive = ACTIVE_STATUSES.has(job.status);
  const statusColor =
    STATUS_COLORS[job.status] ?? (isActive ? "text-amber-500 animate-pulse" : "text-muted-foreground");

  const duration = job.completed_at
    ? formatDuration(new Date(job.started_at), new Date(job.completed_at))
    : isActive
      ? "in progress..."
      : "--";

  return (
    <div className="border-b border-border/50 last:border-0">
      <div
        onClick={() => setExpanded(!expanded)}
        className="flex cursor-pointer items-center gap-4 px-5 py-3 transition-colors hover:bg-muted/20"
      >
        <Circle className={cn("size-2 shrink-0 fill-current", statusColor)} />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {job.agent_name ? (
              <Link
                to={`/agents/${job.agent_id}`}
                onClick={(e) => e.stopPropagation()}
                className="text-sm font-medium hover:underline"
              >
                {job.agent_name}
              </Link>
            ) : (
              <span className="font-mono text-xs text-muted-foreground">
                {job.agent_id.slice(0, 8)}
              </span>
            )}
            <Badge
              variant="outline"
              className={cn(
                "text-[10px]",
                TARGET_COLORS[job.target] ?? "bg-muted text-muted-foreground border-border"
              )}
            >
              {job.target}
            </Badge>
          </div>
        </div>

        <span className="text-xs text-muted-foreground">{job.status}</span>

        <span className="flex w-20 items-center justify-end gap-1 text-right font-mono text-[10px] text-muted-foreground">
          <Clock className="size-2.5" />
          {duration}
        </span>

        <span className="w-16 text-right text-[10px] text-muted-foreground">
          {timeSince(job.started_at)}
        </span>

        {expanded ? (
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
        )}
      </div>

      {expanded && (
        <div className="border-t border-border/30 bg-muted/10 px-5 py-4">
          <DeployPipeline
            status={job.status}
            errorMessage={job.error_message}
          />
          <div className="mt-3 flex items-center gap-4 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <Target className="size-2.5" />
              Target: {job.target}
            </span>
            <span>
              Started:{" "}
              {new Date(job.started_at).toLocaleString("en-US", {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })}
            </span>
            {job.completed_at && (
              <span>
                Finished:{" "}
                {new Date(job.completed_at).toLocaleString("en-US", {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function DeploysPage() {
  const [statusFilter, setStatusFilter] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["deploys", { statusFilter }],
    queryFn: () =>
      api.deploys.list({
        status: (statusFilter as DeployJobStatus) || undefined,
      }),
    staleTime: 5_000,
    refetchInterval: 10_000,
  });

  const jobs = data?.data ?? [];
  const total = data?.meta.total ?? 0;

  // Sortable
  const { sortedData, sortKey, sortDirection, toggleSort } = useSortable(
    jobs as unknown as Record<string, unknown>[],
    "started_at",
    "desc"
  );
  const sortedJobs = sortedData as unknown as DeployJob[];

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Deploys</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {total} deployment{total !== 1 ? "s" : ""}
          </p>
        </div>
        <ExportDropdown
          data={jobs as unknown as Record<string, unknown>[]}
          filename="deploys"
        />
      </div>

      {/* Filter */}
      <div className="mb-4 flex items-center gap-3">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none"
        >
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="parsing">Parsing</option>
          <option value="building">Building</option>
          <option value="provisioning">Provisioning</option>
          <option value="deploying">Deploying</option>
          <option value="health_checking">Health Checking</option>
          <option value="registering">Registering</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
        </select>
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-lg border border-border">
        <div className="flex items-center gap-4 border-b border-border bg-muted/30 px-5 py-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          <span className="w-2" />
          <span className="flex-1">
            <SortableColumnHeader
              sortKey="agent_name"
              currentSortKey={sortKey}
              currentDirection={sortDirection}
              onSort={toggleSort}
            >
              Agent
            </SortableColumnHeader>
          </span>
          <span className="w-24 text-center">
            <SortableColumnHeader
              sortKey="status"
              currentSortKey={sortKey}
              currentDirection={sortDirection}
              onSort={toggleSort}
            >
              Status
            </SortableColumnHeader>
          </span>
          <span className="w-20 text-right">Duration</span>
          <span className="w-16 text-right">
            <SortableColumnHeader
              sortKey="started_at"
              currentSortKey={sortKey}
              currentDirection={sortDirection}
              onSort={toggleSort}
            >
              Started
            </SortableColumnHeader>
          </span>
          <span className="w-3.5" />
        </div>

        {isLoading ? (
          <SkeletonTableRows rows={5} columns={3} />
        ) : error ? (
          <div className="px-6 py-12 text-center text-sm text-destructive">
            Failed to load deploys: {(error as Error).message}
          </div>
        ) : sortedJobs.length === 0 ? (
          <EmptyState
            icon={Activity}
            title={statusFilter ? "No deploys match your filter" : "No deploy history"}
            description={
              statusFilter
                ? "Try changing the status filter."
                : "Deploy an agent with `agentbreeder deploy` to see deployment jobs here."
            }
          />
        ) : (
          sortedJobs.map((job) => <DeployRow key={job.id} job={job} />)
        )}
      </div>
    </div>
  );
}

function formatDuration(start: Date, end: Date): string {
  const ms = end.getTime() - start.getTime();
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (minutes < 60) return `${minutes}m ${secs}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function timeSince(dateStr: string): string {
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (seconds < 60) return "now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}
