import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams, Link, useNavigate } from "react-router-dom";
import { ArrowLeft, Cpu, Plus, X } from "lucide-react";
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
  code: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border-cyan-500/20",
  reasoning: "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20",
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

/** Get max value across models for a numeric field (used for bar scaling). */
function maxValue(models: Model[], extract: (m: Model) => number | null): number {
  let mx = 0;
  for (const m of models) {
    const v = extract(m);
    if (v != null && v > mx) mx = v;
  }
  return mx;
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

function ComparisonBar({
  value,
  maxVal,
  label,
  isBest,
  colorClass,
}: {
  value: number | null;
  maxVal: number;
  label: string;
  isBest: boolean;
  colorClass: string;
}) {
  if (value == null || maxVal === 0) {
    return <span className="text-xs text-muted-foreground">--</span>;
  }
  const pct = Math.max((value / maxVal) * 100, 4);

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5">
        <span className={cn("font-mono text-sm", isBest && "font-semibold")}>{label}</span>
        {isBest && (
          <span className="text-[9px] font-medium text-emerald-600 dark:text-emerald-400">
            BEST
          </span>
        )}
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={cn("h-full rounded-full transition-all duration-500", colorClass)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function ModelSelector({
  currentIds,
  onAdd,
}: {
  currentIds: string[];
  onAdd: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);

  const { data } = useQuery({
    queryKey: ["models-list-for-compare"],
    queryFn: () => api.models.list({ per_page: 100 } as Parameters<typeof api.models.list>[0]),
    enabled: open,
    staleTime: 30_000,
  });

  const allModels: Model[] = data?.data ?? [];
  const available = allModels.filter((m) => !currentIds.includes(m.id));

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground transition-colors hover:bg-muted/30 hover:text-foreground"
      >
        <Plus className="size-3" />
        Add model
      </button>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-background p-2 shadow-sm">
      <div className="mb-2 flex items-center justify-between px-1">
        <span className="text-xs font-medium text-muted-foreground">Select a model</span>
        <button
          onClick={() => setOpen(false)}
          className="rounded p-0.5 text-muted-foreground hover:text-foreground"
        >
          <X className="size-3" />
        </button>
      </div>
      {available.length === 0 ? (
        <p className="px-1 py-2 text-xs text-muted-foreground">No more models available</p>
      ) : (
        <div className="max-h-48 space-y-0.5 overflow-y-auto">
          {available.map((m) => (
            <button
              key={m.id}
              onClick={() => {
                onAdd(m.id);
                setOpen(false);
              }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-muted/50"
            >
              <Cpu className="size-3 shrink-0 text-muted-foreground" />
              <span className="font-mono text-xs">{m.name}</span>
              <Badge
                variant="outline"
                className={cn(
                  "ml-auto text-[9px]",
                  PROVIDER_COLORS[m.provider.toLowerCase()] ?? "bg-muted text-muted-foreground border-border"
                )}
              >
                {m.provider}
              </Badge>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ModelComparePage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
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

  function addModel(id: string) {
    const newIds = [...ids, id];
    navigate(`/models/compare?ids=${newIds.join(",")}`, { replace: true });
  }

  function removeModel(id: string) {
    const newIds = ids.filter((i) => i !== id);
    navigate(`/models/compare?ids=${newIds.join(",")}`, { replace: true });
  }

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
          <div className="mt-6 w-72">
            <ModelSelector currentIds={ids} onAdd={addModel} />
          </div>
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

  const maxContext = maxValue(models, (m) => m.context_window);
  const maxOutput = maxValue(models, (m) => m.max_output_tokens);
  const maxInputPrice = maxValue(models, (m) => m.input_price_per_million);
  const maxOutputPrice = maxValue(models, (m) => m.output_price_per_million);

  return (
    <div className="mx-auto max-w-5xl p-6">
      <Link
        to="/models"
        className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3" /> Models
      </Link>

      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Model Comparison</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Comparing {models.length} models
          </p>
        </div>
        {models.length < 3 && (
          <ModelSelector currentIds={ids} onAdd={addModel} />
        )}
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
            <div key={m.id} className="flex items-center gap-2 px-4 py-3">
              <Link
                to={`/models/${m.id}`}
                className="font-mono text-sm font-medium hover:underline"
              >
                {m.name}
              </Link>
              {models.length > 2 && (
                <button
                  onClick={() => removeModel(m.id)}
                  className="ml-auto rounded p-0.5 text-muted-foreground hover:text-foreground"
                  title="Remove from comparison"
                >
                  <X className="size-3" />
                </button>
              )}
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
          render={(m, i) => (
            <ComparisonBar
              value={m.context_window}
              maxVal={maxContext}
              label={formatContextWindow(m.context_window)}
              isBest={i === bestContextIdx}
              colorClass="bg-blue-500"
            />
          )}
          isOdd={false}
        />

        <CompareRow
          label="Max Output"
          models={models}
          extract={(m) => m.max_output_tokens}
          highlightBest={bestOutputIdx}
          render={(m, i) => (
            <ComparisonBar
              value={m.max_output_tokens}
              maxVal={maxOutput}
              label={formatContextWindow(m.max_output_tokens)}
              isBest={i === bestOutputIdx}
              colorClass="bg-violet-500"
            />
          )}
          isOdd
        />

        <CompareRow
          label="Input Price"
          models={models}
          extract={(m) => m.input_price_per_million}
          highlightBest={bestInputPriceIdx}
          render={(m, i) => (
            <ComparisonBar
              value={m.input_price_per_million}
              maxVal={maxInputPrice}
              label={m.input_price_per_million != null ? `${formatPrice(m.input_price_per_million)} / 1M` : "--"}
              isBest={i === bestInputPriceIdx}
              colorClass="bg-emerald-500"
            />
          )}
          isOdd={false}
        />

        <CompareRow
          label="Output Price"
          models={models}
          extract={(m) => m.output_price_per_million}
          highlightBest={bestOutputPriceIdx}
          render={(m, i) => (
            <ComparisonBar
              value={m.output_price_per_million}
              maxVal={maxOutputPrice}
              label={m.output_price_per_million != null ? `${formatPrice(m.output_price_per_million)} / 1M` : "--"}
              isBest={i === bestOutputPriceIdx}
              colorClass="bg-amber-500"
            />
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
