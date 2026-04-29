import { useQuery } from "@tanstack/react-query";
import { useParams, Link, useNavigate } from "react-router-dom";
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
  Pencil,
} from "lucide-react";
import { useState } from "react";
import { api, type ToolDetail, type ToolUsage, type ToolHealth, type ToolHealthStatus, type ToolRunResponse } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RelativeTime } from "@/components/ui/relative-time";
import { SchemaViewer } from "@/components/schema-viewer";
import { useUrlState } from "@/hooks/use-url-state";
import { cn } from "@/lib/utils";
import { Loader2, Play, AlertCircle, CheckCircle2 } from "lucide-react";

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
            <RelativeTime date={health.last_ping} />
          </span>
        </Field>
      )}
    </div>
  );
}

function HeaderHealthBadge({ toolId }: { toolId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["tool-health", toolId],
    queryFn: () => api.tools.health(toolId),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const health: ToolHealth | undefined = data?.data;

  if (isLoading) {
    return <div className="h-5 w-20 animate-pulse rounded-full bg-muted" />;
  }

  if (!health) return null;

  const config = HEALTH_CONFIG[health.status];

  return (
    <div
      className={cn(
        "flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium",
        config.bgColor
      )}
    >
      <Circle className={cn("size-1.5 fill-current", config.dotColor)} />
      {config.label}
      {health.latency_ms != null && (
        <span className="font-mono text-[10px] opacity-75">{health.latency_ms}ms</span>
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
      <div className="flex flex-col items-center justify-center py-6 text-center">
        <Users className="size-5 text-muted-foreground/40 mb-2" />
        <p className="text-xs text-muted-foreground">No agents are using this tool yet</p>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {agents.map((a) => (
        <Link
          key={a.agent_id}
          to={`/agents/${a.agent_id}`}
          className="flex items-center gap-3 rounded-md px-2 py-2 text-sm transition-colors hover:bg-muted/30"
        >
          <Circle
            className={cn(
              "size-1.5 shrink-0 fill-current",
              a.agent_status === "running"
                ? "text-emerald-500"
                : a.agent_status === "failed"
                  ? "text-red-500"
                  : "text-muted-foreground"
            )}
          />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate font-medium">{a.agent_name}</span>
              <span className="shrink-0 text-[10px] text-muted-foreground">
                v{a.agent_version}
              </span>
            </div>
            {a.last_deployed && (
              <p className="flex items-center gap-1 text-[10px] text-muted-foreground mt-0.5">
                <Clock className="size-2.5" />
                Deployed <RelativeTime date={a.last_deployed} />
              </p>
            )}
          </div>
          <Badge
            variant="outline"
            className={cn(
              "ml-auto shrink-0 text-[10px]",
              a.agent_status === "running"
                ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                : a.agent_status === "failed"
                  ? "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20"
                  : ""
            )}
          >
            {a.agent_status}
          </Badge>
        </Link>
      ))}
    </div>
  );
}

export default function ToolDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

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

  const [activeTab, setActiveTab] = useUrlState("tab", "overview");
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
            {tool.tool_type === "mcp_server" && (
              <HeaderHealthBadge toolId={id!} />
            )}
            <button
              onClick={() => navigate(`/tools/builder/${id}`)}
              className="ml-2 flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <Pencil className="size-3" />
              Edit in Builder
            </button>
          </div>
          {tool.description && (
            <p className="max-w-2xl text-sm text-muted-foreground">{tool.description}</p>
          )}
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="mt-6">
        <TabsList variant="line">
          <TabsTrigger value="overview" className="text-xs">
            Overview
          </TabsTrigger>
          <TabsTrigger value="usage" className="text-xs">
            Usage ({usageCount})
          </TabsTrigger>
          <TabsTrigger value="try" className="text-xs">
            Try it
          </TabsTrigger>
        </TabsList>

        <TabsContent value="try">
          <RunPanel toolId={id!} schema={tool.schema_definition} endpoint={tool.endpoint} />
        </TabsContent>

        <TabsContent value="overview">
          <div className="mt-4 grid gap-6 md:grid-cols-2">
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
                      <RelativeTime date={tool.created_at} />
                    </span>
                  </Field>
                  <Field label="Last Updated">
                    <span className="flex items-center gap-1.5 text-sm">
                      <Clock className="size-3 text-muted-foreground" />
                      <RelativeTime date={tool.updated_at} />
                    </span>
                  </Field>
                </dl>
              </div>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="usage">
          <div className="mt-4 max-w-2xl">
            <div className="rounded-lg border border-border p-4">
              <h3 className="mb-4 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                <Users className="size-3" />
                Agents using this tool
              </h3>
              <UsageSection toolId={id!} />
            </div>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Run panel — execute the registered tool against its endpoint with structured
// args. Mirrors `agentbreeder registry tool run <name> --args '{…}'` from CLI.
// ────────────────────────────────────────────────────────────────────────────
function RunPanel({
  toolId,
  schema,
  endpoint,
}: {
  toolId: string;
  schema: Record<string, unknown> | null | undefined;
  endpoint: string | null | undefined;
}) {
  const initialArgs = (() => {
    const props = (schema as { properties?: Record<string, { default?: unknown }> } | null)
      ?.properties;
    if (!props) return "{}\n";
    const draft: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) {
      if (v && typeof v === "object" && "default" in v && v.default !== undefined) {
        draft[k] = v.default;
      }
    }
    return JSON.stringify(draft, null, 2);
  })();

  const [argsText, setArgsText] = useState<string>(initialArgs);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ToolRunResponse | null>(null);
  const [parseError, setParseError] = useState<string>("");

  const handleRun = async (): Promise<void> => {
    setParseError("");
    setResult(null);
    let parsed: Record<string, unknown>;
    try {
      parsed = argsText.trim() ? JSON.parse(argsText) : {};
      if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) {
        throw new Error("args must be a JSON object");
      }
    } catch (e) {
      setParseError(e instanceof Error ? e.message : String(e));
      return;
    }
    setRunning(true);
    try {
      const resp = await api.tools.run(toolId, parsed);
      setResult(resp.data);
    } catch (e) {
      setResult({
        output: null,
        stdout: "",
        stderr: "",
        exit_code: -1,
        duration_ms: 0,
        error: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="mt-4 grid gap-6 md:grid-cols-2">
      <div className="space-y-3">
        <div>
          <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Arguments (JSON)
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Endpoint:{" "}
            <code className="rounded bg-muted px-1 text-xs">{endpoint || "<none>"}</code>
          </p>
        </div>
        <textarea
          value={argsText}
          onChange={(e) => {
            setArgsText(e.target.value);
            setParseError("");
          }}
          spellCheck={false}
          className="h-72 w-full resize-none rounded-md border border-input bg-background p-3 font-mono text-xs leading-relaxed outline-none focus:ring-2 focus:ring-ring"
        />
        {parseError && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
            <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
            <span>{parseError}</span>
          </div>
        )}
        <div className="flex items-center justify-end gap-2">
          <Button onClick={handleRun} disabled={running || !endpoint} size="sm" className="gap-1.5">
            {running ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
            Run tool
          </Button>
        </div>
      </div>

      <div className="space-y-3">
        <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Result
        </h3>
        {result === null && !running && (
          <div className="flex h-72 items-center justify-center rounded-md border border-dashed border-input text-xs text-muted-foreground">
            Run the tool to see its output here.
          </div>
        )}
        {running && (
          <div className="flex h-72 items-center justify-center rounded-md border border-input text-xs text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" />
            Running…
          </div>
        )}
        {result && (
          <div className="space-y-2">
            <div
              className={
                "flex items-center gap-2 rounded-md border p-2 text-xs " +
                (result.error
                  ? "border-destructive/30 bg-destructive/10 text-destructive"
                  : "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400")
              }
            >
              {result.error ? (
                <AlertCircle className="size-3.5" />
              ) : (
                <CheckCircle2 className="size-3.5" />
              )}
              <span className="font-mono">
                exit={result.exit_code} • {result.duration_ms} ms
              </span>
            </div>
            {result.error && (
              <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md border border-destructive/30 bg-destructive/5 p-2 font-mono text-xs text-destructive">
                {result.error}
              </pre>
            )}
            <pre className="max-h-72 overflow-auto rounded-md border border-input bg-muted/40 p-3 font-mono text-xs">
              {JSON.stringify(result.output, null, 2)}
            </pre>
            {result.stderr && (
              <details className="rounded-md border border-input bg-muted/20 p-2 text-xs">
                <summary className="cursor-pointer font-mono">stderr ({result.stderr.length} bytes)</summary>
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap font-mono">
                  {result.stderr}
                </pre>
              </details>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
