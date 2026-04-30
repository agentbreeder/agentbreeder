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
  Pencil,
  Power,
  Plug,
  Zap,
  Globe,
  Server,
  Bot,
  Sparkles,
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
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { cn } from "@/lib/utils";
import { useState, useCallback } from "react";
import { useToast } from "@/hooks/use-toast";

// --- Provider type metadata ---

interface ProviderMeta {
  type: ProviderType;
  label: string;
  description: string;
  color: string;
  icon: React.ComponentType<{ className?: string }>;
  defaultUrl: string;
  requiresKey: boolean;
  available: boolean;
}

const PROVIDER_TYPES: ProviderMeta[] = [
  {
    type: "openai",
    label: "OpenAI",
    description: "GPT-4o, o1, o3 models",
    color: "text-emerald-600 dark:text-emerald-400",
    icon: Sparkles,
    defaultUrl: "https://api.openai.com/v1",
    requiresKey: true,
    available: true,
  },
  {
    type: "anthropic",
    label: "Anthropic",
    description: "Claude Opus, Sonnet, Haiku",
    color: "text-orange-600 dark:text-orange-400",
    icon: Bot,
    defaultUrl: "https://api.anthropic.com",
    requiresKey: true,
    available: true,
  },
  {
    type: "google",
    label: "Google AI",
    description: "Gemini 2.0, 1.5 models",
    color: "text-blue-600 dark:text-blue-400",
    icon: Globe,
    defaultUrl: "https://generativelanguage.googleapis.com",
    requiresKey: true,
    available: true,
  },
  {
    type: "ollama",
    label: "Ollama",
    description: "Local models (Llama, Mistral)",
    color: "text-violet-600 dark:text-violet-400",
    icon: Server,
    defaultUrl: "http://localhost:11434",
    requiresKey: false,
    available: true,
  },
  {
    type: "litellm",
    label: "LiteLLM",
    description: "Unified proxy gateway",
    color: "text-cyan-600 dark:text-cyan-400",
    icon: Plug,
    defaultUrl: "http://localhost:4000",
    requiresKey: false,
    available: true,
  },
  {
    type: "openrouter",
    label: "OpenRouter",
    description: "Multi-provider routing",
    color: "text-rose-600 dark:text-rose-400",
    icon: Zap,
    defaultUrl: "https://openrouter.ai/api/v1",
    requiresKey: true,
    available: true,
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

function getProviderMeta(type: ProviderType): ProviderMeta | undefined {
  return PROVIDER_TYPES.find((p) => p.type === type);
}

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

function maskApiKey(key: string | undefined | null): string {
  if (!key) return "Not set";
  if (key.length <= 8) return "****";
  return `${key.slice(0, 4)}...${key.slice(-4)}`;
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

// --- Discovered model type ---

interface DiscoveredModel {
  id: string;
  name: string;
  context_window?: number;
  max_output?: number;
  input_price?: number;
  output_price?: number;
}

// --- Ollama Pull Model Dialog (#214) ---

interface PullEvent {
  status?: string;
  digest?: string;
  total?: number;
  completed?: number;
  message?: string;
}

const POPULAR_OLLAMA_MODELS = [
  "llama3.2",
  "llama3.1",
  "mistral",
  "mixtral",
  "qwen2.5",
  "phi3",
  "gemma2",
  "codellama",
];

function PullModelDialog({ providerId }: { providerId: string }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [model, setModel] = useState("");
  const [events, setEvents] = useState<PullEvent[]>([]);
  const [pulling, setPulling] = useState(false);

  const lastEvent = events[events.length - 1];
  const percent =
    lastEvent?.total && lastEvent?.completed
      ? Math.round((lastEvent.completed / lastEvent.total) * 100)
      : null;
  const finalSuccess = lastEvent?.status === "success";
  const finalError = lastEvent?.status === "error";

  async function startPull() {
    if (!model.trim()) {
      toast({ title: "Enter a model name", variant: "error" });
      return;
    }
    setEvents([]);
    setPulling(true);
    try {
      const resp = await api.providers.pullModel(providerId, model.trim());
      if (!resp.ok || !resp.body) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Pull failed (${resp.status})`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE events are separated by double newlines; each line begins
        // with `data: ` followed by JSON.
        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          try {
            const evt: PullEvent = JSON.parse(line.slice(6));
            setEvents((prev) => [...prev, evt]);
            if (evt.status === "success") {
              toast({ title: `Pulled ${model.trim()}`, variant: "success" });
              queryClient.invalidateQueries({ queryKey: ["models"] });
            } else if (evt.status === "error") {
              toast({
                title: "Pull failed",
                description: evt.message,
                variant: "error",
              });
            }
          } catch {
            // ignore unparsable lines
          }
        }
      }
    } catch (err) {
      toast({ title: "Pull failed", description: (err as Error).message, variant: "error" });
    } finally {
      setPulling(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !pulling && setOpen(o)}>
      <Button
        variant="outline"
        size="xs"
        onClick={() => {
          setEvents([]);
          setOpen(true);
        }}
      >
        Pull Model
      </Button>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Pull Ollama Model</DialogTitle>
          <DialogDescription>
            Streams progress from <code className="font-mono">ollama pull</code> on this provider.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-muted-foreground">Model name</label>
            <Input
              value={model}
              disabled={pulling}
              placeholder="llama3.2"
              onChange={(e) => setModel(e.target.value)}
            />
          </div>
          <div className="flex flex-wrap gap-1">
            {POPULAR_OLLAMA_MODELS.map((m) => (
              <button
                key={m}
                onClick={() => setModel(m)}
                disabled={pulling}
                className="rounded bg-muted px-2 py-0.5 text-[11px] hover:bg-muted/70 disabled:opacity-50"
              >
                {m}
              </button>
            ))}
          </div>

          {events.length > 0 && (
            <div className="rounded-md border border-border bg-muted/30 p-3 text-xs">
              {percent !== null && !finalSuccess && !finalError && (
                <div className="mb-2 h-2 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full bg-primary transition-all"
                    style={{ width: `${percent}%` }}
                  />
                </div>
              )}
              <div className="font-mono">
                {finalSuccess
                  ? "✓ Pull complete."
                  : finalError
                    ? `✗ ${lastEvent?.message ?? "error"}`
                    : `${lastEvent?.status ?? "starting…"}${
                        percent !== null ? ` — ${percent}%` : ""
                      }`}
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => setOpen(false)}
            disabled={pulling}
          >
            {finalSuccess || finalError ? "Close" : "Cancel"}
          </Button>
          <Button onClick={startPull} disabled={pulling || !model.trim()}>
            {pulling ? "Pulling…" : "Pull"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}


// --- Provider Card ---

function ProviderCard({
  provider,
  onEdit,
}: {
  provider: Provider;
  onEdit: (provider: Provider) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const testMutation = useMutation({
    mutationFn: () => api.providers.test(provider.id),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["providers"] });
      if (data.data.success) {
        toast({
          title: "Connection healthy",
          description: `${provider.name}: ${data.data.latency_ms}ms latency`,
          variant: "success",
        });
      } else {
        toast({
          title: "Connection failed",
          description: data.data.error ?? "Unknown error",
          variant: "error",
        });
      }
    },
    onError: (err: Error) => {
      toast({ title: "Test failed", description: err.message, variant: "error" });
    },
  });

  const discoverMutation = useMutation({
    mutationFn: () => api.providers.discover(provider.id),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["providers"] });
      toast({
        title: "Models discovered",
        description: `Found ${data.data.total} models from ${provider.name}`,
        variant: "success",
      });
    },
    onError: (err: Error) => {
      toast({ title: "Discovery failed", description: err.message, variant: "error" });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.providers.delete(provider.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["providers"] });
      toast({
        title: "Provider removed",
        description: `${provider.name} has been removed`,
        variant: "success",
      });
    },
    onError: (err: Error) => {
      toast({ title: "Delete failed", description: err.message, variant: "error" });
    },
  });

  const toggleMutation = useMutation({
    mutationFn: () =>
      api.providers.update(provider.id, {
        status: provider.status === "disabled" ? "active" : "disabled",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["providers"] });
      const newStatus = provider.status === "disabled" ? "enabled" : "disabled";
      toast({
        title: `Provider ${newStatus}`,
        description: `${provider.name} is now ${newStatus}`,
        variant: "info",
      });
    },
    onError: (err: Error) => {
      toast({ title: "Update failed", description: err.message, variant: "error" });
    },
  });

  const isOllama = provider.provider_type === "ollama";
  const apiKeyEnv = provider.config?.api_key_env as string | undefined;

  return (
    <>
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
              {apiKeyEnv && (
                <span className="font-mono">{maskApiKey(apiKeyEnv)}</span>
              )}
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
              onClick={() => onEdit(provider)}
              title="Edit provider"
            >
              <Pencil className="size-3" />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => toggleMutation.mutate()}
              disabled={toggleMutation.isPending}
              title={provider.status === "disabled" ? "Enable" : "Disable"}
            >
              {toggleMutation.isPending ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Power className="size-3" />
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
                <span className="text-muted-foreground">Models registered</span>
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
              {apiKeyEnv && (
                <div>
                  <span className="text-muted-foreground">API Key (env)</span>
                  <p className="mt-0.5 font-mono text-[11px]">
                    {apiKeyEnv}
                  </p>
                </div>
              )}
              <div>
                <span className="text-muted-foreground">Last verified</span>
                <p className="mt-0.5">{timeAgo(provider.last_verified)}</p>
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
                <Power className="size-3" />
                {provider.status === "disabled" ? "Enable" : "Disable"}
              </Button>
              {isOllama && <PullModelDialog providerId={provider.id} />}
              <div className="flex-1" />
              <Button
                variant="destructive"
                size="xs"
                onClick={() => setDeleteOpen(true)}
                disabled={deleteMutation.isPending}
              >
                <Trash2 className="size-3" />
                Remove
              </Button>
            </div>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={`Remove ${provider.name}?`}
        description="Are you sure? This will remove the provider and all associated models. This action cannot be undone."
        confirmLabel="Remove"
        variant="danger"
        onConfirm={() => deleteMutation.mutate()}
      />
    </>
  );
}

// --- Edit Provider Dialog ---

function EditProviderDialog({
  provider,
  open,
  onOpenChange,
}: {
  provider: Provider | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);

  const meta = provider ? getProviderMeta(provider.provider_type) : null;

  const populateFields = useCallback(
    (p: Provider) => {
      setName(p.name);
      setBaseUrl(p.base_url ?? meta?.defaultUrl ?? "");
      setApiKey("");
      setShowKey(false);
      setTestResult(null);
    },
    [meta]
  );

  // Populate fields when provider changes
  const [lastProviderId, setLastProviderId] = useState<string | null>(null);
  if (provider && provider.id !== lastProviderId) {
    setLastProviderId(provider.id);
    populateFields(provider);
  }

  const updateMutation = useMutation({
    mutationFn: (body: {
      name?: string;
      base_url?: string;
      config?: Record<string, unknown>;
    }) => api.providers.update(provider!.id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["providers"] });
      toast({
        title: "Provider updated",
        description: `${name} configuration saved`,
        variant: "success",
      });
      onOpenChange(false);
    },
    onError: (err: Error) => {
      toast({ title: "Update failed", description: err.message, variant: "error" });
    },
  });

  const testMutation = useMutation({
    mutationFn: () => api.providers.test(provider!.id),
    onSuccess: (data) => {
      setTestResult(data.data);
      queryClient.invalidateQueries({ queryKey: ["providers"] });
      if (data.data.success) {
        toast({
          title: "Connection healthy",
          description: `${data.data.latency_ms}ms latency`,
          variant: "success",
        });
      } else {
        toast({
          title: "Connection failed",
          description: data.data.error ?? "Unknown error",
          variant: "error",
        });
      }
    },
    onError: (err: Error) => {
      toast({ title: "Test failed", description: err.message, variant: "error" });
    },
  });

  const handleSave = () => {
    if (!provider) return;
    const body: {
      name?: string;
      base_url?: string;
      config?: Record<string, unknown>;
    } = {};

    if (name !== provider.name) body.name = name;
    if (baseUrl !== (provider.base_url ?? "")) body.base_url = baseUrl || undefined;
    if (apiKey) {
      body.config = {
        ...provider.config,
        api_key_env: `${provider.provider_type.toUpperCase()}_API_KEY`,
      };
    }

    updateMutation.mutate(body);
  };

  if (!provider || !meta) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit {provider.name}</DialogTitle>
          <DialogDescription>
            Update connection details for this {meta.label} provider
          </DialogDescription>
        </DialogHeader>

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
            <label className="mb-1.5 block text-xs font-medium">Base URL</label>
            <Input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={meta.defaultUrl}
              className="h-8 font-mono text-xs"
            />
          </div>
          {meta.requiresKey && (
            <div>
              <label className="mb-1.5 block text-xs font-medium">API Key</label>
              <p className="mb-1.5 text-[11px] text-muted-foreground">
                Set via environment variable{" "}
                <code className="rounded bg-muted px-1 py-0.5 font-mono text-[10px]">
                  {provider.provider_type.toUpperCase()}_API_KEY
                </code>
                . Leave blank to keep current value.
              </p>
              <div className="relative">
                <Input
                  type={showKey ? "text" : "password"}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="sk-... (leave blank to keep current)"
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

          {/* Test result inline */}
          {testResult && (
            <div
              className={cn(
                "flex items-center gap-2 rounded-lg border px-3 py-2 text-xs",
                testResult.success
                  ? "border-emerald-500/30 bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
                  : "border-red-500/30 bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200"
              )}
            >
              {testResult.success ? (
                <>
                  <CheckCircle2 className="size-3.5 shrink-0" />
                  <span>
                    Connected ({testResult.latency_ms}ms,{" "}
                    {testResult.model_count} models)
                  </span>
                </>
              ) : (
                <>
                  <XCircle className="size-3.5 shrink-0" />
                  <span>{testResult.error ?? "Connection failed"}</span>
                </>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => testMutation.mutate()}
            disabled={testMutation.isPending}
          >
            {testMutation.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <RefreshCw className="size-3" />
            )}
            Test Connection
          </Button>
          <Button
            size="sm"
            onClick={handleSave}
            disabled={!name.trim() || updateMutation.isPending}
          >
            {updateMutation.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : null}
            Save Changes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// --- Add Provider Wizard (4-step) ---

function AddProviderDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [selectedType, setSelectedType] = useState<ProviderMeta | null>(null);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [discoveredModels, setDiscoveredModels] = useState<DiscoveredModel[]>([]);
  const [selectedModels, setSelectedModels] = useState<Set<string>>(new Set());
  const [registeredCount, setRegisteredCount] = useState(0);

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
    onError: (err: Error) => {
      toast({ title: "Creation failed", description: err.message, variant: "error" });
    },
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => api.providers.test(id),
    onSuccess: (data) => {
      setTestResult(data.data);
      if (data.data.success) {
        // Move to model discovery step
        setStep(3);
      }
    },
    onError: (err: Error) => {
      toast({ title: "Test failed", description: err.message, variant: "error" });
      setTestResult({
        success: false,
        latency_ms: null,
        model_count: null,
        error: err.message,
      });
    },
  });

  const discoverMutation = useMutation({
    mutationFn: (id: string) => api.providers.discover(id),
    onSuccess: (data) => {
      // The API returns model names as strings; map them to DiscoveredModel objects
      const models: DiscoveredModel[] = data.data.models.map((m) => {
        if (typeof m === "string") {
          return { id: m, name: m };
        }
        // If API returns full model objects in the future
        return m as unknown as DiscoveredModel;
      });
      setDiscoveredModels(models);
      // Select all by default
      setSelectedModels(new Set(models.map((m) => m.id)));
      queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (err: Error) => {
      toast({
        title: "Discovery failed",
        description: err.message,
        variant: "error",
      });
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
    setDiscoveredModels([]);
    setSelectedModels(new Set());
    setRegisteredCount(0);
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

  const handleDiscoverModels = () => {
    if (!createdId) return;
    discoverMutation.mutate(createdId);
  };

  const handleToggleModel = (modelId: string) => {
    setSelectedModels((prev) => {
      const next = new Set(prev);
      if (next.has(modelId)) {
        next.delete(modelId);
      } else {
        next.add(modelId);
      }
      return next;
    });
  };

  const handleSelectAll = () => {
    if (selectedModels.size === discoveredModels.length) {
      setSelectedModels(new Set());
    } else {
      setSelectedModels(new Set(discoveredModels.map((m) => m.id)));
    }
  };

  const handleRegisterModels = () => {
    // In a real app, this would call an API to register selected models.
    // For now, we record the count and move to success step.
    setRegisteredCount(selectedModels.size);
    setStep(4);
    queryClient.invalidateQueries({ queryKey: ["providers"] });
    toast({
      title: "Provider connected",
      description: `${name} added with ${selectedModels.size} models`,
      variant: "success",
    });
  };

  const handleSkipToSuccess = () => {
    setRegisteredCount(0);
    setStep(4);
    queryClient.invalidateQueries({ queryKey: ["providers"] });
    toast({
      title: "Provider connected",
      description: `${name} added successfully`,
      variant: "success",
    });
  };

  const stepTitles: Record<number, string> = {
    1: "Add Provider",
    2: `Configure ${selectedType?.label ?? "Provider"}`,
    3: "Discover Models",
    4: "All Set",
  };

  const stepDescriptions: Record<number, string> = {
    1: "Choose an LLM provider to connect",
    2: "Set up the connection details",
    3: "Select models to register from this provider",
    4: "Provider is connected and ready to use",
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{stepTitles[step]}</DialogTitle>
          <DialogDescription>{stepDescriptions[step]}</DialogDescription>
        </DialogHeader>

        {/* Step indicator */}
        <div className="flex items-center gap-1.5">
          {[1, 2, 3, 4].map((s) => (
            <div
              key={s}
              className={cn(
                "h-1 flex-1 rounded-full transition-colors",
                s <= step ? "bg-primary" : "bg-muted"
              )}
            />
          ))}
        </div>

        {/* Step 1: Pick provider type */}
        {step === 1 && (
          <div className="grid grid-cols-2 gap-2">
            {PROVIDER_TYPES.map((meta) => {
              const Icon = meta.icon;
              return (
                <button
                  key={meta.type}
                  onClick={() => meta.available && handleSelectType(meta)}
                  disabled={!meta.available}
                  className={cn(
                    "flex items-start gap-3 rounded-lg border border-border p-3 text-left transition-colors",
                    meta.available
                      ? "hover:border-foreground/20 hover:bg-muted/50"
                      : "cursor-not-allowed opacity-50"
                  )}
                >
                  <div
                    className={cn(
                      "flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-muted/50",
                      meta.color
                    )}
                  >
                    <Icon className="size-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <span className={cn("text-sm font-medium", meta.color)}>
                      {meta.label}
                    </span>
                    <p className="text-[11px] text-muted-foreground">
                      {meta.description}
                    </p>
                    {!meta.available && (
                      <Badge variant="outline" className="mt-1 text-[9px]">
                        Coming soon
                      </Badge>
                    )}
                  </div>
                </button>
              );
            })}
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

            {/* Show test error if test failed inline */}
            {testResult && !testResult.success && (
              <div className="flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-50 px-3 py-2 text-xs text-red-800 dark:bg-red-950 dark:text-red-200">
                <XCircle className="size-3.5 shrink-0" />
                <span>{testResult.error ?? "Connection failed"}</span>
              </div>
            )}
          </div>
        )}

        {/* Step 3: Model discovery */}
        {step === 3 && (
          <div className="space-y-4">
            {/* Test success summary */}
            <div className="flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
              <CheckCircle2 className="size-3.5 shrink-0" />
              <span>
                Connected successfully
                {testResult?.latency_ms != null && ` (${testResult.latency_ms}ms)`}
              </span>
            </div>

            {/* Discover button or model list */}
            {discoveredModels.length === 0 ? (
              <div className="flex flex-col items-center gap-3 py-6">
                {discoverMutation.isPending ? (
                  <>
                    <Loader2 className="size-6 animate-spin text-muted-foreground" />
                    <p className="text-xs text-muted-foreground">
                      Discovering available models...
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-xs text-muted-foreground">
                      Fetch available models from this provider
                    </p>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleDiscoverModels}
                    >
                      <RefreshCw className="size-3" />
                      Discover Models
                    </Button>
                  </>
                )}
              </div>
            ) : (
              <>
                {/* Select all toggle */}
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {discoveredModels.length} models available
                  </span>
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={handleSelectAll}
                  >
                    {selectedModels.size === discoveredModels.length
                      ? "Deselect All"
                      : "Select All"}
                  </Button>
                </div>

                {/* Model checklist */}
                <div className="max-h-64 space-y-1 overflow-y-auto rounded-lg border border-border">
                  {discoveredModels.map((model) => {
                    const isSelected = selectedModels.has(model.id);
                    return (
                      <label
                        key={model.id}
                        className={cn(
                          "flex cursor-pointer items-center gap-3 px-3 py-2 text-xs transition-colors hover:bg-muted/50",
                          isSelected && "bg-muted/30"
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => handleToggleModel(model.id)}
                          className="size-3.5 rounded border-border accent-primary"
                        />
                        <div className="min-w-0 flex-1">
                          <span className="font-mono text-[11px] font-medium">
                            {model.name}
                          </span>
                          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                            {model.context_window && (
                              <span>
                                {(model.context_window / 1000).toFixed(0)}K ctx
                              </span>
                            )}
                            {model.input_price != null && (
                              <span>${model.input_price}/M in</span>
                            )}
                            {model.output_price != null && (
                              <span>${model.output_price}/M out</span>
                            )}
                          </div>
                        </div>
                      </label>
                    );
                  })}
                </div>

                <p className="text-[11px] text-muted-foreground">
                  {selectedModels.size} of {discoveredModels.length} models
                  selected
                </p>
              </>
            )}
          </div>
        )}

        {/* Step 4: Success confirmation */}
        {step === 4 && (
          <div className="flex flex-col items-center gap-3 py-6">
            <div className="flex size-12 items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-900/30">
              <CheckCircle2 className="size-6 text-emerald-600 dark:text-emerald-400" />
            </div>
            <p className="text-sm font-medium">Provider connected</p>
            <p className="text-xs text-muted-foreground">
              {registeredCount > 0
                ? `${name} is ready with ${registeredCount} models registered`
                : `${name} is connected and ready to use`}
            </p>
          </div>
        )}

        {/* Footer */}
        {step === 1 && null}
        {step === 2 && (
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setStep(1)}>
              Back
            </Button>
            <Button
              size="sm"
              onClick={handleConfigure}
              disabled={!name.trim() || createMutation.isPending || testMutation.isPending}
            >
              {createMutation.isPending || testMutation.isPending ? (
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
            <Button variant="outline" size="sm" onClick={handleSkipToSuccess}>
              Skip
            </Button>
            {discoveredModels.length > 0 ? (
              <Button
                size="sm"
                onClick={handleRegisterModels}
                disabled={selectedModels.size === 0}
              >
                <CheckCircle2 className="size-3" />
                Register {selectedModels.size} Model
                {selectedModels.size !== 1 ? "s" : ""}
              </Button>
            ) : !discoverMutation.isPending ? (
              <Button size="sm" onClick={handleDiscoverModels}>
                <RefreshCw className="size-3" />
                Discover Models
              </Button>
            ) : null}
          </DialogFooter>
        )}
        {step === 4 && (
          <DialogFooter>
            <Button size="sm" onClick={() => handleOpenChange(false)}>
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
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editProvider, setEditProvider] = useState<Provider | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["providers"],
    queryFn: () => api.providers.list(),
    staleTime: 10_000,
  });

  const providers = data?.data ?? [];
  const total = data?.meta.total ?? 0;

  const activeCount = providers.filter((p) => p.status === "active").length;
  const errorCount = providers.filter((p) => p.status === "error").length;
  const totalModels = providers.reduce((sum, p) => sum + p.model_count, 0);

  const handleEdit = (provider: Provider) => {
    setEditProvider(provider);
    setEditDialogOpen(true);
  };

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
          {/* Summary stats */}
          {providers.length > 0 && (
            <div className="mb-4 flex items-center gap-4">
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Circle className="size-2 fill-emerald-500 text-emerald-500" />
                {activeCount} active
              </div>
              {errorCount > 0 && (
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Circle className="size-2 fill-red-500 text-red-500" />
                  {errorCount} error
                </div>
              )}
              <div className="text-xs text-muted-foreground">
                {totalModels} total models
              </div>
            </div>
          )}

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
                onClick={() => setAddDialogOpen(true)}
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
            <EmptyState onAdd={() => setAddDialogOpen(true)} />
          ) : (
            <div className="space-y-3">
              {providers.map((provider) => (
                <ProviderCard
                  key={provider.id}
                  provider={provider}
                  onEdit={handleEdit}
                />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      <AddProviderDialog open={addDialogOpen} onOpenChange={setAddDialogOpen} />
      <EditProviderDialog
        provider={editProvider}
        open={editDialogOpen}
        onOpenChange={setEditDialogOpen}
      />
    </div>
  );
}
