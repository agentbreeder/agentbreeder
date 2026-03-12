import { useQuery } from "@tanstack/react-query";
import { useSearchParams, Link } from "react-router-dom";
import { Bot, Search, Filter, Circle, Star } from "lucide-react";
import { api, type Agent, type AgentStatus } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useState, useMemo } from "react";
import { FavoriteButton } from "@/components/favorite-button";
import { ExportDropdown } from "@/components/export-dropdown";
import { TagFilter } from "@/components/tag-input";
import { useFavorites } from "@/hooks/use-favorites";

const STATUS_COLORS: Record<AgentStatus, string> = {
  running: "text-emerald-500",
  deploying: "text-amber-500 animate-pulse",
  stopped: "text-muted-foreground",
  failed: "text-destructive",
};

const FRAMEWORK_COLORS: Record<string, string> = {
  langgraph: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  crewai: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20",
  claude_sdk: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  openai_agents: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  google_adk: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  custom: "bg-muted text-muted-foreground border-border",
};

function StatusDot({ status }: { status: AgentStatus }) {
  return <Circle className={cn("size-2 fill-current", STATUS_COLORS[status])} />;
}

function AgentRow({ agent }: { agent: Agent }) {
  const age = timeSince(agent.updated_at);
  return (
    <Link
      to={`/agents/${agent.id}`}
      className="group flex items-center gap-4 border-b border-border/50 px-6 py-3.5 transition-colors last:border-0 hover:bg-muted/30"
    >
      <FavoriteButton id={agent.id} />
      <StatusDot status={agent.status} />

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium group-hover:text-primary">
            {agent.name}
          </span>
          <span className="text-xs text-muted-foreground">v{agent.version}</span>
        </div>
        {agent.description && (
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {agent.description}
          </p>
        )}
        {agent.tags && agent.tags.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {agent.tags.slice(0, 3).map((tag) => (
              <Badge key={tag} variant="outline" className="text-[9px] px-1.5 py-0 h-4">
                {tag}
              </Badge>
            ))}
            {agent.tags.length > 3 && (
              <span className="text-[9px] text-muted-foreground">
                +{agent.tags.length - 3}
              </span>
            )}
          </div>
        )}
      </div>

      <Badge
        variant="outline"
        className={cn("text-[10px] font-medium", FRAMEWORK_COLORS[agent.framework] ?? FRAMEWORK_COLORS.custom)}
      >
        {agent.framework}
      </Badge>

      <span className="w-20 text-right text-xs text-muted-foreground">{agent.team}</span>

      <span className="w-16 text-right font-mono text-[10px] text-muted-foreground">
        {age}
      </span>
    </Link>
  );
}

function EmptyState({ hasFilter }: { hasFilter: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-border">
        <Bot className="size-5 text-muted-foreground" />
      </div>
      <h3 className="text-sm font-medium">
        {hasFilter ? "No agents match your filters" : "No agents registered"}
      </h3>
      <p className="mt-1 max-w-xs text-xs text-muted-foreground">
        {hasFilter
          ? "Try adjusting your search or filters."
          : "Deploy an agent with `garden deploy` and it will appear here automatically."}
      </p>
    </div>
  );
}

export default function AgentsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState(searchParams.get("q") ?? "");
  const [framework, setFramework] = useState(searchParams.get("framework") ?? "");
  const [status, setStatus] = useState(searchParams.get("status") ?? "");
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [showFavoritesOnly, setShowFavoritesOnly] = useState(false);
  const { showOnlyFavorites, favorites } = useFavorites();

  const { data, isLoading, error } = useQuery({
    queryKey: ["agents", { search, framework, status }],
    queryFn: () =>
      search
        ? api.agents.search(search)
        : api.agents.list({
            framework: framework || undefined,
            status: (status as AgentStatus) || undefined,
          }),
    staleTime: 10_000,
  });

  const agents = data?.data ?? [];
  const total = data?.meta.total ?? 0;
  const hasFilter = !!(search || framework || status || activeTags.length > 0 || showFavoritesOnly);

  // Extract all unique tags from agents
  const allTags = useMemo(() => {
    const tagSet = new Set<string>();
    for (const agent of agents) {
      if (agent.tags) {
        for (const tag of agent.tags) tagSet.add(tag);
      }
    }
    return [...tagSet].sort();
  }, [agents]);

  // Apply tag filter
  let filteredAgents = activeTags.length > 0
    ? agents.filter((a) => a.tags && activeTags.some((t) => a.tags.includes(t)))
    : agents;

  if (showFavoritesOnly) {
    filteredAgents = showOnlyFavorites(filteredAgents);
  }

  const handleSearch = (value: string) => {
    setSearch(value);
    const sp = new URLSearchParams(searchParams);
    if (value) sp.set("q", value);
    else sp.delete("q");
    setSearchParams(sp, { replace: true });
  };

  const toggleTag = (tag: string) => {
    setActiveTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  };

  return (
    <div className="mx-auto max-w-5xl p-6">
      {/* Header */}
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Agents</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {total} agent{total !== 1 ? "s" : ""} in registry
          </p>
        </div>
        <ExportDropdown
          data={filteredAgents as unknown as Record<string, unknown>[]}
          filename="agents"
        />
      </div>

      {/* Filters */}
      <div className="mb-4 flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search agents..."
            value={search}
            onChange={(e) => handleSearch(e.target.value)}
            className="h-8 pl-9 text-xs"
          />
        </div>

        <div className="flex items-center gap-2">
          <Filter className="size-3.5 text-muted-foreground" />
          <select
            value={framework}
            onChange={(e) => setFramework(e.target.value)}
            className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none"
          >
            <option value="">All frameworks</option>
            <option value="langgraph">LangGraph</option>
            <option value="crewai">CrewAI</option>
            <option value="claude_sdk">Claude SDK</option>
            <option value="openai_agents">OpenAI Agents</option>
            <option value="google_adk">Google ADK</option>
            <option value="custom">Custom</option>
          </select>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none"
          >
            <option value="">All statuses</option>
            <option value="running">Running</option>
            <option value="deploying">Deploying</option>
            <option value="stopped">Stopped</option>
            <option value="failed">Failed</option>
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
      </div>

      {/* Tag filter */}
      {allTags.length > 0 && (
        <div className="mb-4">
          <TagFilter
            allTags={allTags}
            activeTags={activeTags}
            onToggle={toggleTag}
          />
        </div>
      )}

      {/* Table */}
      <div className="overflow-hidden rounded-lg border border-border">
        {/* Column headers */}
        <div className="flex items-center gap-4 border-b border-border bg-muted/30 px-6 py-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          <span className="w-3.5" />
          <span className="w-2" />
          <span className="flex-1">Agent</span>
          <span className="w-24 text-center">Framework</span>
          <span className="w-20 text-right">Team</span>
          <span className="w-16 text-right">Updated</span>
        </div>

        {isLoading ? (
          <div className="space-y-0">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 border-b border-border/50 px-6 py-3.5 last:border-0">
                <div className="size-3.5 animate-pulse rounded bg-muted" />
                <div className="size-2 animate-pulse rounded-full bg-muted" />
                <div className="flex-1 space-y-1.5">
                  <div className="h-3.5 w-36 animate-pulse rounded bg-muted" />
                  <div className="h-2.5 w-64 animate-pulse rounded bg-muted/60" />
                </div>
                <div className="h-5 w-16 animate-pulse rounded-full bg-muted" />
                <div className="h-3 w-14 animate-pulse rounded bg-muted" />
                <div className="h-3 w-10 animate-pulse rounded bg-muted" />
              </div>
            ))}
          </div>
        ) : error ? (
          <div className="px-6 py-12 text-center text-sm text-destructive">
            Failed to load agents: {(error as Error).message}
          </div>
        ) : filteredAgents.length === 0 ? (
          <EmptyState hasFilter={hasFilter} />
        ) : (
          filteredAgents.map((agent) => <AgentRow key={agent.id} agent={agent} />)
        )}
      </div>
    </div>
  );
}

function timeSince(dateStr: string): string {
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (seconds < 60) return "now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d`;
  return `${Math.floor(days / 30)}mo`;
}
