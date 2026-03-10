import { useQuery } from "@tanstack/react-query";
import { Cpu, Search, Circle, Zap, Cloud } from "lucide-react";
import { api, type Model } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useState } from "react";

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  openai: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  google: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  meta: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/20",
  mistral: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  cohere: "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20",
};

const SOURCE_ICONS: Record<string, typeof Zap> = {
  litellm: Cloud,
  manual: Zap,
};

function ModelRow({ model }: { model: Model }) {
  const isActive = model.status === "active";
  return (
    <div className="flex items-center gap-4 border-b border-border/50 px-5 py-3 transition-colors last:border-0 hover:bg-muted/20">
      <Circle
        className={cn(
          "size-1.5 shrink-0 fill-current",
          isActive ? "text-emerald-500" : "text-muted-foreground"
        )}
      />

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm">{model.name}</span>
        </div>
        {model.description && (
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {model.description}
          </p>
        )}
      </div>

      <Badge
        variant="outline"
        className={cn(
          "text-[10px] font-medium",
          PROVIDER_COLORS[model.provider.toLowerCase()] ??
            "bg-muted text-muted-foreground border-border"
        )}
      >
        {model.provider}
      </Badge>

      <div className="flex w-20 items-center justify-end gap-1.5 text-xs text-muted-foreground">
        {(() => {
          const Icon = SOURCE_ICONS[model.source] ?? Zap;
          return <Icon className="size-3" />;
        })()}
        <span>{model.source}</span>
      </div>
    </div>
  );
}

function EmptyState({ hasFilter }: { hasFilter: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-border">
        <Cpu className="size-5 text-muted-foreground" />
      </div>
      <h3 className="text-sm font-medium">
        {hasFilter ? "No models match your filters" : "No models registered"}
      </h3>
      <p className="mt-1 max-w-xs text-xs text-muted-foreground">
        {hasFilter
          ? "Try adjusting your search or filters."
          : "Connect a LiteLLM gateway or register models manually to see them here."}
      </p>
    </div>
  );
}

export default function ModelsPage() {
  const [search, setSearch] = useState("");
  const [providerFilter, setProviderFilter] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["models", { providerFilter }],
    queryFn: () =>
      api.models.list({ provider: providerFilter || undefined }),
    staleTime: 10_000,
  });

  const models = data?.data ?? [];
  const total = data?.meta.total ?? 0;
  const filtered = search
    ? models.filter(
        (m) =>
          m.name.toLowerCase().includes(search.toLowerCase()) ||
          m.description.toLowerCase().includes(search.toLowerCase()) ||
          m.provider.toLowerCase().includes(search.toLowerCase())
      )
    : models;

  // Extract unique providers for the filter dropdown
  const providers = [...new Set(models.map((m) => m.provider))].sort();

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6">
        <h1 className="text-lg font-semibold tracking-tight">Models</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {total} model{total !== 1 ? "s" : ""} in registry
        </p>
      </div>

      {/* Filters */}
      <div className="mb-4 flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Filter models..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 pl-9 text-xs"
          />
        </div>
        <select
          value={providerFilter}
          onChange={(e) => setProviderFilter(e.target.value)}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none"
        >
          <option value="">All providers</option>
          {providers.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-lg border border-border">
        <div className="flex items-center gap-4 border-b border-border bg-muted/30 px-5 py-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          <span className="w-1.5" />
          <span className="flex-1">Model</span>
          <span className="w-24 text-center">Provider</span>
          <span className="w-20 text-right">Source</span>
        </div>

        {isLoading ? (
          <div className="space-y-0">
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                className="flex items-center gap-4 border-b border-border/50 px-5 py-3 last:border-0"
              >
                <div className="size-1.5 animate-pulse rounded-full bg-muted" />
                <div className="flex-1 space-y-1.5">
                  <div className="h-3.5 w-40 animate-pulse rounded bg-muted" />
                  <div className="h-2.5 w-56 animate-pulse rounded bg-muted/60" />
                </div>
                <div className="h-5 w-16 animate-pulse rounded-full bg-muted" />
                <div className="h-3 w-14 animate-pulse rounded bg-muted" />
              </div>
            ))}
          </div>
        ) : error ? (
          <div className="px-6 py-12 text-center text-sm text-destructive">
            Failed to load models: {(error as Error).message}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState hasFilter={!!(search || providerFilter)} />
        ) : (
          filtered.map((model) => <ModelRow key={model.id} model={model} />)
        )}
      </div>
    </div>
  );
}
