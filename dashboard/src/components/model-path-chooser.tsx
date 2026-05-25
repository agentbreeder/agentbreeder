/**
 * ModelPathChooser — three-path model runtime selector for the /models page.
 *
 * Replaces the previous flat 3-tab layout (Direct / Gateways / Local) with a
 * single-question chooser: "How do you want to run models?"
 *
 *   LOCAL    — Free, no API key. Runs Ollama on the local machine. Wires the
 *              existing POST /api/v1/providers/detect-ollama endpoint to auto-
 *              register models.
 *
 *   GATEWAY  — Recommended. Routes all model traffic through a single endpoint
 *              (LiteLLM or OpenRouter). Reuses ProviderCatalog with filter="gateway".
 *
 *   DIRECT   — Advanced. Talk directly to an OpenAI-compatible provider. Shows a
 *              pointer for foundation providers (OpenAI / Anthropic / Google) and
 *              the 8 niche catalog entries with collapseAdvanced.
 *
 * The registered-models table and filter pills that appear below this component
 * are unchanged — they come from the parent ModelsPage.
 */
import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, ChevronRight, HardDrive, Network, Plug } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, type OllamaDetectResult } from "@/lib/api";
import { ProviderCatalog } from "@/components/provider-catalog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Path type
// ---------------------------------------------------------------------------

type ModelPath = "local" | "gateway" | "direct";

// ---------------------------------------------------------------------------
// Path card data
// ---------------------------------------------------------------------------

interface PathCardDef {
  id: ModelPath;
  icon: typeof HardDrive;
  label: string;
  badge?: string;
  badgeVariant?: "outline" | "secondary" | "default";
  badgeClass?: string;
  description: string;
}

const PATH_CARDS: PathCardDef[] = [
  {
    id: "local",
    icon: HardDrive,
    label: "Local",
    badge: "Free",
    badgeVariant: "outline",
    badgeClass: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    description: "Run models on your machine with Ollama — no API key required.",
  },
  {
    id: "gateway",
    icon: Network,
    label: "Gateway",
    badge: "Recommended",
    badgeVariant: "outline",
    badgeClass: "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400",
    description: "Route all traffic through a single endpoint (LiteLLM or OpenRouter).",
  },
  {
    id: "direct",
    icon: Plug,
    label: "Direct provider",
    badge: "Advanced",
    badgeVariant: "outline",
    badgeClass: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    description: "Talk directly to an OpenAI-compatible API endpoint.",
  },
];

// ---------------------------------------------------------------------------
// Path card button
// ---------------------------------------------------------------------------

interface PathCardProps extends PathCardDef {
  selected: boolean;
  onClick: () => void;
}

function PathCard({ id, icon: Icon, label, badge, badgeVariant, badgeClass, description, selected, onClick }: PathCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`path-card-${id}`}
      aria-pressed={selected}
      className={cn(
        "flex flex-1 flex-col gap-2 rounded-lg border p-4 text-left transition-all",
        selected
          ? "border-foreground bg-muted/50 shadow-sm"
          : "border-border bg-card hover:border-muted-foreground/40 hover:bg-muted/20",
      )}
    >
      <div className="flex items-center gap-2">
        <Icon className={cn("size-4", selected ? "text-foreground" : "text-muted-foreground")} />
        <span className={cn("text-sm font-semibold", selected ? "text-foreground" : "text-muted-foreground")}>
          {label}
        </span>
        {badge && (
          <Badge variant={badgeVariant} className={cn("text-[10px]", badgeClass)}>
            {badge}
          </Badge>
        )}
        <ChevronRight className={cn("ml-auto size-3.5 shrink-0", selected ? "text-foreground" : "text-muted-foreground/50")} />
      </div>
      <p className="text-[11px] leading-relaxed text-muted-foreground">{description}</p>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Local path panel
// ---------------------------------------------------------------------------

function LocalPathPanel() {
  const [result, setResult] = useState<OllamaDetectResult | null>(null);

  const detectMutation = useMutation({
    mutationFn: () => api.providers.detectOllama(),
    onSuccess: (res) => setResult(res.data),
  });

  return (
    <div className="space-y-4" data-testid="local-path-panel">
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="mb-3 flex items-center gap-2">
          <HardDrive className="size-3.5 text-muted-foreground" />
          <h3 className="text-sm font-semibold">Local — Ollama</h3>
        </div>
        <p className="mb-4 text-[11px] leading-relaxed text-muted-foreground">
          AgentBreeder can auto-detect a running{" "}
          <a
            href="https://ollama.com"
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 hover:text-foreground"
          >
            Ollama
          </a>{" "}
          instance on{" "}
          <code className="rounded bg-muted/50 px-1 py-0.5 text-[10px]">localhost:11434</code>,
          register it as a provider, and discover all locally-available models automatically.
        </p>

        {!result ? (
          <div className="space-y-3">
            <div className="rounded-md bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
              <span className="font-medium">Prerequisites:</span> make sure Ollama is installed and
              running (<code className="rounded bg-muted/50 px-1">ollama serve</code>), then pull at
              least one model (e.g.{" "}
              <code className="rounded bg-muted/50 px-1">ollama pull llama3.2</code>).
            </div>

            {detectMutation.isError && (
              <div
                role="alert"
                className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-[11px] text-destructive"
                data-testid="local-detect-error"
              >
                {detectMutation.error instanceof Error
                  ? detectMutation.error.message
                  : "Could not reach Ollama. Is it running?"}
              </div>
            )}

            <Button
              size="sm"
              variant="outline"
              onClick={() => detectMutation.mutate()}
              disabled={detectMutation.isPending}
              data-testid="local-detect-btn"
              className="h-8 text-xs"
            >
              {detectMutation.isPending ? "Detecting…" : "Detect Ollama"}
            </Button>
          </div>
        ) : (
          <div className="space-y-2" data-testid="local-detect-result">
            <div className="flex items-center gap-2 text-[11px] text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="size-3.5" />
              <span>
                {result.created
                  ? "Ollama registered as a new provider."
                  : "Ollama provider already registered — models refreshed."}
              </span>
            </div>
            {result.models.length > 0 ? (
              <div className="rounded-md bg-muted/30 px-3 py-2">
                <p className="mb-1.5 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                  Discovered models ({result.models.length})
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {result.models.map((m) => (
                    <code
                      key={m.id}
                      className="rounded bg-muted/50 px-2 py-0.5 text-[10px]"
                    >
                      {m.name}
                    </code>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-[11px] text-muted-foreground">
                No models found. Pull one with{" "}
                <code className="rounded bg-muted/50 px-1">ollama pull &lt;model&gt;</code> then
                re-detect.
              </p>
            )}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setResult(null);
                detectMutation.reset();
              }}
              className="h-7 text-xs text-muted-foreground"
              data-testid="local-detect-reset"
            >
              Run again
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Gateway path panel
// ---------------------------------------------------------------------------

function GatewayPathPanel() {
  return (
    <div data-testid="gateway-path-panel">
      <ProviderCatalog
        filter="gateway"
        heading="Model Gateways"
        hint="Reference gateway models in agent.yaml as <gateway>/<upstream>/<model>"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Direct path panel
// ---------------------------------------------------------------------------

function DirectPathPanel() {
  return (
    <div className="space-y-4" data-testid="direct-path-panel">
      {/* Foundation model pointer — OpenAI / Anthropic / Google aren't in the catalog */}
      <div className="rounded-lg border border-border bg-card px-4 py-3">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          <span className="font-medium text-foreground">Using OpenAI, Anthropic, or Google?</span>{" "}
          These providers are configured via their dedicated settings pages — keys never live in the
          catalog.{" "}
          <Link
            to="/settings"
            className="inline-flex items-center gap-0.5 text-[11px] font-medium text-foreground underline underline-offset-2 hover:text-foreground/80"
            data-testid="direct-settings-link"
          >
            Configure in Settings
            <ChevronRight className="size-3" />
          </Link>
        </p>
      </div>

      {/* Niche OpenAI-compatible providers with collapse */}
      <ProviderCatalog
        filter="openai_compatible"
        heading="OpenAI-Compatible Providers"
        collapseAdvanced
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// ModelPathChooser — public export
// ---------------------------------------------------------------------------

export interface ModelPathChooserProps {
  /** Sync button rendered inside the chooser header row (passed from ModelsPage). */
  syncButton?: React.ReactNode;
}

export function ModelPathChooser({ syncButton }: ModelPathChooserProps) {
  const [activePath, setActivePath] = useState<ModelPath>("gateway");

  return (
    <div className="mb-4 space-y-3" data-testid="model-path-chooser">
      {/* Question header */}
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-muted-foreground">
          How do you want to run models?
        </p>
        {syncButton}
      </div>

      {/* Path cards */}
      <div className="flex gap-3" role="group" aria-label="Select model runtime path">
        {PATH_CARDS.map((card) => (
          <PathCard
            key={card.id}
            {...card}
            selected={activePath === card.id}
            onClick={() => setActivePath(card.id)}
          />
        ))}
      </div>

      {/* Active path panel */}
      {activePath === "local" && <LocalPathPanel />}
      {activePath === "gateway" && <GatewayPathPanel />}
      {activePath === "direct" && <DirectPathPanel />}
    </div>
  );
}
