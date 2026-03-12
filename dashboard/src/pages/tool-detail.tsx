import { useQuery } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import {
  ArrowLeft,
  Circle,
  Clock,
  Server,
  Code,
  Plug,
  Wrench,
  Users,
  Activity,
} from "lucide-react";
import { api, type ToolDetail, type ToolUsage, type ToolHealth, type ToolHealthStatus } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { SchemaViewer } from "@/components/schema-viewer";
import { cn } from "@/lib/utils";

const TYPE_CONFIG: Record<string, { label: string; icon: typeof Server; color: string }> = {
  mcp_server: {
    label: "MCP Server",
    icon: Server,
    color: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  },
  function: {
    label: "Function",
    icon: Code,
    color: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  },
  api: {
    label: "API",
    icon: Plug,
    color: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  },
};

const HEALTH_CONFIG: Record<ToolHealthStatus, { label: string; dotColor: string; bgColor: string }> = {
  healthy: {
    label: "Healthy",
    dotColor: "text-emerald-500",
    bgColor: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  slow: {
    label: "Slow",
    dotColor: "text-amber-500",
    bgColor: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  down: {
    label: "Down",
    dotColor: "text-red-500",
    bgColor: "bg-red-500/10 text-red-600 dark:text-red-400",
  },
  unknown: {
    label: "Unknown",
    dotColor: "text-gray-400",
    bgColor: "bg-gray-500/10 text-gray-500 dark:text-gray-400",
  },
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <dt className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function HealthIndicator({ toolId }: { toolId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["tool-health", toolId],
    queryFn: () => api.tools.health(toolId),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const health: ToolHealth | undefined = data?.data;

  if (isLoading) {
    return <div className="h-6 w-24 animate-pulse rounded bg-muted" />;
  }

  if (!health) return null;

  const config = HEALTH_CONFIG[health.status];

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div
          className={cn(
            "flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium",
            config.bgColor
          )}
        >
          <Circle className={cn("size-1.5 fill-current", config.dotColor)} />
          {config.label}
        </div>
      </div>

      {health.latency_ms != null && (
        <Field label="Latency">
          <span className="font-mono text-sm">{health.latency_ms}ms</span>
        </Field>
      )}

      {health.last_ping && (
        <Field label="Last Ping">
          <span className="flex items-center gap-1.5 text-sm">
            <Clock className="size-3 text-muted-foreground" />
            {new Date(health.last_ping).toLocaleDateString("en-US", {
              year: "numeric",
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        </Field>
      )}
    </div>
  );
}

function UsageSection({ toolId }: { toolId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["tool-usage", toolId],
    queryFn: () => api.tools.usage(toolId),
    staleTime: 10_000,
  });

  const agents: ToolUsage[] = data?.data ?? [];

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="h-8 w-full animate-pulse rounded bg-muted" />
        ))}
      </div>
    );
  }

  if (agents.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">No agents currently use this tool.</p>
    );
  }

  return (
    <div className="space-y-1">
      {agents.map((a) => (
        <Link
          key={a.agent_id}
          to={`/agents/${a.agent_id}`}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-muted/30"
        >
          <Circle
            className={cn(
              "size-1.5 shrink-0 fill-current",
              a.agent_status === "running"
                ? "text-emerald-500"
                : "text-muted-foreground"
            )}
          />
          <span>{a.agent_name}</span>
          <Badge variant="outline" className="ml-auto text-[10px]">
            {a.agent_status}
          </Badge>
        </Link>
      ))}
    </div>
  );
}

export default function ToolDetailPage() {
  const { id } = useParams<{ id: string }>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["tool", id],
    queryFn: () => api.tools.get(id!),
    enabled: !!id,
  });

  const { data: usageData } = useQuery({
    queryKey: ["tool-usage", id],
    queryFn: () => api.tools.usage(id!),
    enabled: !!id,
    staleTime: 10_000,
  });

  const tool: ToolDetail | undefined = data?.data;
  const usageCount = usageData?.data?.length ?? 0;

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <div className="mb-6 h-4 w-20 animate-pulse rounded bg-muted" />
        <div className="space-y-3">
          <div className="h-6 w-48 animate-pulse rounded bg-muted" />
          <div className="h-4 w-96 animate-pulse rounded bg-muted/60" />
        </div>
      </div>
    );
  }

  if (error || !tool) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <Link
          to="/tools"
          className="mb-4 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" /> Back to tools
        </Link>
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center">
          <p className="text-sm text-destructive">
            {error ? (error as Error).message : "Tool not found"}
          </p>
        </div>
      </div>
    );
  }

  const typeConfig = TYPE_CONFIG[tool.tool_type] ?? {
    label: tool.tool_type,
    icon: Wrench,
    color: "bg-muted text-muted-foreground border-border",
  };
  const TypeIcon = typeConfig.icon;
  const isActive = tool.status === "active";

  return (
    <div className="mx-auto max-w-5xl p-6">
      <Link
        to="/tools"
        className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3" /> Tools
      </Link>

      {/* Header */}
      <div className="flex items-start gap-4">
        <div className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-muted">
          <TypeIcon className="size-5 text-muted-foreground" />
        </div>
        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold tracking-tight">{tool.name}</h1>
            <Badge variant="outline" className={cn("text-[10px]", typeConfig.color)}>
              {typeConfig.label}
            </Badge>
            <div
              className={cn(
                "flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium",
                isActive
                  ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                  : "bg-muted text-muted-foreground"
              )}
            >
              <Circle className="size-1.5 fill-current" />
              {isActive ? "Active" : tool.status}
            </div>
          </div>
          {tool.description && (
            <p className="max-w-2xl text-sm text-muted-foreground">{tool.description}</p>
          )}
        </div>
      </div>

      {/* Content grid */}
      <div className="mt-8 grid gap-6 md:grid-cols-2">
        {/* Left column */}
        <div className="space-y-6">
          {/* Schema */}
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-4 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Schema Definition
            </h3>
            <SchemaViewer schema={tool.schema_definition} />
          </div>

          {/* MCP Server info with health */}
          {tool.tool_type === "mcp_server" && (
            <div className="rounded-lg border border-border p-4">
              <h3 className="mb-4 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                <Activity className="size-3" />
                MCP Server
              </h3>
              <dl className="space-y-4">
                {tool.endpoint && (
                  <Field label="Endpoint URL">
                    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs break-all">
                      {tool.endpoint}
                    </code>
                  </Field>
                )}
                <div>
                  <dt className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground mb-2">
                    Health Status
                  </dt>
                  <dd>
                    <HealthIndicator toolId={id!} />
                  </dd>
                </div>
              </dl>
            </div>
          )}
        </div>

        {/* Right column */}
        <div className="space-y-6">
          {/* Usage */}
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-4 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              <Users className="size-3" />
              Used by {usageCount} agent{usageCount !== 1 ? "s" : ""}
            </h3>
            <UsageSection toolId={id!} />
          </div>

          {/* Metadata */}
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-4 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Metadata
            </h3>
            <dl className="space-y-4">
              <Field label="Source">
                <Badge variant="outline" className="text-[10px]">
                  {tool.source}
                </Badge>
              </Field>
              <Field label="Created">
                <span className="flex items-center gap-1.5 text-sm">
                  <Clock className="size-3 text-muted-foreground" />
                  {new Date(tool.created_at).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </Field>
              <Field label="Last Updated">
                <span className="flex items-center gap-1.5 text-sm">
                  <Clock className="size-3 text-muted-foreground" />
                  {new Date(tool.updated_at).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </Field>
            </dl>
          </div>
        </div>
      </div>
    </div>
  );
}
