import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Wrench, Search, Circle, Server, Plug, Code, Star, Plus } from "lucide-react";
import { api, type Tool } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { FavoriteButton } from "@/components/favorite-button";
import { ExportDropdown } from "@/components/export-dropdown";
import { useFavorites } from "@/hooks/use-favorites";
import { useSortable } from "@/hooks/use-sortable";
import { SortableColumnHeader } from "@/components/ui/sortable-header";
import { SkeletonCardGrid } from "@/components/ui/skeleton-table";
import { EmptyState } from "@/components/ui/empty-state";

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

function ToolCard({ tool, onClick }: { tool: Tool; onClick: () => void }) {
  const Icon = TYPE_ICONS[tool.tool_type] ?? Wrench;
  const isActive = tool.status === "active";

  return (
    <div
      onClick={onClick}
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
            <div className="ml-auto">
              <FavoriteButton id={tool.id} />
            </div>
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
    </div>
  );
}

export default function ToolsPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [showFavoritesOnly, setShowFavoritesOnly] = useState(false);
  const { showOnlyFavorites } = useFavorites();

  const { data, isLoading, error } = useQuery({
    queryKey: ["tools", { typeFilter }],
    queryFn: () =>
      api.tools.list({ tool_type: typeFilter || undefined }),
    staleTime: 10_000,
  });

  const tools = data?.data ?? [];
  const total = data?.meta.total ?? 0;
  let filtered = search
    ? tools.filter(
        (t) =>
          t.name.toLowerCase().includes(search.toLowerCase()) ||
          t.description.toLowerCase().includes(search.toLowerCase())
      )
    : tools;

  if (showFavoritesOnly) {
    filtered = showOnlyFavorites(filtered);
  }

  // Sortable
  const { sortedData, sortKey, sortDirection, toggleSort } = useSortable(
    filtered as unknown as Record<string, unknown>[],
    "name",
    "asc"
  );
  const sortedTools = sortedData as unknown as Tool[];

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Tools & MCP Servers</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {total} tool{total !== 1 ? "s" : ""} in registry
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => navigate("/tools/builder")}
            className="flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background transition-colors hover:bg-foreground/90"
          >
            <Plus className="size-3" />
            Create Tool
          </button>
          <ExportDropdown
            data={filtered as unknown as Record<string, unknown>[]}
            filename="tools"
          />
        </div>
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
        <button
          onClick={() => setShowFavoritesOnly(!showFavoritesOnly)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md border border-input px-2.5 py-1.5 text-xs font-medium transition-colors",
            showFavoritesOnly
              ? "border-amber-400/50 bg-amber-500/10 text-amber-600 dark:text-amber-400"
              : "text-muted-foreground hover:bg-muted"
          )}
        >
          <Star className={cn("size-3", showFavoritesOnly && "fill-amber-400")} />
          Favorites
        </button>
      </div>

      {/* Sort controls */}
      <div className="mb-3 flex items-center gap-4 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        <span>Sort by:</span>
        <SortableColumnHeader
          sortKey="name"
          currentSortKey={sortKey}
          currentDirection={sortDirection}
          onSort={toggleSort}
        >
          Name
        </SortableColumnHeader>
        <SortableColumnHeader
          sortKey="tool_type"
          currentSortKey={sortKey}
          currentDirection={sortDirection}
          onSort={toggleSort}
        >
          Type
        </SortableColumnHeader>
        <SortableColumnHeader
          sortKey="source"
          currentSortKey={sortKey}
          currentDirection={sortDirection}
          onSort={toggleSort}
        >
          Source
        </SortableColumnHeader>
        <SortableColumnHeader
          sortKey="created_at"
          currentSortKey={sortKey}
          currentDirection={sortDirection}
          onSort={toggleSort}
        >
          Created
        </SortableColumnHeader>
      </div>

      {/* Grid */}
      {isLoading ? (
        <SkeletonCardGrid cards={4} />
      ) : error ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center text-sm text-destructive">
          Failed to load tools: {(error as Error).message}
        </div>
      ) : sortedTools.length === 0 ? (
        <EmptyState
          icon={Wrench}
          title="No tools registered"
          description="Run `garden scan` to discover MCP servers and register them."
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {sortedTools.map((tool) => (
            <ToolCard
              key={tool.id}
              tool={tool}
              onClick={() => navigate(`/tools/${tool.id}`)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
