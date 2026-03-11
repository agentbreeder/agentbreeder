import { useQuery } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import {
  ArrowLeft,
  Circle,
  Clock,
  Cpu,
  DollarSign,
  Users,
  Zap,
} from "lucide-react";
import { api, type Model, type ModelUsage } from "@/lib/api";
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

function formatContextWindow(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(tokens % 1_000_000 === 0 ? 0 : 1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(tokens % 1_000 === 0 ? 0 : 1)}K`;
  return String(tokens);
}

function formatPrice(price: number): string {
  return `$${price.toFixed(2)}`;
}

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

function UsageSection({ modelId }: { modelId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["model-usage", modelId],
    queryFn: () => api.models.usage(modelId),
    staleTime: 10_000,
  });

  const agents: ModelUsage[] = data?.data ?? [];

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
      <p className="text-xs text-muted-foreground">No agents currently use this model.</p>
    );
  }

  return (
    <div className="space-y-1">
      {agents.map((a) => (
        <Link
          key={a.agent_id}
          to={`/agents/${a.agent_id}`}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-muted/30"
        >
          <Circle
            className={cn(
              "size-1.5 shrink-0 fill-current",
              a.agent_status === "running"
                ? "text-emerald-500"
                : "text-muted-foreground"
            )}
          />
          <span>{a.agent_name}</span>
          <Badge variant="outline" className="ml-auto text-[10px]">
            {a.usage_type}
          </Badge>
        </Link>
      ))}
    </div>
  );
}

export default function ModelDetailPage() {
  const { id } = useParams<{ id: string }>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["model", id],
    queryFn: () => api.models.get(id!),
    enabled: !!id,
  });

  const { data: usageData } = useQuery({
    queryKey: ["model-usage", id],
    queryFn: () => api.models.usage(id!),
    enabled: !!id,
    staleTime: 10_000,
  });

  const model: Model | undefined = data?.data;
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

  if (error || !model) {
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
            {error ? (error as Error).message : "Model not found"}
          </p>
        </div>
      </div>
    );
  }

  const isActive = model.status === "active";
  const providerColor =
    PROVIDER_COLORS[model.provider.toLowerCase()] ??
    "bg-muted text-muted-foreground border-border";

  return (
    <div className="mx-auto max-w-5xl p-6">
      <Link
        to="/models"
        className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3" /> Models
      </Link>

      {/* Header */}
      <div className="flex items-start gap-4">
        <div className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-muted">
          <Cpu className="size-5 text-muted-foreground" />
        </div>
        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold tracking-tight font-mono">{model.name}</h1>
            <Badge variant="outline" className={cn("text-[10px] font-medium", providerColor)}>
              {model.provider}
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
              {isActive ? "Active" : model.status}
            </div>
          </div>
          {model.description && (
            <p className="max-w-2xl text-sm text-muted-foreground">{model.description}</p>
          )}
        </div>
      </div>

      {/* Content grid */}
      <div className="mt-8 grid gap-6 md:grid-cols-2">
        {/* Left column */}
        <div className="space-y-6">
          {/* Specs */}
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-4 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              <Zap className="size-3" />
              Specifications
            </h3>
            <dl className="space-y-4">
              {model.context_window != null && (
                <Field label="Context Window">
                  <span className="font-mono text-sm">
                    {formatContextWindow(model.context_window)} tokens
                  </span>
                </Field>
              )}
              {model.max_output_tokens != null && (
                <Field label="Max Output Tokens">
                  <span className="font-mono text-sm">
                    {formatContextWindow(model.max_output_tokens)} tokens
                  </span>
                </Field>
              )}
              {model.capabilities && model.capabilities.length > 0 && (
                <Field label="Capabilities">
                  <div className="flex flex-wrap gap-1">
                    {model.capabilities.map((cap) => (
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
                    ))}
                  </div>
                </Field>
              )}
            </dl>
          </div>

          {/* Pricing */}
          {(model.input_price_per_million != null || model.output_price_per_million != null) && (
            <div className="rounded-lg border border-border p-4">
              <h3 className="mb-4 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                <DollarSign className="size-3" />
                Pricing
              </h3>
              <dl className="space-y-4">
                {model.input_price_per_million != null && (
                  <Field label="Input Price">
                    <span className="font-mono text-sm">
                      {formatPrice(model.input_price_per_million)} / 1M tokens
                    </span>
                  </Field>
                )}
                {model.output_price_per_million != null && (
                  <Field label="Output Price">
                    <span className="font-mono text-sm">
                      {formatPrice(model.output_price_per_million)} / 1M tokens
                    </span>
                  </Field>
                )}
              </dl>
            </div>
          )}
        </div>

        {/* Right column */}
        <div className="space-y-6">
          {/* Usage */}
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-4 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              <Users className="size-3" />
              Used by {usageCount} agent{usageCount !== 1 ? "s" : ""}
            </h3>
            <UsageSection modelId={id!} />
          </div>

          {/* Metadata */}
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-4 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Metadata
            </h3>
            <dl className="space-y-4">
              <Field label="Source">
                <Badge variant="outline" className="text-[10px]">
                  {model.source}
                </Badge>
              </Field>
              <Field label="Created">
                <span className="flex items-center gap-1.5 text-sm">
                  <Clock className="size-3 text-muted-foreground" />
                  {new Date(model.created_at).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </Field>
            </dl>
          </div>
        </div>
      </div>
    </div>
  );
}
