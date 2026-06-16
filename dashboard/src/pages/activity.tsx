import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Clock,
  Plus,
  Pencil,
  Rocket,
  Trash2,
  CheckCircle2,
  XCircle,
  Activity,
  AlertTriangle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { RelativeTime } from "@/components/ui/relative-time";
import { cn } from "@/lib/utils";
import { useUrlState } from "@/hooks/use-url-state";
import { api, type AuditEvent } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// Resource types we render with first-class colour/icon. Anything else from
// the backend audit log is bucketed into "other".
type KnownResourceType = "agent" | "tool" | "prompt" | "model" | "deploy";
type ResourceType = KnownResourceType | "other";

// Verb taxonomy. The backend may emit dotted action names like
// `secret.created`; we extract the verb and fall back to "other" otherwise.
type KnownActionType =
  | "created"
  | "updated"
  | "deployed"
  | "deleted"
  | "succeeded"
  | "failed"
  | "started"
  | "archived";
type ActionType = KnownActionType | "other";

interface ActivityEvent {
  id: string;
  resourceType: ResourceType;
  resourceId: string | null;
  resourceName: string;
  action: ActionType;
  rawAction: string;
  actor: string;
  timestamp: string; // ISO string
  description: string;
  link: string | null;
}

// ---------------------------------------------------------------------------
// Adapter: backend AuditEvent → page ActivityEvent
// ---------------------------------------------------------------------------

const KNOWN_RESOURCE_TYPES: readonly KnownResourceType[] = [
  "agent",
  "tool",
  "prompt",
  "model",
  "deploy",
];
const KNOWN_ACTIONS: readonly KnownActionType[] = [
  "created",
  "updated",
  "deployed",
  "deleted",
  "succeeded",
  "failed",
  "started",
  "archived",
];

function isKnownResourceType(s: string): s is KnownResourceType {
  return (KNOWN_RESOURCE_TYPES as readonly string[]).includes(s);
}

function isKnownAction(s: string): s is KnownActionType {
  return (KNOWN_ACTIONS as readonly string[]).includes(s);
}

function pluralizePath(rt: KnownResourceType): string {
  return rt === "deploy" ? "deploys" : `${rt}s`;
}

function adaptAuditToActivity(audit: AuditEvent): ActivityEvent {
  const rt: ResourceType = isKnownResourceType(audit.resource_type)
    ? audit.resource_type
    : "other";

  // "secret.created" → verb "created"; "agent.deployed" → "deployed"; etc.
  const verb = audit.action.includes(".")
    ? (audit.action.split(".").pop() ?? "")
    : audit.action;
  const action: ActionType = isKnownAction(verb) ? verb : "other";

  const link =
    rt !== "other" && audit.resource_id
      ? `/${pluralizePath(rt)}/${audit.resource_id}`
      : null;

  const description = `${audit.actor} ${audit.action} ${audit.resource_type} ${audit.resource_name}`;

  return {
    id: audit.id,
    resourceType: rt,
    resourceId: audit.resource_id,
    resourceName: audit.resource_name,
    action,
    rawAction: audit.action,
    actor: audit.actor,
    timestamp: audit.created_at,
    description,
    link,
  };
}

// ---------------------------------------------------------------------------
// Visual maps
// ---------------------------------------------------------------------------

const ACTION_ICONS: Record<ActionType, typeof Plus> = {
  created: Plus,
  updated: Pencil,
  deployed: Rocket,
  deleted: Trash2,
  archived: Trash2,
  started: Rocket,
  succeeded: CheckCircle2,
  failed: XCircle,
  other: Activity,
};

const ACTION_COLORS: Record<ActionType, string> = {
  created: "text-emerald-500 bg-emerald-500/10",
  updated: "text-blue-500 bg-blue-500/10",
  deployed: "text-violet-500 bg-violet-500/10",
  started: "text-violet-500 bg-violet-500/10",
  succeeded: "text-emerald-500 bg-emerald-500/10",
  deleted: "text-red-500 bg-red-500/10",
  archived: "text-red-500 bg-red-500/10",
  failed: "text-red-500 bg-red-500/10",
  other: "text-muted-foreground bg-muted",
};

const RESOURCE_BADGE_COLORS: Record<ResourceType, string> = {
  agent: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20",
  tool: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  prompt:
    "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
  model:
    "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  deploy:
    "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  other: "bg-muted text-muted-foreground border-border",
};

function getDayLabel(dateStr: string): string {
  const date = new Date(dateStr);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const eventDay = new Date(date);
  eventDay.setHours(0, 0, 0, 0);

  const diffDays = Math.floor(
    (today.getTime() - eventDay.getTime()) / 86_400_000
  );

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  return date.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------

type ResourceFilterType = "all" | KnownResourceType;
type ActionFilterType = "all" | "created" | "updated" | "deployed" | "deleted";

const RESOURCE_FILTERS: { value: ResourceFilterType; label: string }[] = [
  { value: "all", label: "All" },
  { value: "agent", label: "Agents" },
  { value: "tool", label: "Tools" },
  { value: "prompt", label: "Prompts" },
  { value: "model", label: "Models" },
  { value: "deploy", label: "Deploys" },
];

const ACTION_FILTERS: { value: ActionFilterType; label: string }[] = [
  { value: "all", label: "All actions" },
  { value: "created", label: "Created" },
  { value: "updated", label: "Updated" },
  { value: "deployed", label: "Deployed" },
  { value: "deleted", label: "Deleted" },
];

const ACTION_FILTER_MAP: Record<ActionFilterType, KnownActionType[]> = {
  all: [],
  created: ["created"],
  updated: ["updated"],
  deployed: ["deployed", "started", "succeeded", "failed"],
  deleted: ["deleted", "archived"],
};

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

function ActivityItem({ event }: { event: ActivityEvent }) {
  const ActionIcon = ACTION_ICONS[event.action];
  const actionColor = ACTION_COLORS[event.action];

  const content = (
    <span className="text-sm text-foreground">{event.description}</span>
  );

  return (
    <div className="flex items-start gap-3 px-5 py-3 transition-colors hover:bg-muted/20">
      <div
        className={cn(
          "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full",
          actionColor
        )}
      >
        <ActionIcon className="size-3.5" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Badge
            variant="outline"
            className={cn(
              "text-[10px] capitalize",
              RESOURCE_BADGE_COLORS[event.resourceType]
            )}
          >
            {event.resourceType}
          </Badge>
          {event.link ? (
            <Link to={event.link} className="hover:underline">
              {content}
            </Link>
          ) : (
            content
          )}
        </div>
      </div>

      <RelativeTime
        date={event.timestamp}
        className="shrink-0 text-xs text-muted-foreground"
      />
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-border">
        <Clock className="size-5 text-muted-foreground" />
      </div>
      <h3 className="text-sm font-medium">No activity yet</h3>
      <p className="mt-1 max-w-xs text-xs text-muted-foreground">{message}</p>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-destructive/40 bg-destructive/5">
        <AlertTriangle className="size-5 text-destructive" />
      </div>
      <h3 className="text-sm font-medium">Could not load activity</h3>
      <p className="mt-1 max-w-md text-xs text-muted-foreground">{message}</p>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="flex items-start gap-3 border-b border-border/50 px-5 py-3 last:border-0"
        >
          <div className="mt-0.5 size-7 shrink-0 animate-pulse rounded-full bg-muted" />
          <div className="flex-1 space-y-2">
            <div className="h-3 w-1/3 animate-pulse rounded bg-muted" />
            <div className="h-2 w-2/3 animate-pulse rounded bg-muted/60" />
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ActivityPage() {
  const [resourceFilter, setResourceFilter] = useUrlState(
    "resource",
    "all"
  ) as [ResourceFilterType, (v: ResourceFilterType) => void];
  const [actionFilter, setActionFilter] = useUrlState("action", "all") as [
    ActionFilterType,
    (v: ActionFilterType) => void,
  ];

  // Server-side filter only by resource type — the action verb may be a
  // namespaced dotted name (`secret.created`) on the wire so we can't
  // round-trip the simplified ActionFilterType against the audit endpoint.
  const auditQuery = useQuery({
    queryKey: ["audit", { resource_type: resourceFilter }],
    queryFn: () =>
      api.audit.list({
        resource_type: resourceFilter !== "all" ? resourceFilter : undefined,
        // The audit endpoint caps per_page at 100 (422 above that).
        per_page: 100,
      }),
  });

  const adapted = useMemo<ActivityEvent[]>(
    () => (auditQuery.data?.data ?? []).map(adaptAuditToActivity),
    [auditQuery.data]
  );

  const filtered = useMemo(() => {
    if (actionFilter === "all") return adapted;
    const allowed = ACTION_FILTER_MAP[actionFilter];
    return adapted.filter(
      (e): e is ActivityEvent =>
        e.action !== "other" && allowed.includes(e.action)
    );
  }, [adapted, actionFilter]);

  const grouped = useMemo(() => {
    const groups: { label: string; events: ActivityEvent[] }[] = [];
    let currentLabel = "";
    for (const event of filtered) {
      const label = getDayLabel(event.timestamp);
      if (label !== currentLabel) {
        currentLabel = label;
        groups.push({ label, events: [] });
      }
      groups[groups.length - 1].events.push(event);
    }
    return groups;
  }, [filtered]);

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Activity</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {auditQuery.isLoading
              ? "Loading…"
              : `${filtered.length} event${filtered.length !== 1 ? "s" : ""} across all resources`}
          </p>
        </div>
      </div>

      <div className="mb-4 flex items-center gap-3">
        <div className="flex items-center gap-1 rounded-lg border border-border p-0.5">
          {RESOURCE_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setResourceFilter(f.value)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                resourceFilter === f.value
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {f.label}
            </button>
          ))}
        </div>

        <select
          value={actionFilter}
          onChange={(e) => setActionFilter(e.target.value as ActionFilterType)}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none"
        >
          {ACTION_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </div>

      {auditQuery.isLoading ? (
        <LoadingState />
      ) : auditQuery.isError ? (
        <ErrorState
          message={
            auditQuery.error instanceof Error
              ? auditQuery.error.message
              : "Failed to fetch /api/v1/audit."
          }
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          message={
            adapted.length === 0
              ? "Activity will appear here as you create, update, and deploy resources."
              : "No events match the current filters."
          }
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          {grouped.map((group) => (
            <div key={group.label}>
              <div className="sticky top-0 z-10 border-b border-border bg-muted/30 px-5 py-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                {group.label}
              </div>
              {group.events.map((event) => (
                <div
                  key={event.id}
                  className="border-b border-border/50 last:border-0"
                >
                  <ActivityItem event={event} />
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
