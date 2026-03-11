import { useQuery } from "@tanstack/react-query";
import { useSearchParams, Link } from "react-router-dom";
import { ArrowLeft, Cpu } from "lucide-react";
import { api, type Model } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
  openai: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  google: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  meta: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  ollama: "bg-gray-500/10 text-gray-600 dark:text-gray-400 border-gray-500/20",
};

const CAPABILITY_COLORS: Record<string, string> = {
  text: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  vision: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  function_calling: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  embeddings: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
};

function formatContextWindow(tokens: number | null): string {
  if (tokens == null) return "--";
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(tokens % 1_000_000 === 0 ? 0 : 1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(tokens % 1_000 === 0 ? 0 : 1)}K`;
  return String(tokens);
}

function formatPrice(price: number | null): string {
  if (price == null) return "--";
  return `$${price.toFixed(2)}`;
}

/** Check if values differ across models for a given extractor. */
function valuesDiffer(models: Model[], extract: (m: Model) => unknown): boolean {
  const values = models.map(extract);
  const first = JSON.stringify(values[0]);
  return values.some((v) => JSON.stringify(v) !== first);
}

/** Find the best (min or max) index for a numeric field. */
function bestIndex(
  models: Model[],
  extract: (m: Model) => number | null,
  mode: "min" | "max"
): number {
  let bestIdx = -1;
  let bestVal: number | null = null;
  for (let i = 0; i < models.length; i++) {
    const v = extract(models[i]);
    if (v == null) continue;
    if (
      bestVal == null ||
      (mode === "min" && v < bestVal) ||
      (mode === "max" && v > bestVal)
    ) {
      bestVal = v;
      bestIdx = i;
    }
  }
  return bestIdx;
}

interface RowProps {
  label: string;
  models: Model[];
  render: (m: Model, idx: number) => React.ReactNode;
  extract?: (m: Model) => unknown;
  highlightBest?: number;
  isOdd: boolean;
}

function CompareRow({ label, models, render, extract, highlightBest, isOdd }: RowProps) {
  const differs = extract ? valuesDiffer(models, extract) : false;

  return (
    <div
      className={cn(
        "grid items-center gap-0",
        models.length === 2 ? "grid-cols-3" : "grid-cols-4",
        isOdd && "bg-muted/20"
      )}
    >
      <div className="px-4 py-3 text-xs font-medium text-muted-foreground">{label}</div>
      {models.map((m, i) => (
        <div
          key={m.id}
          className={cn(
            "px-4 py-3 text-sm",
            differs && "bg-amber-500/5",
            highlightBest === i && "bg-emerald-500/5"
          )}
        >
          {render(m, i)}
        </div>
      ))}
    </div>
  );
}

export default function ModelComparePage() {
  const [searchParams] = useSearchParams();
  const idsParam = searchParams.get("ids") ?? "";
  const ids = idsParam
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  const { data, isLoading, error } = useQuery({
    queryKey: ["models-compare", ids],
    queryFn: () => api.models.compare(ids),
    enabled: ids.length >= 2 && ids.length <= 3,
  });

  const models: Model[] = data?.data ?? [];

  if (ids.length < 2) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <Link
          to="/models"
          className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-3" /> Models
        </Link>
        <div className="flex flex-col items-center py-24 text-center">
          <Cpu className="mb-4 size-8 text-muted-foreground" />
          <h2 className="text-sm font-medium">Select 2-3 models to compare</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Go back to the models page and select models for comparison.
          </p>
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <div className="mb-6 h-4 w-20 animate-pulse rounded bg-muted" />
        <div className="h-96 animate-pulse rounded-lg border border-border bg-muted/30" />
      </div>
    );
  }

  if (error || models.length === 0) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <Link
          to="/models"
          className="mb-4 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" /> Back to models
        </Link>
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center">
          <p className="text-sm text-destructive">
            {error ? (error as Error).message : "Models not found"}
          </p>
        </div>
      </div>
    );
  }

  const bestContextIdx = bestIndex(models, (m) => m.context_window, "max");
  const bestOutputIdx = bestIndex(models, (m) => m.max_output_tokens, "max");
  const bestInputPriceIdx = bestIndex(models, (m) => m.input_price_per_million, "min");
  const bestOutputPriceIdx = bestIndex(models, (m) => m.output_price_per_million, "min");

  return (
    <div className="mx-auto max-w-5xl p-6">
      <Link
        to="/models"
        className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3" /> Models
      </Link>

      <div className="mb-6">
        <h1 className="text-lg font-semibold tracking-tight">Model Comparison</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Comparing {models.length} models
        </p>
      </div>

      <div className="overflow-hidden rounded-lg border border-border">
        {/* Header row: model names */}
        <div
          className={cn(
            "grid items-center border-b border-border bg-muted/30",
            models.length === 2 ? "grid-cols-3" : "grid-cols-4"
          )}
        >
          <div className="px-4 py-3" />
          {models.map((m) => (
            <div key={m.id} className="px-4 py-3">
              <Link
                to={`/models/${m.id}`}
                className="font-mono text-sm font-medium hover:underline"
              >
                {m.name}
              </Link>
            </div>
          ))}
        </div>

        {/* Data rows */}
        <CompareRow
          label="Provider"
          models={models}
          extract={(m) => m.provider}
          render={(m) => (
            <Badge
              variant="outline"
              className={cn(
                "text-[10px] font-medium",
                PROVIDER_COLORS[m.provider.toLowerCase()] ??
                  "bg-muted text-muted-foreground border-border"
              )}
            >
              {m.provider}
            </Badge>
          )}
          isOdd={false}
        />

        <CompareRow
          label="Status"
          models={models}
          extract={(m) => m.status}
          render={(m) => (
            <span
              className={cn(
                "text-xs",
                m.status === "active" ? "text-emerald-600" : "text-muted-foreground"
              )}
            >
              {m.status}
            </span>
          )}
          isOdd
        />

        <CompareRow
          label="Context Window"
          models={models}
          extract={(m) => m.context_window}
          highlightBest={bestContextIdx}
          render={(m) => (
            <span className="font-mono text-sm">{formatContextWindow(m.context_window)}</span>
          )}
          isOdd={false}
        />

        <CompareRow
          label="Max Output"
          models={models}
          extract={(m) => m.max_output_tokens}
          highlightBest={bestOutputIdx}
          render={(m) => (
            <span className="font-mono text-sm">{formatContextWindow(m.max_output_tokens)}</span>
          )}
          isOdd
        />

        <CompareRow
          label="Input Price"
          models={models}
          extract={(m) => m.input_price_per_million}
          highlightBest={bestInputPriceIdx}
          render={(m) => (
            <span className="font-mono text-sm">
              {m.input_price_per_million != null
                ? `${formatPrice(m.input_price_per_million)} / 1M`
                : "--"}
            </span>
          )}
          isOdd={false}
        />

        <CompareRow
          label="Output Price"
          models={models}
          extract={(m) => m.output_price_per_million}
          highlightBest={bestOutputPriceIdx}
          render={(m) => (
            <span className="font-mono text-sm">
              {m.output_price_per_million != null
                ? `${formatPrice(m.output_price_per_million)} / 1M`
                : "--"}
            </span>
          )}
          isOdd
        />

        <CompareRow
          label="Capabilities"
          models={models}
          extract={(m) => m.capabilities}
          render={(m) => (
            <div className="flex flex-wrap gap-1">
              {m.capabilities && m.capabilities.length > 0 ? (
                m.capabilities.map((cap) => (
                  <Badge
                    key={cap}
                    variant="outline"
                    className={cn(
                      "text-[10px]",
                      CAPABILITY_COLORS[cap] ?? "bg-muted text-muted-foreground border-border"
                    )}
                  >
                    {cap.replace(/_/g, " ")}
                  </Badge>
                ))
              ) : (
                <span className="text-xs text-muted-foreground">--</span>
              )}
            </div>
          )}
          isOdd={false}
        />
      </div>
    </div>
  );
}
