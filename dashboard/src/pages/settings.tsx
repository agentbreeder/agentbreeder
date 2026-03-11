import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Settings,
  Plus,
  Circle,
  ChevronDown,
  ChevronUp,
  Trash2,
  RefreshCw,
  Loader2,
  Eye,
  EyeOff,
  CheckCircle2,
  XCircle,
  ArrowRight,
} from "lucide-react";
import {
  api,
  type Provider,
  type ProviderType,
  type ProviderTestResult,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { useState } from "react";

// --- Provider type metadata ---

interface ProviderMeta {
  type: ProviderType;
  label: string;
  description: string;
  color: string;
  defaultUrl: string;
  requiresKey: boolean;
}

const PROVIDER_TYPES: ProviderMeta[] = [
  {
    type: "openai",
    label: "OpenAI",
    description: "GPT-4o, o1, o3 models",
    color: "text-emerald-600 dark:text-emerald-400",
    defaultUrl: "https://api.openai.com/v1",
    requiresKey: true,
  },
  {
    type: "anthropic",
    label: "Anthropic",
    description: "Claude Opus, Sonnet, Haiku",
    color: "text-orange-600 dark:text-orange-400",
    defaultUrl: "https://api.anthropic.com",
    requiresKey: true,
  },
  {
    type: "google",
    label: "Google AI",
    description: "Gemini 2.0, 1.5 models",
    color: "text-blue-600 dark:text-blue-400",
    defaultUrl: "https://generativelanguage.googleapis.com",
    requiresKey: true,
  },
  {
    type: "ollama",
    label: "Ollama",
    description: "Local models (Llama, Mistral)",
    color: "text-violet-600 dark:text-violet-400",
    defaultUrl: "http://localhost:11434",
    requiresKey: false,
  },
  {
    type: "litellm",
    label: "LiteLLM",
    description: "Unified proxy gateway",
    color: "text-cyan-600 dark:text-cyan-400",
    defaultUrl: "http://localhost:4000",
    requiresKey: false,
  },
  {
    type: "openrouter",
    label: "OpenRouter",
    description: "Multi-provider routing",
    color: "text-rose-600 dark:text-rose-400",
    defaultUrl: "https://openrouter.ai/api/v1",
    requiresKey: true,
  },
];

const PROVIDER_COLORS: Record<string, string> = {
  openai: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  anthropic: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  google: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  ollama: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  litellm: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border-cyan-500/20",
  openrouter: "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20",
};

function statusColor(provider: Provider): string {
  if (provider.status === "disabled") return "text-muted-foreground";
  if (provider.status === "error") return "text-red-500";
  if (provider.latency_ms && provider.latency_ms > 500) return "text-yellow-500";
  return "text-emerald-500";
}

function statusLabel(provider: Provider): string {
  if (provider.status === "disabled") return "Disabled";
  if (provider.status === "error") return "Error";
  if (provider.latency_ms && provider.latency_ms > 500) return "Slow";
  return "Healthy";
}

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return "Never";
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

// --- Provider Card ---

function ProviderCard({ provider }: { provider: Provider }) {
  const [expanded, setExpanded] = useState(false);
  const queryClient = useQueryClient();

  const testMutation = useMutation({
    mutationFn: () => api.providers.test(provider.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["providers"] }),
  });

  const discoverMutation = useMutation({
    mutationFn: () => api.providers.discover(provider.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["providers"] }),
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.providers.delete(provider.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["providers"] }),
  });

  const toggleMutation = useMutation({
    mutationFn: () =>
      api.providers.update(provider.id, {
        status: provider.status === "disabled" ? "active" : "disabled",
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["providers"] }),
  });

  const isOllama = provider.provider_type === "ollama";

  return (
    <div className="overflow-hidden rounded-lg border border-border transition-colors hover:border-border/80">
      {/* Main card row */}
      <div className="flex items-center gap-4 px-5 py-4">
        <Circle
          className={cn("size-2 shrink-0 fill-current", statusColor(provider))}
        />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{provider.name}</span>
            <Badge
              variant="outline"
              className={cn(
                "text-[10px] font-medium",
                PROVIDER_COLORS[provider.provider_type] ??
                  "bg-muted text-muted-foreground border-border"
              )}
            >
              {provider.provider_type}
            </Badge>
            {isOllama && (
              <Badge
                variant="outline"
                className="text-[10px] font-medium bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20"
              >
                Local
              </Badge>
            )}
          </div>
          <div className="mt-0.5 flex items-center gap-3 text-[11px] text-muted-foreground">
            <span>{statusLabel(provider)}</span>
            <span>{provider.model_count} models</span>
            {provider.latency_ms != null && (
              <span>{provider.latency_ms}ms</span>
            )}
            <span>Verified {timeAgo(provider.last_verified)}</span>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => testMutation.mutate()}
            disabled={testMutation.isPending}
            title="Test connection"
          >
            {testMutation.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <RefreshCw className="size-3" />
            )}
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => setExpanded(!expanded)}
            title={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? (
              <ChevronUp className="size-3" />
            ) : (
              <ChevronDown className="size-3" />
            )}
          </Button>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-border bg-muted/20 px-5 py-4">
          <div className="grid grid-cols-2 gap-4 text-xs">
            <div>
              <span className="text-muted-foreground">Base URL</span>
              <p className="mt-0.5 font-mono text-[11px]">
                {provider.base_url ?? "Default"}
              </p>
            </div>
            <div>
              <span className="text-muted-foreground">Status</span>
              <p className="mt-0.5">{provider.status}</p>
            </div>
            <div>
              <span className="text-muted-foreground">Models discovered</span>
              <p className="mt-0.5">{provider.model_count}</p>
            </div>
            <div>
              <span className="text-muted-foreground">Latency</span>
              <p className="mt-0.5">
                {provider.latency_ms != null
                  ? `${provider.latency_ms}ms`
                  : "Not tested"}
              </p>
            </div>
          </div>

          {/* Discovered models */}
          {discoverMutation.data && (
            <div className="mt-4">
              <span className="text-xs text-muted-foreground">
                Discovered models ({discoverMutation.data.data.total})
              </span>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {discoverMutation.data.data.models.map((m) => (
                  <Badge
                    key={m}
                    variant="outline"
                    className="text-[10px] font-mono"
                  >
                    {m}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="mt-4 flex items-center gap-2">
            <Button
              variant="outline"
              size="xs"
              onClick={() => discoverMutation.mutate()}
              disabled={discoverMutation.isPending}
            >
              {discoverMutation.isPending ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <RefreshCw className="size-3" />
              )}
              Discover models
            </Button>
            <Button
              variant="outline"
              size="xs"
              onClick={() => toggleMutation.mutate()}
              disabled={toggleMutation.isPending}
            >
              {provider.status === "disabled" ? "Enable" : "Disable"}
            </Button>
            {isOllama && (
              <Button variant="outline" size="xs" disabled title="Coming soon">
                Pull Model
              </Button>
            )}
            <div className="flex-1" />
            <Button
              variant="destructive"
              size="xs"
              onClick={() => {
                if (confirm(`Remove provider "${provider.name}"?`)) {
                  deleteMutation.mutate();
                }
              }}
              disabled={deleteMutation.isPending}
            >
              <Trash2 className="size-3" />
              Remove
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// --- Add Provider Dialog ---

function AddProviderDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [selectedType, setSelectedType] = useState<ProviderMeta | null>(null);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const [createdId, setCreatedId] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: (body: {
      name: string;
      provider_type: ProviderType;
      base_url?: string;
      config?: Record<string, unknown>;
    }) => api.providers.create(body),
    onSuccess: (data) => {
      setCreatedId(data.data.id);
      queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => api.providers.test(id),
    onSuccess: (data) => {
      setTestResult(data.data);
      setStep(3);
    },
  });

  const reset = () => {
    setStep(1);
    setSelectedType(null);
    setName("");
    setBaseUrl("");
    setApiKey("");
    setShowKey(false);
    setTestResult(null);
    setCreatedId(null);
  };

  const handleOpenChange = (v: boolean) => {
    if (!v) reset();
    onOpenChange(v);
  };

  const handleSelectType = (meta: ProviderMeta) => {
    setSelectedType(meta);
    setName(meta.label);
    setBaseUrl(meta.defaultUrl);
    setStep(2);
  };

  const handleConfigure = async () => {
    if (!selectedType) return;

    const config: Record<string, unknown> = {};
    if (apiKey) config.api_key_env = `${selectedType.type.toUpperCase()}_API_KEY`;

    const result = await createMutation.mutateAsync({
      name,
      provider_type: selectedType.type,
      base_url: baseUrl || undefined,
      config: Object.keys(config).length > 0 ? config : undefined,
    });

    // Auto-test after creation
    testMutation.mutate(result.data.id);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {step === 1 && "Add Provider"}
            {step === 2 && `Configure ${selectedType?.label}`}
            {step === 3 && "Connection Test"}
          </DialogTitle>
          <DialogDescription>
            {step === 1 && "Choose an LLM provider to connect"}
            {step === 2 && "Set up the connection details"}
            {step === 3 && "Verifying provider connectivity"}
          </DialogDescription>
        </DialogHeader>

        {/* Step 1: Pick provider type */}
        {step === 1 && (
          <div className="grid grid-cols-2 gap-2">
            {PROVIDER_TYPES.map((meta) => (
              <button
                key={meta.type}
                onClick={() => handleSelectType(meta)}
                className="flex flex-col gap-1 rounded-lg border border-border p-3 text-left transition-colors hover:border-foreground/20 hover:bg-muted/50"
              >
                <span className={cn("text-sm font-medium", meta.color)}>
                  {meta.label}
                </span>
                <span className="text-[11px] text-muted-foreground">
                  {meta.description}
                </span>
              </button>
            ))}
          </div>
        )}

        {/* Step 2: Configure connection */}
        {step === 2 && selectedType && (
          <div className="space-y-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium">Name</label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Provider display name"
                className="h-8 text-xs"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium">
                Base URL
              </label>
              <Input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={selectedType.defaultUrl}
                className="h-8 font-mono text-xs"
              />
            </div>
            {selectedType.requiresKey && (
              <div>
                <label className="mb-1.5 block text-xs font-medium">
                  API Key
                </label>
                <p className="mb-1.5 text-[11px] text-muted-foreground">
                  Set via environment variable{" "}
                  <code className="rounded bg-muted px-1 py-0.5 font-mono text-[10px]">
                    {selectedType.type.toUpperCase()}_API_KEY
                  </code>
                  . Not stored in database.
                </p>
                <div className="relative">
                  <Input
                    type={showKey ? "text" : "password"}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="sk-... (optional, for test only)"
                    className="h-8 pr-8 font-mono text-xs"
                  />
                  <button
                    type="button"
                    onClick={() => setShowKey(!showKey)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  >
                    {showKey ? (
                      <EyeOff className="size-3.5" />
                    ) : (
                      <Eye className="size-3.5" />
                    )}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Step 3: Test result */}
        {step === 3 && (
          <div className="flex flex-col items-center gap-3 py-4">
            {testMutation.isPending ? (
              <>
                <Loader2 className="size-8 animate-spin text-muted-foreground" />
                <p className="text-sm text-muted-foreground">
                  Testing connection...
                </p>
              </>
            ) : testResult?.success ? (
              <>
                <CheckCircle2 className="size-8 text-emerald-500" />
                <p className="text-sm font-medium">Connection successful</p>
                <div className="flex items-center gap-4 text-xs text-muted-foreground">
                  {testResult.model_count != null && (
                    <span>{testResult.model_count} models discovered</span>
                  )}
                  {testResult.latency_ms != null && (
                    <span>{testResult.latency_ms}ms latency</span>
                  )}
                </div>
              </>
            ) : (
              <>
                <XCircle className="size-8 text-red-500" />
                <p className="text-sm font-medium">Connection failed</p>
                {testResult?.error && (
                  <p className="text-xs text-muted-foreground">
                    {testResult.error}
                  </p>
                )}
              </>
            )}
          </div>
        )}

        {/* Footer */}
        {step === 2 && (
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setStep(1)}>
              Back
            </Button>
            <Button
              size="sm"
              onClick={handleConfigure}
              disabled={!name.trim() || createMutation.isPending}
            >
              {createMutation.isPending ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <ArrowRight className="size-3" />
              )}
              Test Connection
            </Button>
          </DialogFooter>
        )}
        {step === 3 && (
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={() => handleOpenChange(false)}
            >
              Done
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

// --- Empty State ---

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-border">
        <Settings className="size-5 text-muted-foreground" />
      </div>
      <h3 className="text-sm font-medium">No providers configured</h3>
      <p className="mt-1 max-w-xs text-xs text-muted-foreground">
        Add an LLM provider to start deploying agents. Connect OpenAI,
        Anthropic, or a local Ollama instance.
      </p>
      <Button variant="outline" size="sm" className="mt-4" onClick={onAdd}>
        <Plus className="size-3.5" />
        Add Provider
      </Button>
    </div>
  );
}

// --- Settings Page ---

export default function SettingsPage() {
  const [dialogOpen, setDialogOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["providers"],
    queryFn: () => api.providers.list(),
    staleTime: 10_000,
  });

  const providers = data?.data ?? [];
  const total = data?.meta.total ?? 0;

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6">
        <h1 className="text-lg font-semibold tracking-tight">Settings</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Manage platform configuration
        </p>
      </div>

      <Tabs defaultValue="providers">
        <TabsList>
          <TabsTrigger value="providers">Providers</TabsTrigger>
        </TabsList>

        <TabsContent value="providers">
          {/* Header */}
          <div className="mb-4 flex items-center justify-between">
            <div>
              <p className="text-xs text-muted-foreground">
                {total} provider{total !== 1 ? "s" : ""} configured
              </p>
            </div>
            {providers.length > 0 && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setDialogOpen(true)}
              >
                <Plus className="size-3.5" />
                Add Provider
              </Button>
            )}
          </div>

          {/* Provider list */}
          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <div
                  key={i}
                  className="flex items-center gap-4 rounded-lg border border-border px-5 py-4"
                >
                  <div className="size-2 animate-pulse rounded-full bg-muted" />
                  <div className="flex-1 space-y-1.5">
                    <div className="h-3.5 w-32 animate-pulse rounded bg-muted" />
                    <div className="h-2.5 w-48 animate-pulse rounded bg-muted/60" />
                  </div>
                  <div className="h-5 w-16 animate-pulse rounded-full bg-muted" />
                </div>
              ))}
            </div>
          ) : error ? (
            <div className="rounded-lg border border-border px-6 py-12 text-center text-sm text-destructive">
              Failed to load providers: {(error as Error).message}
            </div>
          ) : providers.length === 0 ? (
            <EmptyState onAdd={() => setDialogOpen(true)} />
          ) : (
            <div className="space-y-3">
              {providers.map((provider) => (
                <ProviderCard key={provider.id} provider={provider} />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      <AddProviderDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}
