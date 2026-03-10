import { useSearchParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Search, Bot, Wrench, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";

const ENTITY_ICONS: Record<string, typeof Bot> = {
  agent: Bot,
  tool: Wrench,
};

const ENTITY_ROUTES: Record<string, string> = {
  agent: "/agents",
  tool: "/tools",
};

export default function SearchPage() {
  const [searchParams] = useSearchParams();
  const q = searchParams.get("q") ?? "";

  const { data, isLoading } = useQuery({
    queryKey: ["search", q],
    queryFn: () => api.search(q),
    enabled: q.length > 0,
  });

  const results = data?.data ?? [];

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="mb-6">
        <h1 className="text-lg font-semibold tracking-tight">Search Results</h1>
        {q && (
          <p className="mt-0.5 text-xs text-muted-foreground">
            {results.length} result{results.length !== 1 ? "s" : ""} for "{q}"
          </p>
        )}
      </div>

      {!q ? (
        <div className="flex flex-col items-center py-24 text-center">
          <Search className="mb-3 size-6 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            Use the search bar or <kbd className="rounded border border-border px-1 font-mono text-[10px]">⌘K</kbd> to search
          </p>
        </div>
      ) : isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="rounded-lg border border-border p-4">
              <div className="h-4 w-40 animate-pulse rounded bg-muted" />
              <div className="mt-2 h-3 w-64 animate-pulse rounded bg-muted/60" />
            </div>
          ))}
        </div>
      ) : results.length === 0 ? (
        <div className="flex flex-col items-center py-24 text-center">
          <p className="text-sm text-muted-foreground">No results found for "{q}"</p>
        </div>
      ) : (
        <div className="space-y-2">
          {results.map((r) => {
            const Icon = ENTITY_ICONS[r.entity_type] ?? Bot;
            const route = ENTITY_ROUTES[r.entity_type] ?? "/";
            return (
              <Link
                key={r.id}
                to={r.entity_type === "agent" ? `/agents/${r.id}` : route}
                className="group flex items-center gap-3 rounded-lg border border-border p-4 transition-colors hover:bg-muted/30"
              >
                <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted">
                  <Icon className="size-3.5 text-muted-foreground" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium group-hover:text-primary">
                      {r.name}
                    </span>
                    <Badge variant="outline" className="text-[10px]">
                      {r.entity_type}
                    </Badge>
                    {r.team && (
                      <span className="text-[10px] text-muted-foreground">{r.team}</span>
                    )}
                  </div>
                  {r.description && (
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {r.description}
                    </p>
                  )}
                </div>
                <ArrowRight className="size-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
