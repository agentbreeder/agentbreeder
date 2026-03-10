import { useQuery } from "@tanstack/react-query";
import { FileText, Search, ChevronDown, ChevronRight, Users, Clock } from "lucide-react";
import { api, type Prompt } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useState, useMemo } from "react";

interface PromptGroup {
  name: string;
  versions: Prompt[];
  latestVersion: string;
  team: string;
  description: string;
}

function PromptCard({ group }: { group: PromptGroup }) {
  const [expanded, setExpanded] = useState(false);
  const latest = group.versions[0];

  return (
    <div className="overflow-hidden rounded-lg border border-border transition-all hover:border-border">
      {/* Header — always visible */}
      <div
        onClick={() => setExpanded(!expanded)}
        className="flex cursor-pointer items-start gap-3 p-4 transition-colors hover:bg-muted/20"
      >
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
          <FileText className="size-4 text-muted-foreground" />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium">{group.name}</h3>
            <Badge variant="outline" className="text-[10px] font-mono">
              v{group.latestVersion}
            </Badge>
            {group.versions.length > 1 && (
              <span className="text-[10px] text-muted-foreground">
                +{group.versions.length - 1} version{group.versions.length > 2 ? "s" : ""}
              </span>
            )}
          </div>
          {group.description && (
            <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
              {group.description}
            </p>
          )}
          <div className="mt-2 flex items-center gap-3">
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <Users className="size-2.5" />
              {group.team}
            </span>
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <Clock className="size-2.5" />
              {new Date(latest.created_at).toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
              })}
            </span>
          </div>
        </div>

        {expanded ? (
          <ChevronDown className="mt-1 size-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="mt-1 size-4 shrink-0 text-muted-foreground" />
        )}
      </div>

      {/* Expanded — shows prompt content */}
      {expanded && (
        <div className="border-t border-border">
          {group.versions.map((prompt, i) => (
            <div
              key={prompt.id}
              className={cn(
                "px-4 py-3",
                i > 0 && "border-t border-border/50"
              )}
            >
              <div className="mb-2 flex items-center gap-2">
                <Badge
                  variant="outline"
                  className={cn(
                    "font-mono text-[10px]",
                    i === 0
                      ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                      : ""
                  )}
                >
                  v{prompt.version}
                </Badge>
                {i === 0 && (
                  <span className="text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                    latest
                  </span>
                )}
                <span className="ml-auto text-[10px] text-muted-foreground">
                  {new Date(prompt.created_at).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                  })}
                </span>
              </div>
              <pre className="max-h-40 overflow-auto rounded-md bg-muted/50 px-3 py-2 font-mono text-xs leading-relaxed text-foreground/80">
                {prompt.content}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EmptyState({ hasFilter }: { hasFilter: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-border">
        <FileText className="size-5 text-muted-foreground" />
      </div>
      <h3 className="text-sm font-medium">
        {hasFilter ? "No prompts match your filters" : "No prompts registered"}
      </h3>
      <p className="mt-1 max-w-xs text-xs text-muted-foreground">
        {hasFilter
          ? "Try adjusting your search or filters."
          : "Register prompt templates via the API to manage and version them here."}
      </p>
    </div>
  );
}

export default function PromptsPage() {
  const [search, setSearch] = useState("");
  const [teamFilter, setTeamFilter] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["prompts", { teamFilter }],
    queryFn: () =>
      api.prompts.list({ team: teamFilter || undefined }),
    staleTime: 10_000,
  });

  const prompts = data?.data ?? [];
  const total = data?.meta.total ?? 0;

  // Group prompts by name, with versions sorted descending
  const groups = useMemo(() => {
    const map = new Map<string, Prompt[]>();
    for (const p of prompts) {
      const existing = map.get(p.name) ?? [];
      existing.push(p);
      map.set(p.name, existing);
    }
    const result: PromptGroup[] = [];
    for (const [name, versions] of map) {
      // Sort versions descending (newest first)
      versions.sort((a, b) => b.version.localeCompare(a.version, undefined, { numeric: true }));
      result.push({
        name,
        versions,
        latestVersion: versions[0].version,
        team: versions[0].team,
        description: versions[0].description,
      });
    }
    return result;
  }, [prompts]);

  const filtered = search
    ? groups.filter(
        (g) =>
          g.name.toLowerCase().includes(search.toLowerCase()) ||
          g.description.toLowerCase().includes(search.toLowerCase()) ||
          g.team.toLowerCase().includes(search.toLowerCase())
      )
    : groups;

  const teams = [...new Set(prompts.map((p) => p.team))].filter(Boolean).sort();

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6">
        <h1 className="text-lg font-semibold tracking-tight">Prompts</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {total} prompt{total !== 1 ? "s" : ""} across {groups.length} template
          {groups.length !== 1 ? "s" : ""} in registry
        </p>
      </div>

      {/* Filters */}
      <div className="mb-4 flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Filter prompts..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 pl-9 text-xs"
          />
        </div>
        <select
          value={teamFilter}
          onChange={(e) => setTeamFilter(e.target.value)}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none"
        >
          <option value="">All teams</option>
          {teams.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
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
                  <div className="h-4 w-36 animate-pulse rounded bg-muted" />
                  <div className="h-3 w-52 animate-pulse rounded bg-muted/60" />
                  <div className="h-3 w-24 animate-pulse rounded bg-muted/40" />
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : error ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center text-sm text-destructive">
          Failed to load prompts: {(error as Error).message}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState hasFilter={!!(search || teamFilter)} />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {filtered.map((group) => (
            <PromptCard key={group.name} group={group} />
          ))}
        </div>
      )}
    </div>
  );
}
