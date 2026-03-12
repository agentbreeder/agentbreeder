/**
 * Approvals Page — pending reviews queue for PRs, grouped/filterable by
 * resource type and status.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  GitPullRequest,
  Bot,
  Wrench,
  FileText,
  Database,
  Brain,
  Clock,
  CheckCircle2,
  XCircle,
  AlertCircle,
  MessageSquare,
  Filter,
  Loader2,
  Server,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { api, type PRStatus, type GitPR } from "@/lib/api";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const RESOURCE_TYPE_OPTIONS = [
  { value: "", label: "All Types" },
  { value: "agent", label: "Agent" },
  { value: "prompt", label: "Prompt" },
  { value: "tool", label: "Tool" },
  { value: "mcp", label: "MCP Server" },
  { value: "rag", label: "RAG" },
  { value: "memory", label: "Memory" },
] as const;

const STATUS_OPTIONS: { value: PRStatus | ""; label: string }[] = [
  { value: "", label: "All Statuses" },
  { value: "submitted", label: "Submitted" },
  { value: "in_review", label: "In Review" },
  { value: "approved", label: "Approved" },
  { value: "changes_requested", label: "Changes Requested" },
  { value: "rejected", label: "Rejected" },
  { value: "published", label: "Published" },
];

const RESOURCE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  agent: Bot,
  prompt: FileText,
  tool: Wrench,
  mcp: Server,
  rag: Database,
  memory: Brain,
};

const STATUS_CONFIG: Record<
  PRStatus,
  { label: string; className: string; icon: React.ComponentType<{ className?: string }> }
> = {
  draft: {
    label: "Draft",
    className: "bg-muted text-muted-foreground border-border",
    icon: Clock,
  },
  submitted: {
    label: "Submitted",
    className: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
    icon: GitPullRequest,
  },
  in_review: {
    label: "In Review",
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
    icon: AlertCircle,
  },
  approved: {
    label: "Approved",
    className: "bg-green-500/10 text-green-600 dark:text-green-400 border-green-500/20",
    icon: CheckCircle2,
  },
  changes_requested: {
    label: "Changes Requested",
    className: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
    icon: AlertCircle,
  },
  rejected: {
    label: "Rejected",
    className: "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20",
    icon: XCircle,
  },
  published: {
    label: "Published",
    className: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
    icon: CheckCircle2,
  },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMins = Math.floor(diffMs / 60_000);
  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHrs = Math.floor(diffMins / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  const diffDays = Math.floor(diffHrs / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return d.toLocaleDateString();
}

// ---------------------------------------------------------------------------
// PR Card
// ---------------------------------------------------------------------------

function PRCard({ pr }: { pr: GitPR }) {
  const statusCfg = STATUS_CONFIG[pr.status];
  const StatusIcon = statusCfg.icon;
  const ResourceIcon = RESOURCE_ICONS[pr.resource_type] ?? GitPullRequest;

  return (
    <Link
      to={`/approvals/${pr.id}`}
      className="group block rounded-lg border border-border bg-card p-4 transition-all hover:border-foreground/20 hover:shadow-sm"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-muted">
            <ResourceIcon className="size-4 text-muted-foreground" />
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-medium group-hover:text-foreground">
              {pr.title}
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {pr.resource_type && (
                <span className="capitalize">{pr.resource_type}</span>
              )}
              {pr.resource_name && (
                <span className="text-muted-foreground/60">
                  {" / "}
                  {pr.resource_name}
                </span>
              )}
            </p>
          </div>
        </div>

        <Badge
          variant="outline"
          className={cn("shrink-0 gap-1 border text-[11px]", statusCfg.className)}
        >
          <StatusIcon className="size-3" />
          {statusCfg.label}
        </Badge>
      </div>

      <div className="mt-3 flex items-center gap-4 text-xs text-muted-foreground">
        <span>by {pr.submitter}</span>
        <span>{formatDate(pr.created_at)}</span>
        {pr.comments.length > 0 && (
          <span className="flex items-center gap-1">
            <MessageSquare className="size-3" />
            {pr.comments.length}
          </span>
        )}
        {pr.reviewer && (
          <span className="text-muted-foreground/70">
            Reviewer: {pr.reviewer}
          </span>
        )}
      </div>

      {pr.description && (
        <p className="mt-2 line-clamp-2 text-xs text-muted-foreground/80">
          {pr.description}
        </p>
      )}
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function ApprovalsPage() {
  const [statusFilter, setStatusFilter] = useState<PRStatus | "">("");
  const [resourceTypeFilter, setResourceTypeFilter] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["prs", statusFilter, resourceTypeFilter],
    queryFn: () =>
      api.git.prs.list({
        status: statusFilter || undefined,
        resource_type: resourceTypeFilter || undefined,
      }),
  });

  const prs = data?.data?.prs ?? [];

  // Count pending reviews (submitted + in_review)
  const pendingCount = prs.filter(
    (p) => p.status === "submitted" || p.status === "in_review"
  ).length;

  return (
    <div className="mx-auto max-w-5xl p-6">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-lg bg-foreground">
            <GitPullRequest className="size-5 text-background" />
          </div>
          <div>
            <h1 className="text-xl font-semibold">Approvals</h1>
            <p className="text-sm text-muted-foreground">
              Review and approve changes to agents, prompts, tools, and other resources.
              {pendingCount > 0 && (
                <span className="ml-1 font-medium text-amber-600 dark:text-amber-400">
                  {pendingCount} pending review{pendingCount !== 1 ? "s" : ""}
                </span>
              )}
            </p>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="mb-4 flex items-center gap-2">
        <Filter className="size-4 text-muted-foreground" />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as PRStatus | "")}
          className="h-7 rounded-md border border-border bg-background px-2 text-xs outline-none focus:border-ring"
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <select
          value={resourceTypeFilter}
          onChange={(e) => setResourceTypeFilter(e.target.value)}
          className="h-7 rounded-md border border-border bg-background px-2 text-xs outline-none focus:border-ring"
        >
          {RESOURCE_TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {/* List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      ) : prs.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-20">
          <GitPullRequest className="size-10 text-muted-foreground/40" />
          <p className="mt-3 text-sm text-muted-foreground">No pull requests found</p>
          <p className="text-xs text-muted-foreground/70">
            {statusFilter || resourceTypeFilter
              ? "Try adjusting your filters"
              : "Submit a resource for review from any builder page"}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {prs.map((pr) => (
            <PRCard key={pr.id} pr={pr} />
          ))}
        </div>
      )}
    </div>
  );
}
