import { useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Circle,
  Clock,
  Server,
  Wrench,
  RefreshCw,
  Loader2,
  CheckCircle2,
  AlertCircle,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { api, type McpServer, type McpServerDiscoveredTool } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { SchemaViewer } from "@/components/schema-viewer";
import { RelativeTime } from "@/components/ui/relative-time";
import { cn } from "@/lib/utils";

const TRANSPORT_LABELS: Record<string, string> = {
  stdio: "stdio",
  sse: "SSE",
  streamable_http: "Streamable HTTP",
};

const STATUS_CONFIG: Record<
  string,
  { dotColor: string; bgColor: string; label: string }
> = {
  active: {
    dotColor: "text-emerald-500",
    bgColor: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    label: "Active",
  },
  inactive: {
    dotColor: "text-muted-foreground",
    bgColor: "bg-muted text-muted-foreground",
    label: "Inactive",
  },
  error: {
    dotColor: "text-red-500",
    bgColor: "bg-red-500/10 text-red-600 dark:text-red-400",
    label: "Error",
  },
};

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <dt className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function DiscoveredToolCard({
  tool,
}: {
  tool: McpServerDiscoveredTool;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-md border border-border">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-muted/20"
      >
        {expanded ? (
          <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
        )}
        <Wrench className="size-3.5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <span className="text-xs font-medium">{tool.name}</span>
          {tool.description && (
            <p className="text-[10px] text-muted-foreground line-clamp-1">
              {tool.description}
            </p>
          )}
        </div>
      </button>
      {expanded && (
        <div className="border-t border-border px-3 py-3">
          <SchemaViewer schema={tool.schema_definition} />
        </div>
      )}
    </div>
  );
}

// Mock uptime data for the chart
function UptimeChart() {
  const bars = useMemo(
    () =>
      Array.from({ length: 30 }, (_, i) => {
        // Deterministic mock data based on index
        const v = ((i * 7 + 3) % 10);
        return v > 0 ? "up" : i % 2 === 0 ? "slow" : "down";
      }),
    [],
  );

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-[10px] text-muted-foreground">
        <span>30 days ago</span>
        <span>Today</span>
      </div>
      <div className="flex gap-0.5">
        {bars.map((status, i) => (
          <div
            key={i}
            className={cn(
              "h-6 flex-1 rounded-sm",
              status === "up"
                ? "bg-emerald-500/60"
                : status === "slow"
                  ? "bg-amber-500/60"
                  : "bg-red-500/60"
            )}
          />
        ))}
      </div>
      <div className="flex items-center gap-4 text-[10px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <span className="inline-block size-2 rounded-sm bg-emerald-500/60" />{" "}
          Up
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block size-2 rounded-sm bg-amber-500/60" />{" "}
          Slow
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block size-2 rounded-sm bg-red-500/60" />{" "}
          Down
        </span>
      </div>
    </div>
  );
}

export default function McpServerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["mcp-server", id],
    queryFn: () => api.mcpServers.get(id!),
    enabled: !!id,
  });

  const [discoveredTools, setDiscoveredTools] = useState<
    McpServerDiscoveredTool[]
  >([]);
  const [hasDiscovered, setHasDiscovered] = useState(false);

  const testMutation = useMutation({
    mutationFn: () => api.mcpServers.test(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mcp-server", id] });
    },
  });

  const discoverMutation = useMutation({
    mutationFn: () => api.mcpServers.discover(id!),
    onSuccess: (resp) => {
      setDiscoveredTools(resp.data.tools);
      setHasDiscovered(true);
      queryClient.invalidateQueries({ queryKey: ["mcp-server", id] });
    },
  });

  const server: McpServer | undefined = data?.data;

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

  if (error || !server) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <Link
          to="/mcp-servers"
          className="mb-4 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" /> Back to MCP Servers
        </Link>
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center">
          <p className="text-sm text-destructive">
            {error ? (error as Error).message : "MCP server not found"}
          </p>
        </div>
      </div>
    );
  }

  const statusConfig = STATUS_CONFIG[server.status] ?? STATUS_CONFIG.inactive;

  return (
    <div className="mx-auto max-w-5xl p-6">
      <Link
        to="/mcp-servers"
        className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3" /> MCP Servers
      </Link>

      {/* Header */}
      <div className="flex items-start gap-4">
        <div className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-muted">
          <Server className="size-5 text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold tracking-tight">
              {server.name}
            </h1>
            <div
              className={cn(
                "flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium",
                statusConfig.bgColor
              )}
            >
              <Circle className="size-1.5 fill-current" />
              {statusConfig.label}
            </div>
            <Badge variant="outline" className="text-[10px]">
              {TRANSPORT_LABELS[server.transport] ?? server.transport}
            </Badge>
          </div>
          <code className="rounded bg-muted px-2 py-1 font-mono text-xs text-muted-foreground">
            {server.endpoint}
          </code>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => testMutation.mutate()}
            disabled={testMutation.isPending}
            className="flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium transition-colors hover:bg-muted disabled:opacity-50"
          >
            {testMutation.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <RefreshCw className="size-3" />
            )}
            Test
          </button>
          <button
            onClick={() => discoverMutation.mutate()}
            disabled={discoverMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background transition-colors hover:bg-foreground/90 disabled:opacity-50"
          >
            {discoverMutation.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <Wrench className="size-3" />
            )}
            Discover Tools
          </button>
        </div>
      </div>

      {/* Test result feedback */}
      {testMutation.isSuccess && (
        <div className="mt-4 flex items-center gap-2 rounded-md bg-emerald-500/10 px-3 py-2 text-xs text-emerald-600 dark:text-emerald-400">
          <CheckCircle2 className="size-3" />
          Connection test passed ({testMutation.data.data.latency_ms}ms)
        </div>
      )}
      {testMutation.isError && (
        <div className="mt-4 flex items-center gap-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <AlertCircle className="size-3" />
          Connection test failed: {(testMutation.error as Error).message}
        </div>
      )}

      {/* Content grid */}
      <div className="mt-8 grid gap-6 md:grid-cols-2">
        {/* Left column */}
        <div className="space-y-6">
          {/* Server info */}
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-4 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Server Details
            </h3>
            <dl className="space-y-4">
              <Field label="Transport">
                <Badge variant="outline" className="text-[10px]">
                  {TRANSPORT_LABELS[server.transport] ?? server.transport}
                </Badge>
              </Field>
              <Field label="Tool Count">
                <span className="flex items-center gap-1.5">
                  <Wrench className="size-3 text-muted-foreground" />
                  {server.tool_count} tool{server.tool_count !== 1 ? "s" : ""}
                </span>
              </Field>
              <Field label="Last Ping">
                <span className="flex items-center gap-1.5">
                  <Clock className="size-3 text-muted-foreground" />
                  {server.last_ping_at ? (
                    <RelativeTime date={server.last_ping_at} />
                  ) : (
                    "Never"
                  )}
                </span>
              </Field>
              <Field label="Created">
                <span className="flex items-center gap-1.5">
                  <Clock className="size-3 text-muted-foreground" />
                  <RelativeTime date={server.created_at} />
                </span>
              </Field>
            </dl>
          </div>

          {/* Uptime chart */}
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-4 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Uptime (30 days)
            </h3>
            <UptimeChart />
          </div>
        </div>

        {/* Right column: Tools */}
        <div className="space-y-6">
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-4 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              <Wrench className="size-3" />
              Discovered Tools
              {hasDiscovered && (
                <Badge variant="secondary" className="text-[10px]">
                  {discoveredTools.length}
                </Badge>
              )}
            </h3>

            {!hasDiscovered ? (
              <div className="flex flex-col items-center justify-center py-8 text-center">
                <Wrench className="mb-2 size-5 text-muted-foreground/40" />
                <p className="text-xs text-muted-foreground">
                  Click "Discover Tools" to scan this server
                </p>
              </div>
            ) : discoveredTools.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-center">
                <AlertCircle className="mb-2 size-5 text-muted-foreground/40" />
                <p className="text-xs text-muted-foreground">
                  No tools found on this server
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {discoveredTools.map((tool) => (
                  <DiscoveredToolCard key={tool.name} tool={tool} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
