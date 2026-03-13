import { useState, useEffect } from "react";
import {
  Cloud,
  Zap,
  Server,
  Activity,
  DollarSign,
  CheckCircle,
  XCircle,
  AlertCircle,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

const API_BASE = "/api/v1/gateway";

// ── Types ──────────────────────────────────────────────────────────────

interface GatewayTier {
  tier: string;
  label: string;
  description: string;
  status: "connected" | "disconnected" | "partial";
  latency_ms: number | null;
  model_count: number;
  base_url: string | null;
}

interface GatewayModel {
  id: string;
  name: string;
  provider: string;
  gateway_tier: string;
  context_window: number;
  input_price_per_million: number;
  output_price_per_million: number;
  status: string;
}

interface GatewayProvider {
  id: string;
  name: string;
  tier: string;
  status: "healthy" | "unhealthy" | "unknown";
  latency_ms: number | null;
  model_count: number;
  last_checked: string;
}

interface LogEntry {
  id: string;
  timestamp: string;
  agent: string;
  model: string;
  provider: string;
  gateway_tier: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  cost_usd: number;
  status: "success" | "error";
}

interface CostRow {
  model: string;
  name: string;
  provider: string;
  gateway_tier: string;
  input_per_million: number;
  output_per_million: number;
  context_window: number;
}

// ── Helpers ─────────────────────────────────────────────────────────────

function formatContext(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(tokens % 1_000_000 === 0 ? 0 : 1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(tokens % 1_000 === 0 ? 0 : 1)}K`;
  return String(tokens);
}

function formatPrice(price: number): string {
  if (price === 0) return "Free";
  if (price < 0.01) return `$${price.toFixed(4)}`;
  return `$${price.toFixed(2)}`;
}

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  openai: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  google: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  meta: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/20",
  mistral: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  litellm: "bg-gray-500/10 text-gray-600 dark:text-gray-400 border-gray-500/20",
};

const TIER_COLORS: Record<string, string> = {
  litellm: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20",
  openrouter: "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20",
  direct: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
};

function StatusIcon({ status }: { status: string }) {
  if (status === "connected" || status === "healthy" || status === "success") {
    return <CheckCircle className="size-4 text-emerald-500" />;
  }
  if (status === "disconnected" || status === "unhealthy" || status === "error") {
    return <XCircle className="size-4 text-red-500" />;
  }
  return <AlertCircle className="size-4 text-amber-500" />;
}

// ── Section components ────────────────────────────────────────────────

function TierCard({ tier }: { tier: GatewayTier }) {
  const Icon = tier.tier === "litellm" ? Server : tier.tier === "openrouter" ? Cloud : Zap;
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex items-start justify-between">
        <div className="flex items-center gap-2">
          <div className="flex size-8 items-center justify-center rounded-md bg-muted">
            <Icon className="size-4 text-muted-foreground" />
          </div>
          <div>
            <div className="text-sm font-medium">{tier.label}</div>
            <div className="text-xs text-muted-foreground">{tier.description}</div>
          </div>
        </div>
        <StatusIcon status={tier.status} />
      </div>
      <div className="grid grid-cols-3 gap-3 border-t border-border pt-3">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Status</div>
          <div className="mt-0.5 text-xs font-medium capitalize">{tier.status}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Latency</div>
          <div className="mt-0.5 font-mono text-xs">
            {tier.latency_ms != null ? `${tier.latency_ms}ms` : "--"}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Models</div>
          <div className="mt-0.5 font-mono text-xs">{tier.model_count}</div>
        </div>
      </div>
      {tier.base_url && (
        <div className="mt-2 truncate font-mono text-[10px] text-muted-foreground">
          {tier.base_url}
        </div>
      )}
    </div>
  );
}

function RoutingTable({ models }: { models: GatewayModel[] }) {
  const [filterTier, setFilterTier] = useState("");
  const tiers = Array.from(new Set(models.map((m) => m.gateway_tier)));
  const filtered = filterTier ? models.filter((m) => m.gateway_tier === filterTier) : models;

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        {["", ...tiers].map((t) => (
          <button
            key={t || "all"}
            onClick={() => setFilterTier(t)}
            className={cn(
              "rounded-full px-3 py-1 text-xs font-medium transition-colors",
              filterTier === t
                ? "bg-foreground text-background"
                : "bg-muted/50 text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            {t || "All"}
          </button>
        ))}
      </div>
      <div className="overflow-hidden rounded-lg border border-border">
        <div className="grid grid-cols-[1fr_100px_100px_80px_100px] gap-4 border-b border-border bg-muted/30 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          <span>Model</span>
          <span>Provider</span>
          <span>Gateway</span>
          <span className="text-right">Context</span>
          <span className="text-right">Status</span>
        </div>
        {filtered.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">
            No models for this tier.
          </div>
        ) : (
          filtered.map((model) => (
            <div
              key={model.id}
              className="grid grid-cols-[1fr_100px_100px_80px_100px] items-center gap-4 border-b border-border/50 px-4 py-2.5 last:border-0 hover:bg-muted/20"
            >
              <div>
                <span className="font-mono text-xs">{model.id}</span>
              </div>
              <Badge
                variant="outline"
                className={cn(
                  "w-fit text-[10px]",
                  PROVIDER_COLORS[model.provider] ?? "bg-muted text-muted-foreground"
                )}
              >
                {model.provider}
              </Badge>
              <Badge
                variant="outline"
                className={cn(
                  "w-fit text-[10px]",
                  TIER_COLORS[model.gateway_tier] ?? "bg-muted text-muted-foreground"
                )}
              >
                {model.gateway_tier}
              </Badge>
              <div className="text-right font-mono text-xs text-muted-foreground">
                {formatContext(model.context_window)}
              </div>
              <div className="flex justify-end">
                <StatusIcon status={model.status} />
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function CostTable({ rows }: { rows: CostRow[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="grid grid-cols-[1fr_100px_100px_100px_100px_80px] gap-4 border-b border-border bg-muted/30 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        <span>Model</span>
        <span>Provider</span>
        <span>Gateway</span>
        <span className="text-right">Input / 1M</span>
        <span className="text-right">Output / 1M</span>
        <span className="text-right">Context</span>
      </div>
      {rows.map((row) => (
        <div
          key={row.model}
          className="grid grid-cols-[1fr_100px_100px_100px_100px_80px] items-center gap-4 border-b border-border/50 px-4 py-2.5 last:border-0 hover:bg-muted/20"
        >
          <div className="font-mono text-xs">{row.model}</div>
          <Badge
            variant="outline"
            className={cn(
              "w-fit text-[10px]",
              PROVIDER_COLORS[row.provider] ?? "bg-muted text-muted-foreground"
            )}
          >
            {row.provider}
          </Badge>
          <Badge
            variant="outline"
            className={cn(
              "w-fit text-[10px]",
              TIER_COLORS[row.gateway_tier] ?? "bg-muted text-muted-foreground"
            )}
          >
            {row.gateway_tier}
          </Badge>
          <div className="text-right font-mono text-xs">{formatPrice(row.input_per_million)}</div>
          <div className="text-right font-mono text-xs">{formatPrice(row.output_per_million)}</div>
          <div className="text-right font-mono text-xs text-muted-foreground">
            {formatContext(row.context_window)}
          </div>
        </div>
      ))}
    </div>
  );
}

function LogTable({ entries }: { entries: LogEntry[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="grid grid-cols-[140px_1fr_120px_70px_70px_70px_60px_60px] gap-3 border-b border-border bg-muted/30 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        <span>Time</span>
        <span>Agent</span>
        <span>Model</span>
        <span className="text-right">In Tok</span>
        <span className="text-right">Out Tok</span>
        <span className="text-right">Latency</span>
        <span className="text-right">Cost</span>
        <span className="text-right">Status</span>
      </div>
      {entries.length === 0 ? (
        <div className="px-4 py-8 text-center text-sm text-muted-foreground">No log entries.</div>
      ) : (
        entries.map((entry) => (
          <div
            key={entry.id}
            className="grid grid-cols-[140px_1fr_120px_70px_70px_70px_60px_60px] items-center gap-3 border-b border-border/50 px-4 py-2 last:border-0 hover:bg-muted/20"
          >
            <div className="truncate font-mono text-[10px] text-muted-foreground">
              {entry.timestamp.replace("T", " ").replace("Z", "")}
            </div>
            <div className="truncate text-xs">{entry.agent}</div>
            <div className="truncate font-mono text-xs">{entry.model}</div>
            <div className="text-right font-mono text-xs text-muted-foreground">
              {entry.input_tokens.toLocaleString()}
            </div>
            <div className="text-right font-mono text-xs text-muted-foreground">
              {entry.output_tokens.toLocaleString()}
            </div>
            <div className="text-right font-mono text-xs">
              {entry.latency_ms}ms
            </div>
            <div className="text-right font-mono text-xs">
              ${entry.cost_usd.toFixed(4)}
            </div>
            <div className="flex justify-end">
              <StatusIcon status={entry.status} />
            </div>
          </div>
        ))
      )}
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────

type Section = "routing" | "costs" | "logs";

export default function GatewayPage() {
  const [tiers, setTiers] = useState<GatewayTier[]>([]);
  const [models, setModels] = useState<GatewayModel[]>([]);
  const [providers, setProviders] = useState<GatewayProvider[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [costRows, setCostRows] = useState<CostRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [activeSection, setActiveSection] = useState<Section>("routing");

  async function fetchAll(isRefresh = false) {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);

    try {
      const [statusRes, modelsRes, providersRes, logsRes, costsRes] = await Promise.all([
        fetch(`${API_BASE}/status`).then((r) => r.json()),
        fetch(`${API_BASE}/models?per_page=200`).then((r) => r.json()),
        fetch(`${API_BASE}/providers`).then((r) => r.json()),
        fetch(`${API_BASE}/logs?per_page=20`).then((r) => r.json()),
        fetch(`${API_BASE}/costs/comparison`).then((r) => r.json()),
      ]);

      setTiers(statusRes.data ?? []);
      setModels(modelsRes.data ?? []);
      setProviders(providersRes.data ?? []);
      setLogs(logsRes.data ?? []);
      setCostRows(costsRes.data ?? []);
    } catch (err) {
      console.error("Failed to load gateway data", err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    fetchAll();
  }, []);

  const totalModels = models.length;
  const connectedProviders = providers.filter((p) => p.status === "healthy").length;

  const SECTIONS: { key: Section; label: string; icon: typeof Activity }[] = [
    { key: "routing", label: "Routing", icon: Zap },
    { key: "costs", label: "Cost Comparison", icon: DollarSign },
    { key: "logs", label: "Request Log", icon: Activity },
  ];

  return (
    <div className="mx-auto max-w-6xl p-6">
      {/* Header */}
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Model Gateway</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {totalModels} models across {connectedProviders} healthy providers
          </p>
        </div>
        <button
          onClick={() => fetchAll(true)}
          disabled={refreshing}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
        >
          <RefreshCw className={cn("size-3", refreshing && "animate-spin")} />
          Refresh
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-24 text-muted-foreground">
          <RefreshCw className="size-5 animate-spin" />
        </div>
      ) : (
        <>
          {/* Tier status cards */}
          <div className="mb-6 grid grid-cols-3 gap-4">
            {tiers.map((tier) => (
              <TierCard key={tier.tier} tier={tier} />
            ))}
          </div>

          {/* Provider health row */}
          <div className="mb-6 overflow-hidden rounded-lg border border-border">
            <div className="border-b border-border bg-muted/30 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Provider Health
            </div>
            <div className="grid grid-cols-[1fr_80px_80px_70px_70px] gap-4 border-b border-border bg-muted/10 px-4 py-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              <span>Provider</span>
              <span>Tier</span>
              <span>Status</span>
              <span className="text-right">Latency</span>
              <span className="text-right">Models</span>
            </div>
            {providers.map((provider) => (
              <div
                key={provider.id}
                className="grid grid-cols-[1fr_80px_80px_70px_70px] items-center gap-4 border-b border-border/50 px-4 py-2.5 last:border-0 hover:bg-muted/20"
              >
                <div className="text-sm font-medium">{provider.name}</div>
                <Badge
                  variant="outline"
                  className={cn(
                    "w-fit text-[10px]",
                    TIER_COLORS[provider.tier] ?? "bg-muted text-muted-foreground"
                  )}
                >
                  {provider.tier}
                </Badge>
                <div className="flex items-center gap-1.5">
                  <StatusIcon status={provider.status} />
                  <span className="text-xs capitalize">{provider.status}</span>
                </div>
                <div className="text-right font-mono text-xs text-muted-foreground">
                  {provider.latency_ms != null ? `${provider.latency_ms}ms` : "--"}
                </div>
                <div className="text-right font-mono text-xs">{provider.model_count}</div>
              </div>
            ))}
          </div>

          {/* Section tabs */}
          <div className="mb-4 flex items-center gap-1.5 border-b border-border">
            {SECTIONS.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                onClick={() => setActiveSection(key)}
                className={cn(
                  "flex items-center gap-1.5 border-b-2 px-3 pb-3 pt-1 text-xs font-medium transition-colors",
                  activeSection === key
                    ? "border-foreground text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                )}
              >
                <Icon className="size-3.5" />
                {label}
              </button>
            ))}
          </div>

          {activeSection === "routing" && <RoutingTable models={models} />}
          {activeSection === "costs" && <CostTable rows={costRows} />}
          {activeSection === "logs" && <LogTable entries={logs} />}
        </>
      )}
    </div>
  );
}
