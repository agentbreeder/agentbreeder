import { useMemo } from "react";
import { Link } from "react-router-dom";
import {
  Clock,
  Plus,
  Pencil,
  Rocket,
  Trash2,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { RelativeTime } from "@/components/ui/relative-time";
import { cn } from "@/lib/utils";
import { useUrlState } from "@/hooks/use-url-state";
import { ComingSoonBanner } from "@/components/coming-soon-badge";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ResourceType = "agent" | "tool" | "prompt" | "model" | "deploy";
type ActionType = "created" | "updated" | "deployed" | "deleted" | "succeeded" | "failed" | "started" | "archived";

interface ActivityEvent {
  id: string;
  resourceType: ResourceType;
  resourceId: string;
  resourceName: string;
  action: ActionType;
  actor: string;
  timestamp: string; // ISO string
  description: string;
  link: string;
}

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const NOW = new Date("2026-03-11T14:30:00Z");

function hoursAgo(h: number): string {
  return new Date(NOW.getTime() - h * 3600_000).toISOString();
}

const MOCK_EVENTS: ActivityEvent[] = [
  {
    id: "ev-01",
    resourceType: "agent",
    resourceId: "a-101",
    resourceName: "customer-support-v2",
    action: "created",
    actor: "alice",
    timestamp: hoursAgo(0.5),
    description: "alice created agent customer-support-v2",
    link: "/agents/a-101",
  },
  {
    id: "ev-02",
    resourceType: "deploy",
    resourceId: "d-201",
    resourceName: "Deploy #42",
    action: "succeeded",
    actor: "system",
    timestamp: hoursAgo(1),
    description: "Deploy #42 succeeded for research-agent",
    link: "/deploys",
  },
  {
    id: "ev-03",
    resourceType: "tool",
    resourceId: "t-301",
    resourceName: "zendesk-mcp",
    action: "updated",
    actor: "bob",
    timestamp: hoursAgo(2),
    description: "bob updated tool zendesk-mcp",
    link: "/tools/t-301",
  },
  {
    id: "ev-04",
    resourceType: "prompt",
    resourceId: "p-401",
    resourceName: "support-system-v3",
    action: "updated",
    actor: "alice",
    timestamp: hoursAgo(3),
    description: "alice published new version of prompt support-system-v3",
    link: "/prompts/p-401",
  },
  {
    id: "ev-05",
    resourceType: "model",
    resourceId: "m-501",
    resourceName: "claude-sonnet-4",
    action: "created",
    actor: "carol",
    timestamp: hoursAgo(4),
    description: "carol added model claude-sonnet-4 to registry",
    link: "/models/m-501",
  },
  {
    id: "ev-06",
    resourceType: "deploy",
    resourceId: "d-202",
    resourceName: "Deploy #41",
    action: "failed",
    actor: "system",
    timestamp: hoursAgo(5),
    description: "Deploy #41 failed for data-monitor-agent",
    link: "/deploys",
  },
  {
    id: "ev-07",
    resourceType: "agent",
    resourceId: "a-102",
    resourceName: "research-agent",
    action: "deployed",
    actor: "bob",
    timestamp: hoursAgo(6),
    description: "bob deployed agent research-agent to AWS ECS",
    link: "/agents/a-102",
  },
  {
    id: "ev-08",
    resourceType: "tool",
    resourceId: "t-302",
    resourceName: "order-lookup",
    action: "created",
    actor: "dave",
    timestamp: hoursAgo(8),
    description: "dave created tool order-lookup",
    link: "/tools/t-302",
  },
  {
    id: "ev-09",
    resourceType: "agent",
    resourceId: "a-103",
    resourceName: "doc-analyzer",
    action: "updated",
    actor: "alice",
    timestamp: hoursAgo(18),
    description: "alice updated agent doc-analyzer configuration",
    link: "/agents/a-103",
  },
  {
    id: "ev-10",
    resourceType: "deploy",
    resourceId: "d-203",
    resourceName: "Deploy #40",
    action: "started",
    actor: "system",
    timestamp: hoursAgo(20),
    description: "Deploy #40 started for doc-analyzer",
    link: "/deploys",
  },
  {
    id: "ev-11",
    resourceType: "prompt",
    resourceId: "p-402",
    resourceName: "research-system-v1",
    action: "created",
    actor: "bob",
    timestamp: hoursAgo(26),
    description: "bob created prompt research-system-v1",
    link: "/prompts/p-402",
  },
  {
    id: "ev-12",
    resourceType: "model",
    resourceId: "m-502",
    resourceName: "gpt-4o",
    action: "updated",
    actor: "carol",
    timestamp: hoursAgo(30),
    description: "carol updated model gpt-4o rate limits",
    link: "/models/m-502",
  },
  {
    id: "ev-13",
    resourceType: "agent",
    resourceId: "a-104",
    resourceName: "legacy-chatbot",
    action: "archived",
    actor: "dave",
    timestamp: hoursAgo(32),
    description: "dave archived agent legacy-chatbot",
    link: "/agents/a-104",
  },
  {
    id: "ev-14",
    resourceType: "tool",
    resourceId: "t-303",
    resourceName: "slack-notifier",
    action: "created",
    actor: "alice",
    timestamp: hoursAgo(48),
    description: "alice created tool slack-notifier",
    link: "/tools/t-303",
  },
  {
    id: "ev-15",
    resourceType: "deploy",
    resourceId: "d-204",
    resourceName: "Deploy #39",
    action: "succeeded",
    actor: "system",
    timestamp: hoursAgo(50),
    description: "Deploy #39 succeeded for customer-support-v1",
    link: "/deploys",
  },
  {
    id: "ev-16",
    resourceType: "agent",
    resourceId: "a-105",
    resourceName: "customer-support-v1",
    action: "deployed",
    actor: "alice",
    timestamp: hoursAgo(50),
    description: "alice deployed agent customer-support-v1 to GCP Cloud Run",
    link: "/agents/a-105",
  },
  {
    id: "ev-17",
    resourceType: "model",
    resourceId: "m-503",
    resourceName: "gemini-2.0-flash",
    action: "deleted",
    actor: "carol",
    timestamp: hoursAgo(55),
    description: "carol removed model gemini-2.0-flash from registry",
    link: "/models/m-503",
  },
  {
    id: "ev-18",
    resourceType: "prompt",
    resourceId: "p-403",
    resourceName: "onboarding-flow-v2",
    action: "updated",
    actor: "dave",
    timestamp: hoursAgo(72),
    description: "dave published new version of prompt onboarding-flow-v2",
    link: "/prompts/p-403",
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const ACTION_ICONS: Record<string, typeof Plus> = {
  created: Plus,
  updated: Pencil,
  deployed: Rocket,
  deleted: Trash2,
  archived: Trash2,
  started: Rocket,
  succeeded: CheckCircle2,
  failed: XCircle,
};

const ACTION_COLORS: Record<string, string> = {
  created: "text-emerald-500 bg-emerald-500/10",
  updated: "text-blue-500 bg-blue-500/10",
  deployed: "text-violet-500 bg-violet-500/10",
  started: "text-violet-500 bg-violet-500/10",
  succeeded: "text-emerald-500 bg-emerald-500/10",
  deleted: "text-red-500 bg-red-500/10",
  archived: "text-red-500 bg-red-500/10",
  failed: "text-red-500 bg-red-500/10",
};


const RESOURCE_BADGE_COLORS: Record<ResourceType, string> = {
  agent: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20",
  tool: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  prompt: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
  model: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  deploy: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
};

function getDayLabel(dateStr: string): string {
  const date = new Date(dateStr);
  const today = new Date(NOW);
  today.setHours(0, 0, 0, 0);
  const eventDay = new Date(date);
  eventDay.setHours(0, 0, 0, 0);

  const diffDays = Math.floor((today.getTime() - eventDay.getTime()) / 86_400_000);

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  return date.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

type ResourceFilterType = "all" | ResourceType;
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

const ACTION_FILTER_MAP: Record<ActionFilterType, ActionType[]> = {
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
  const ActionIcon = ACTION_ICONS[event.action] ?? Plus;
  const actionColor = ACTION_COLORS[event.action] ?? "text-muted-foreground bg-muted";

  return (
    <div className="flex items-start gap-3 px-5 py-3 transition-colors hover:bg-muted/20">
      {/* Action icon */}
      <div
        className={cn(
          "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full",
          actionColor
        )}
      >
        <ActionIcon className="size-3.5" />
      </div>

      {/* Content */}
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
          <Link
            to={event.link}
            className="text-sm text-foreground hover:underline"
          >
            {event.description}
          </Link>
        </div>
      </div>

      {/* Timestamp */}
      <RelativeTime
        date={event.timestamp}
        className="shrink-0 text-xs text-muted-foreground"
      />
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-border">
        <Clock className="size-5 text-muted-foreground" />
      </div>
      <h3 className="text-sm font-medium">No activity yet</h3>
      <p className="mt-1 max-w-xs text-xs text-muted-foreground">
        Activity will appear here as you create, update, and deploy resources.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ActivityPage() {
  const [resourceFilter, setResourceFilter] = useUrlState("resource", "all") as [ResourceFilterType, (v: ResourceFilterType) => void];
  const [actionFilter, setActionFilter] = useUrlState("action", "all") as [ActionFilterType, (v: ActionFilterType) => void];

  const filtered = useMemo(() => {
    let events = MOCK_EVENTS;

    if (resourceFilter !== "all") {
      events = events.filter((e) => e.resourceType === resourceFilter);
    }

    if (actionFilter !== "all") {
      const allowed = ACTION_FILTER_MAP[actionFilter];
      events = events.filter((e) => allowed.includes(e.action));
    }

    return events;
  }, [resourceFilter, actionFilter]);

  // Group events by day
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
      <ComingSoonBanner
        feature="Real activity feed"
        issue="#209"
        description="The activity feed currently renders a hardcoded MOCK_EVENTS array. Wiring to the real /api/v1/audit endpoint (which already returns live audit-log entries) is in progress."
      />
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Activity</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {filtered.length} event{filtered.length !== 1 ? "s" : ""} across all resources
          </p>
        </div>
      </div>

      {/* Filters */}
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

      {/* Timeline */}
      {filtered.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          {grouped.map((group) => (
            <div key={group.label}>
              {/* Day header */}
              <div className="sticky top-0 z-10 border-b border-border bg-muted/30 px-5 py-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                {group.label}
              </div>

              {/* Events */}
              {group.events.map((event) => (
                <div key={event.id} className="border-b border-border/50 last:border-0">
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
