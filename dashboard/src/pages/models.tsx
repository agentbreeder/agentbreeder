import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Cpu,
  Search,
  Circle,
  Zap,
  Cloud,
  GitCompareArrows,
  Plus,
  Star,
  ChevronLeft,
  RefreshCcw,
} from "lucide-react";
import { api, type Model } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogTrigger,
  DialogClose,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { FavoriteButton } from "@/components/favorite-button";
import { ExportDropdown } from "@/components/export-dropdown";
import { useFavorites } from "@/hooks/use-favorites";
import { useSortable } from "@/hooks/use-sortable";
import { SortableColumnHeader } from "@/components/ui/sortable-header";
import { SkeletonTableRows } from "@/components/ui/skeleton-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ColumnToggle, type ColumnDefinition } from "@/components/ui/column-toggle";
import { ProviderCatalog } from "@/components/provider-catalog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/hooks/use-toast";
import { useAuth } from "@/hooks/use-auth";

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  openai: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  google: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  meta: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/20",
  mistral: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  cohere: "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20",
  ollama: "bg-gray-500/10 text-gray-600 dark:text-gray-400 border-gray-500/20",
};

const SOURCE_ICONS: Record<string, typeof Zap> = {
  litellm: Cloud,
  manual: Zap,
};

const PROVIDER_TABS = ["All", "OpenAI", "Anthropic", "Google", "Ollama"] as const;

const MODEL_COLUMNS: ColumnDefinition[] = [
  { key: "name", label: "Model", locked: true },
  { key: "provider", label: "Provider" },
  { key: "status", label: "Status" },
  { key: "context_window", label: "Context" },
  { key: "price", label: "Price" },
  { key: "source", label: "Source" },
];

const DEFAULT_MODEL_COLUMNS = new Set(MODEL_COLUMNS.map((c) => c.key));

/** Track G — lifecycle status badge styling (active/beta/deprecated/retired). */
const STATUS_BADGE_CLASSES: Record<string, string> = {
  active: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  beta: "border-cyan-500/30 bg-cyan-500/10 text-cyan-600 dark:text-cyan-400",
  deprecated: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  retired: "border-border bg-muted text-muted-foreground",
};

function ModelStatusBadge({ status }: { status: string }) {
  const cls = STATUS_BADGE_CLASSES[status] ?? STATUS_BADGE_CLASSES.active;
  return (
    <Badge
      variant="outline"
      className={cn("text-[10px] font-medium capitalize", cls)}
      data-testid={`model-status-${status}`}
    >
      {status}
    </Badge>
  );
}

/** Format a token count compactly: 128000 -> "128K", 1000000 -> "1M" */
function formatContextWindow(tokens: number | null): string {
  if (tokens === null || tokens === undefined) return "--";
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(tokens % 1_000_000 === 0 ? 0 : 1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(tokens % 1_000 === 0 ? 0 : 1)}K`;
  return String(tokens);
}

/** Format price per million tokens as currency. */
function formatPrice(price: number | null): string {
  if (price === null || price === undefined) return "--";
  if (price === 0) return "Free";
  if (price < 0.01) return `$${price.toFixed(4)}`;
  return `$${price.toFixed(2)}`;
}

function ModelRow({
  model,
  isSelected,
  onToggleSelect,
  onClick,
  visibleColumns,
}: {
  model: Model;
  isSelected: boolean;
  onToggleSelect: () => void;
  onClick: () => void;
  visibleColumns: Set<string>;
}) {
  const isActive = model.status === "active";
  return (
    <div className="flex items-center gap-4 border-b border-border/50 px-5 py-3 transition-colors last:border-0 hover:bg-muted/20">
      <input
        type="checkbox"
        checked={isSelected}
        onChange={(e) => {
          e.stopPropagation();
          onToggleSelect();
        }}
        className="size-3.5 shrink-0 rounded border-border accent-foreground"
      />

      <FavoriteButton id={model.id} />

      <div
        className="flex min-w-0 flex-1 cursor-pointer items-center gap-4"
        onClick={onClick}
      >
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

        {visibleColumns.has("provider") && (
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
        )}

        {visibleColumns.has("status") && (
          <ModelStatusBadge status={model.status || "active"} />
        )}

        {visibleColumns.has("context_window") && (
          <div className="w-16 text-right font-mono text-xs text-muted-foreground">
            {formatContextWindow(model.context_window)}
          </div>
        )}

        {visibleColumns.has("price") && (
          <div className="w-28 text-right">
            <span className="font-mono text-[10px] text-muted-foreground">
              {formatPrice(model.input_price_per_million)}
              {" / "}
              {formatPrice(model.output_price_per_million)}
            </span>
          </div>
        )}

        {visibleColumns.has("source") && (
          <div className="flex w-20 items-center justify-end gap-1.5 text-xs text-muted-foreground">
            {(() => {
              const Icon = SOURCE_ICONS[model.source] ?? Zap;
              return <Icon className="size-3" />;
            })()}
            <span>{model.source}</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Model catalog ────────────────────────────────────────────────────────────

type CatalogModel = {
  name: string;
  description: string;
  contextWindow: number;
  inputPrice: number | null;
  outputPrice: number | null;
  capabilities: string[];
};

type ProviderCatalog = {
  id: string;
  label: string;
  color: string;
  models: CatalogModel[];
};

const PROVIDER_CATALOG: ProviderCatalog[] = [
  {
    id: "anthropic",
    label: "Anthropic",
    color: "bg-orange-500/10 text-orange-600 border-orange-500/20",
    models: [
      { name: "claude-opus-4-7",    description: "Most capable Claude model — complex reasoning, research, coding",    contextWindow: 200000, inputPrice: 15.00, outputPrice: 75.00,  capabilities: ["text", "vision", "tool_use", "thinking"] },
      { name: "claude-sonnet-4-6",  description: "Best balance of speed and intelligence — recommended for most tasks", contextWindow: 200000, inputPrice:  3.00, outputPrice: 15.00,  capabilities: ["text", "vision", "tool_use"] },
      { name: "claude-haiku-4-5",   description: "Fastest and most compact Claude model",                              contextWindow: 200000, inputPrice:  0.80, outputPrice:  4.00,  capabilities: ["text", "vision", "tool_use"] },
    ],
  },
  {
    id: "openai",
    label: "OpenAI",
    color: "bg-emerald-500/10 text-emerald-600 border-emerald-500/20",
    models: [
      { name: "gpt-4o",       description: "GPT-4o — multimodal flagship, fast and affordable",        contextWindow: 128000,  inputPrice:  2.50, outputPrice: 10.00, capabilities: ["text", "vision", "tool_use"] },
      { name: "gpt-4o-mini",  description: "Lightweight GPT-4o — cost-efficient for high-volume tasks", contextWindow: 128000,  inputPrice:  0.15, outputPrice:  0.60, capabilities: ["text", "vision", "tool_use"] },
      { name: "o3",           description: "OpenAI o3 — advanced reasoning model",                      contextWindow: 200000,  inputPrice: 10.00, outputPrice: 40.00, capabilities: ["text", "tool_use", "reasoning"] },
      { name: "o4-mini",      description: "OpenAI o4-mini — fast reasoning at lower cost",             contextWindow: 200000,  inputPrice:  1.10, outputPrice:  4.40, capabilities: ["text", "vision", "tool_use", "reasoning"] },
    ],
  },
  {
    id: "google",
    label: "Google",
    color: "bg-blue-500/10 text-blue-600 border-blue-500/20",
    models: [
      { name: "gemini-2.5-pro-preview",  description: "Gemini 2.5 Pro — best Google model, 1M context",       contextWindow: 1000000, inputPrice: 1.25, outputPrice: 10.00, capabilities: ["text", "vision", "tool_use", "reasoning"] },
      { name: "gemini-2.0-flash",        description: "Gemini 2.0 Flash — fast multimodal inference",          contextWindow: 1000000, inputPrice: 0.10, outputPrice:  0.40, capabilities: ["text", "vision", "tool_use"] },
      { name: "gemini-2.0-flash-lite",   description: "Gemini 2.0 Flash-Lite — ultra-low cost",                contextWindow: 1000000, inputPrice: 0.075,outputPrice:  0.30, capabilities: ["text", "tool_use"] },
    ],
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    color: "bg-violet-500/10 text-violet-600 border-violet-500/20",
    models: [
      { name: "openrouter/auto",                              description: "Auto-selects the cheapest available model for each request", contextWindow: 200000, inputPrice: null,  outputPrice: null,  capabilities: ["text"] },
      { name: "openrouter/google/gemini-2.0-flash-001",      description: "Gemini 2.0 Flash via OpenRouter",                            contextWindow: 1000000,inputPrice: 0.10,  outputPrice: 0.40,  capabilities: ["text", "vision"] },
      { name: "openrouter/deepseek/deepseek-chat-v3-0324",   description: "DeepSeek v3 — strong coding and reasoning",                  contextWindow: 163840, inputPrice: 0.27,  outputPrice: 1.10,  capabilities: ["text", "tool_use"] },
      { name: "openrouter/meta-llama/llama-4-maverick",      description: "Llama 4 Maverick — Meta's frontier open model",              contextWindow: 524288, inputPrice: 0.19,  outputPrice: 0.65,  capabilities: ["text", "vision"] },
    ],
  },
  {
    id: "ollama",
    label: "Ollama (local)",
    color: "bg-gray-500/10 text-gray-600 border-gray-500/20",
    models: [
      { name: "llama3.2:3b",   description: "Llama 3.2 3B — fast local inference, great for chat",     contextWindow: 128000, inputPrice: 0, outputPrice: 0, capabilities: ["text"] },
      { name: "llama3.1:8b",   description: "Llama 3.1 8B — strong general-purpose local model",       contextWindow: 128000, inputPrice: 0, outputPrice: 0, capabilities: ["text", "tool_use"] },
      { name: "mistral:7b",    description: "Mistral 7B — fast and efficient local model",             contextWindow:  32768, inputPrice: 0, outputPrice: 0, capabilities: ["text"] },
      { name: "gemma3:12b",    description: "Gemma 3 12B — Google's open local model",                 contextWindow: 131072, inputPrice: 0, outputPrice: 0, capabilities: ["text"] },
      { name: "qwq:32b",       description: "QwQ 32B — strong reasoning local model",                  contextWindow: 131072, inputPrice: 0, outputPrice: 0, capabilities: ["text", "reasoning"] },
    ],
  },
];

function CreateModelDialog() {
  const [open, setOpen] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<ProviderCatalog | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [contextWindow, setContextWindow] = useState("");
  const [inputPrice, setInputPrice] = useState("");
  const [outputPrice, setOutputPrice] = useState("");
  const [error, setError] = useState<string | null>(null);

  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () =>
      api.models.create({
        name: name.trim(),
        provider: selectedProvider!.id,
        description: description.trim(),
        context_window: contextWindow ? parseInt(contextWindow, 10) : null,
        input_price_per_million: inputPrice ? parseFloat(inputPrice) : null,
        output_price_per_million: outputPrice ? parseFloat(outputPrice) : null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      setOpen(false);
      reset();
    },
    onError: (err: Error) => setError(err.message),
  });

  const reset = () => {
    setSelectedProvider(null);
    setName("");
    setDescription("");
    setContextWindow("");
    setInputPrice("");
    setOutputPrice("");
    setError(null);
  };

  const applyPreset = (m: CatalogModel) => {
    setName(m.name);
    setDescription(m.description);
    setContextWindow(String(m.contextWindow));
    setInputPrice(m.inputPrice !== null ? String(m.inputPrice) : "");
    setOutputPrice(m.outputPrice !== null ? String(m.outputPrice) : "");
  };

  const canSubmit = !!selectedProvider && name.trim() && !mutation.isPending;

  return (
    <Dialog open={open} onOpenChange={(val) => { setOpen(val); if (!val) reset(); }}>
      <DialogTrigger render={<Button size="sm" />}>
        <Plus className="size-3" data-icon="inline-start" />
        Add Model
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {selectedProvider && (
              <button onClick={() => { setSelectedProvider(null); setName(""); }} className="text-muted-foreground hover:text-foreground">
                <ChevronLeft className="size-4" />
              </button>
            )}
            {selectedProvider ? `${selectedProvider.label} — Select Model` : "Add Model"}
          </DialogTitle>
          <DialogDescription>
            {selectedProvider
              ? "Choose a model or type a custom name below."
              : "Select a provider to see available models."}
          </DialogDescription>
        </DialogHeader>

        {!selectedProvider ? (
          /* Step 1 — Provider grid */
          <div className="grid grid-cols-2 gap-2 py-1">
            {PROVIDER_CATALOG.map((p) => (
              <button
                key={p.id}
                onClick={() => setSelectedProvider(p)}
                className={cn(
                  "flex items-center gap-2 rounded-lg border px-3 py-2.5 text-left text-sm font-medium transition-colors hover:bg-muted/50",
                  p.color
                )}
              >
                <span className="flex-1">{p.label}</span>
                <span className="text-[10px] text-muted-foreground">{p.models.length} models</span>
              </button>
            ))}
          </div>
        ) : (
          /* Step 2 — Model selection + fields */
          <div className="grid gap-3">
            {/* Quick-pick preset buttons */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Quick select</label>
              <div className="flex flex-wrap gap-1.5">
                {selectedProvider.models.map((m) => (
                  <button
                    key={m.name}
                    onClick={() => applyPreset(m)}
                    className={cn(
                      "rounded-md border px-2 py-1 text-[10px] font-medium transition-colors hover:opacity-80",
                      name === m.name
                        ? "bg-foreground text-background border-foreground"
                        : "bg-muted/40 border-border text-muted-foreground"
                    )}
                  >
                    {m.name}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className="mb-1 block text-xs font-medium">Model name <span className="text-destructive">*</span></label>
                <Input
                  placeholder={`e.g. ${selectedProvider.models[0]?.name}`}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="h-8 text-xs"
                />
              </div>
              <div className="col-span-2">
                <label className="mb-1 block text-xs font-medium">Description</label>
                <Input
                  placeholder="Optional description..."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className="h-8 text-xs"
                />
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium">Context window</label>
                <Input
                  placeholder="e.g. 128000"
                  value={contextWindow}
                  onChange={(e) => setContextWindow(e.target.value)}
                  className="h-8 text-xs"
                  type="number"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium">Input $/M tokens</label>
                <Input
                  placeholder={selectedProvider.id === "ollama" ? "0 (free)" : "e.g. 3.00"}
                  value={inputPrice}
                  onChange={(e) => setInputPrice(e.target.value)}
                  className="h-8 text-xs"
                  type="number"
                  step="0.001"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium">Output $/M tokens</label>
                <Input
                  placeholder={selectedProvider.id === "ollama" ? "0 (free)" : "e.g. 15.00"}
                  value={outputPrice}
                  onChange={(e) => setOutputPrice(e.target.value)}
                  className="h-8 text-xs"
                  type="number"
                  step="0.001"
                />
              </div>
            </div>

            {error && (
              <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>
            )}
          </div>
        )}

        <DialogFooter>
          <DialogClose render={<Button variant="outline" size="sm" />}>
            Cancel
          </DialogClose>
          {selectedProvider && (
            <Button size="sm" onClick={() => mutation.mutate()} disabled={!canSubmit}>
              {mutation.isPending ? "Saving..." : "Add Model"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function ModelsPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const canSync = !!user && (user.role === "deployer" || user.role === "admin");
  const [search, setSearch] = useState("");
  const [providerFilter, setProviderFilter] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [showFavoritesOnly, setShowFavoritesOnly] = useState(false);
  const [visibleColumns, setVisibleColumns] = useState<Set<string>>(DEFAULT_MODEL_COLUMNS);
  const { showOnlyFavorites, favorites } = useFavorites();

  // Track G — model lifecycle sync.
  const syncMutation = useMutation({
    mutationFn: () => api.models.sync([]),
    onSuccess: (resp) => {
      const totals = resp.data?.totals ?? { added: 0, deprecated: 0, retired: 0 };
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["providers", "catalog", "status"] });
      toast({
        variant: "success",
        title: "Model sync complete",
        description: `+${totals.added} added · ~${totals.deprecated} deprecated · -${totals.retired} retired`,
      });
    },
    onError: (err: Error) => {
      const msg = err.message ?? "Sync failed";
      if (/401|403|unauthorized|forbidden/i.test(msg)) {
        toast({
          variant: "error",
          title: "Permission denied",
          description: "Model sync requires the deployer role.",
        });
      } else {
        toast({ variant: "error", title: "Sync failed", description: msg });
      }
    },
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ["models", { providerFilter }],
    queryFn: () =>
      api.models.list({ provider: providerFilter || undefined }),
    staleTime: 10_000,
  });

  const models = data?.data ?? [];
  const total = data?.meta.total ?? 0;

  let filtered = search
    ? models.filter(
        (m) =>
          m.name.toLowerCase().includes(search.toLowerCase()) ||
          m.description.toLowerCase().includes(search.toLowerCase()) ||
          m.provider.toLowerCase().includes(search.toLowerCase())
      )
    : models;

  if (showFavoritesOnly) {
    filtered = showOnlyFavorites(filtered);
  }

  // Sortable
  const { sortedData, sortKey, sortDirection, toggleSort } = useSortable(
    filtered as unknown as Record<string, unknown>[],
    "name",
    "asc"
  );
  const sortedModels = sortedData as unknown as Model[];

  function toggleSelect(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        if (next.size >= 3) return prev;
        next.add(id);
      }
      return next;
    });
  }

  function handleCompare() {
    const ids = Array.from(selectedIds).join(",");
    navigate(`/models/compare?ids=${ids}`);
  }

  const canCompare = selectedIds.size >= 2 && selectedIds.size <= 3;
  const hasFilter = !!(search || providerFilter || showFavoritesOnly);

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Models</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {total} model{total !== 1 ? "s" : ""} in registry
          </p>
        </div>
        <div className="flex items-center gap-2">
          {selectedIds.size > 0 && (
            <button
              onClick={handleCompare}
              disabled={!canCompare}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                canCompare
                  ? "bg-foreground text-background hover:bg-foreground/90"
                  : "bg-muted text-muted-foreground cursor-not-allowed"
              )}
            >
              <GitCompareArrows className="size-3" />
              Compare ({selectedIds.size})
            </button>
          )}
          <ColumnToggle
            columns={MODEL_COLUMNS}
            visibleKeys={visibleColumns}
            onChange={setVisibleColumns}
          />
          <ExportDropdown
            data={filtered as unknown as Record<string, unknown>[]}
            filename="models"
          />
          <CreateModelDialog />
        </div>
      </div>

      {/* Provider source tabs (issues #175 + #164).
          - Direct providers: built-in OpenAI-compatible catalog (Track F / #160).
          - Gateways: LiteLLM + OpenRouter (Track H / #164) — same configure
            flow as Direct, plus 3-segment model refs `<gateway>/<upstream>/<model>`.
          - Local: Ollama / vLLM / etc. — content lands later. */}
      <div className="mb-4">
        <Tabs defaultValue="direct" className="gap-3">
          <div className="flex items-center justify-between">
            <TabsList variant="line">
              <TabsTrigger value="direct" className="text-xs">
                Direct providers
              </TabsTrigger>
              <TabsTrigger value="gateways" className="text-xs">
                Gateways
              </TabsTrigger>
              <TabsTrigger
                value="local"
                className="text-xs"
                disabled
                title="Coming soon — local model runtimes"
              >
                Local
              </TabsTrigger>
            </TabsList>
            <Button
              size="sm"
              variant="outline"
              disabled={!canSync || syncMutation.isPending}
              onClick={() => canSync && syncMutation.mutate()}
              title={
                !canSync
                  ? "Requires deployer role"
                  : "Sync models from configured providers"
              }
              className={cn("h-7 text-xs", !canSync && "opacity-50")}
              data-testid="models-sync-btn"
            >
              <RefreshCcw
                className={cn(
                  "mr-1 size-3",
                  syncMutation.isPending && "animate-spin",
                )}
              />
              {syncMutation.isPending ? "Syncing..." : "Sync"}
            </Button>
          </div>
          <TabsContent value="direct">
            <ProviderCatalog filter="openai_compatible" />
          </TabsContent>
          <TabsContent value="gateways">
            <ProviderCatalog
              filter="gateway"
              heading="Model Gateways"
              hint="Reference gateway models in agent.yaml as <gateway>/<upstream>/<model>"
            />
          </TabsContent>
          <TabsContent value="local">
            <div className="rounded-lg border border-border bg-muted/20 p-4 text-xs text-muted-foreground">
              Local model runtime support coming soon.
            </div>
          </TabsContent>
        </Tabs>
      </div>

      {/* Provider filter tabs */}
      <div className="mb-3 flex items-center gap-1.5">
        {PROVIDER_TABS.map((tab) => {
          const value = tab === "All" ? "" : tab.toLowerCase();
          const isActive = providerFilter === value;
          return (
            <button
              key={tab}
              onClick={() => setProviderFilter(value)}
              className={cn(
                "rounded-full px-3 py-1 text-xs font-medium transition-colors",
                isActive
                  ? "bg-foreground text-background"
                  : "bg-muted/50 text-muted-foreground hover:bg-muted hover:text-foreground"
              )}
            >
              {tab}
            </button>
          );
        })}
      </div>

      {/* Search + favorites filter */}
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
          {favorites.size > 0 && (
            <span className="text-[10px]">({favorites.size})</span>
          )}
        </button>
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-lg border border-border">
        <div className="flex items-center gap-4 border-b border-border bg-muted/30 px-5 py-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          <span className="w-3.5" />
          <span className="w-3.5" />
          <span className="w-1.5" />
          <span className="flex-1">
            <SortableColumnHeader
              sortKey="name"
              currentSortKey={sortKey}
              currentDirection={sortDirection}
              onSort={toggleSort}
            >
              Model
            </SortableColumnHeader>
          </span>
          {visibleColumns.has("provider") && (
            <span className="w-24 text-center">
              <SortableColumnHeader
                sortKey="provider"
                currentSortKey={sortKey}
                currentDirection={sortDirection}
                onSort={toggleSort}
              >
                Provider
              </SortableColumnHeader>
            </span>
          )}
          {visibleColumns.has("context_window") && (
            <span className="w-16 text-right">
              <SortableColumnHeader
                sortKey="context_window"
                currentSortKey={sortKey}
                currentDirection={sortDirection}
                onSort={toggleSort}
              >
                Context
              </SortableColumnHeader>
            </span>
          )}
          {visibleColumns.has("price") && (
            <span className="w-28 text-right">
              <SortableColumnHeader
                sortKey="input_price_per_million"
                currentSortKey={sortKey}
                currentDirection={sortDirection}
                onSort={toggleSort}
              >
                Price (in/out)
              </SortableColumnHeader>
            </span>
          )}
          {visibleColumns.has("source") && (
            <span className="w-20 text-right">
              <SortableColumnHeader
                sortKey="source"
                currentSortKey={sortKey}
                currentDirection={sortDirection}
                onSort={toggleSort}
              >
                Source
              </SortableColumnHeader>
            </span>
          )}
        </div>

        {isLoading ? (
          <SkeletonTableRows rows={5} columns={4} />
        ) : error ? (
          <div className="px-6 py-12 text-center text-sm text-destructive">
            Failed to load models: {(error as Error).message}
          </div>
        ) : sortedModels.length === 0 ? (
          <EmptyState
            icon={Cpu}
            title={hasFilter ? "No models match your filters" : "No models registered"}
            description={
              hasFilter
                ? "Try adjusting your search or filters."
                : "Connect a LiteLLM gateway or register models manually to see them here."
            }
          />
        ) : (
          sortedModels.map((model) => (
            <ModelRow
              key={model.id}
              model={model}
              isSelected={selectedIds.has(model.id)}
              onToggleSelect={() => toggleSelect(model.id)}
              onClick={() => navigate(`/models/${model.id}`)}
              visibleColumns={visibleColumns}
            />
          ))
        )}
      </div>
    </div>
  );
}
