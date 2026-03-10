import { useQuery } from "@tanstack/react-query";
import { Wrench, Search, Circle, Server, Plug, Code } from "lucide-react";
import { api, type Tool } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useState } from "react";

const TYPE_ICONS: Record<string, typeof Wrench> = {
  mcp_server: Server,
  function: Code,
  api: Plug,
};

const SOURCE_COLORS: Record<string, string> = {
  mcp_scan: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  manual: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20",
  litellm: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
};

function ToolCard({ tool }: { tool: Tool }) {
  const Icon = TYPE_ICONS[tool.tool_type] ?? Wrench;
  const isActive = tool.status === "active";
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      onClick={() => setExpanded(!expanded)}
      className="cursor-pointer rounded-lg border border-border p-4 transition-all hover:border-border hover:bg-muted/20"
    >
      <div className="flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
          <Icon className="size-4 text-muted-foreground" />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium">{tool.name}</h3>
            <Circle
              className={cn(
                "size-1.5 fill-current",
                isActive ? "text-emerald-500" : "text-muted-foreground"
              )}
            />
          </div>
          {tool.description && (
            <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
              {tool.description}
            </p>
          )}

          <div className="mt-2 flex items-center gap-2">
            <Badge
              variant="outline"
              className={cn(
                "text-[10px]",
                SOURCE_COLORS[tool.source] ?? "bg-muted text-muted-foreground border-border"
              )}
            >
              {tool.source}
            </Badge>
            <span className="text-[10px] text-muted-foreground">{tool.tool_type}</span>
          </div>
        </div>
      </div>

      {expanded && tool.endpoint && (
        <div className="mt-3 rounded-md bg-muted/50 px-3 py-2">
          <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            Endpoint
          </span>
          <code className="mt-1 block truncate font-mono text-xs">{tool.endpoint}</code>
        </div>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-border">
        <Wrench className="size-5 text-muted-foreground" />
      </div>
      <h3 className="text-sm font-medium">No tools registered</h3>
      <p className="mt-1 max-w-xs text-xs text-muted-foreground">
        Run <code className="rounded bg-muted px-1">garden scan</code> to discover MCP
        servers and register them.
      </p>
    </div>
  );
}

export default function ToolsPage() {
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["tools", { typeFilter }],
    queryFn: () =>
      api.tools.list({ tool_type: typeFilter || undefined }),
    staleTime: 10_000,
  });

  const tools = data?.data ?? [];
  const total = data?.meta.total ?? 0;
  const filtered = search
    ? tools.filter(
        (t) =>
          t.name.toLowerCase().includes(search.toLowerCase()) ||
          t.description.toLowerCase().includes(search.toLowerCase())
      )
    : tools;

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6">
        <h1 className="text-lg font-semibold tracking-tight">Tools & MCP Servers</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {total} tool{total !== 1 ? "s" : ""} in registry
        </p>
      </div>

      {/* Filters */}
      <div className="mb-4 flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Filter tools..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 pl-9 text-xs"
          />
        </div>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none"
        >
          <option value="">All types</option>
          <option value="mcp_server">MCP Server</option>
          <option value="function">Function</option>
          <option value="api">API</option>
        </select>
      </div>

      {/* Grid */}
      {isLoading ? (
        <div className="grid gap-3 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="rounded-lg border border-border p-4">
              <div className="flex items-start gap-3">
                <div className="size-9 animate-pulse rounded-lg bg-muted" />
                <div className="flex-1 space-y-2">
                  <div className="h-4 w-32 animate-pulse rounded bg-muted" />
                  <div className="h-3 w-48 animate-pulse rounded bg-muted/60" />
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : error ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center text-sm text-destructive">
          Failed to load tools: {(error as Error).message}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {filtered.map((tool) => (
            <ToolCard key={tool.id} tool={tool} />
          ))}
        </div>
      )}
    </div>
  );
}
