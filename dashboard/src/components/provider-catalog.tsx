/**
 * Provider catalog — surfaces the OpenAI-compatible presets shipped in
 * `engine/providers/catalog.yaml` (Nvidia, Groq, Together, OpenRouter, …) so
 * users can connect a provider with a single click.
 *
 * Backed by:
 *   - `GET /api/v1/providers/catalog`         — list of presets (Track F / #160)
 *   - `GET /api/v1/providers/catalog/status`  — `{name: configured?}` map (#175)
 *   - `POST /api/v1/secrets`                  — write the api-key secret (#175)
 *
 * The "Configure" button opens a modal that takes the api-key value and
 * POSTs it under the deterministic key `<provider>/api-key`. The catalog
 * row then flips to a green "Configured" badge.
 *
 * RBAC:
 *   - viewer  → Configure / Add provider rendered disabled with a tooltip
 *   - deployer / admin → full access
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ExternalLink, Plug, Plus, Server } from "lucide-react";
import { useState } from "react";

import { api, type CatalogProvider } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { useToast } from "@/hooks/use-toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const DEPLOYER_ROLES = new Set(["deployer", "admin"]);
const VIEWER_TOOLTIP = "Requires deployer role";

interface ProviderCatalogProps {
  /** Optional override callback — when omitted, the built-in modal is used. */
  onConfigure?: (provider: CatalogProvider) => void;
}

export function ProviderCatalog({ onConfigure }: ProviderCatalogProps) {
  const { user } = useAuth();
  const canConfigure = !!user && DEPLOYER_ROLES.has(user.role);
  const [activeProvider, setActiveProvider] = useState<CatalogProvider | null>(null);

  const catalogQuery = useQuery({
    queryKey: ["providers", "catalog"],
    queryFn: () => api.providers.catalog(),
    staleTime: 60_000,
  });

  const statusQuery = useQuery({
    queryKey: ["providers", "catalog", "status"],
    queryFn: () => api.providers.catalogStatus(),
    staleTime: 30_000,
  });

  if (catalogQuery.isLoading) {
    return (
      <div className="rounded-lg border border-border bg-muted/20 p-4 text-xs text-muted-foreground">
        Loading provider catalog...
      </div>
    );
  }

  if (catalogQuery.error) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-xs text-destructive">
        Failed to load catalog: {(catalogQuery.error as Error).message}
      </div>
    );
  }

  const presets = catalogQuery.data?.data ?? [];
  if (presets.length === 0) {
    return null;
  }

  const statuses = statusQuery.data?.data ?? {};

  const handleConfigure = (preset: CatalogProvider) => {
    if (onConfigure) {
      onConfigure(preset);
    } else {
      setActiveProvider(preset);
    }
  };

  return (
    <>
      <div className="rounded-lg border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border/50 px-4 py-3">
          <div className="flex items-center gap-2">
            <Server className="size-3.5 text-muted-foreground" />
            <h3 className="text-sm font-semibold">OpenAI-Compatible Catalog</h3>
            <Badge variant="outline" className="text-[10px]">
              {presets.length} providers
            </Badge>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-[11px] text-muted-foreground">
              Click Configure to connect — keys live in your workspace secrets backend, never the database
            </span>
            {canConfigure ? (
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                disabled
                title="Coming soon — add a custom OpenAI-compatible provider"
                data-testid="catalog-add-provider"
              >
                <Plus className="mr-1 size-3" />
                Add provider
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs opacity-50"
                disabled
                title={VIEWER_TOOLTIP}
                data-testid="catalog-add-provider-disabled"
              >
                <Plus className="mr-1 size-3" />
                Add provider
              </Button>
            )}
          </div>
        </div>

        <div className="divide-y divide-border/50">
          {presets.map((preset) => (
            <CatalogRow
              key={preset.name}
              preset={preset}
              configured={statuses[preset.name] ?? false}
              canConfigure={canConfigure}
              onConfigure={handleConfigure}
            />
          ))}
        </div>
      </div>

      <ConfigureModal
        provider={activeProvider}
        onOpenChange={(open) => !open && setActiveProvider(null)}
      />
    </>
  );
}

interface CatalogRowProps {
  preset: CatalogProvider;
  configured: boolean;
  canConfigure: boolean;
  onConfigure: (preset: CatalogProvider) => void;
}

function CatalogRow({ preset, configured, canConfigure, onConfigure }: CatalogRowProps) {
  const isUserLocal = preset.source === "user-local";

  return (
    <div className="flex items-center gap-4 px-4 py-3">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <div className="font-medium text-sm capitalize">{preset.name}</div>
        {isUserLocal && (
          <Badge variant="secondary" className="text-[10px]">
            user-local
          </Badge>
        )}
        <span className="truncate text-[11px] text-muted-foreground">{preset.base_url}</span>
      </div>

      <code className="rounded bg-muted/50 px-2 py-0.5 text-[10px] text-muted-foreground">
        {preset.api_key_env}
      </code>

      {preset.docs && (
        <a
          href={preset.docs}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
        >
          Docs
          <ExternalLink className="size-3" />
        </a>
      )}

      {configured ? (
        <Badge
          variant="outline"
          className="border-emerald-500/30 bg-emerald-500/10 text-[10px] text-emerald-600 dark:text-emerald-400"
          data-testid={`catalog-status-${preset.name}`}
        >
          <CheckCircle2 className="mr-1 size-3" />
          Configured
        </Badge>
      ) : (
        <span
          className="text-[10px] text-muted-foreground"
          data-testid={`catalog-status-${preset.name}`}
        >
          Not configured
        </span>
      )}

      <Button
        size="sm"
        variant="outline"
        onClick={() => canConfigure && onConfigure(preset)}
        disabled={!canConfigure}
        title={canConfigure ? undefined : VIEWER_TOOLTIP}
        className={cn("h-7 text-xs", !canConfigure && "opacity-50")}
        data-testid={`catalog-configure-${preset.name}`}
      >
        <Plug className="mr-1 size-3" />
        {configured ? "Update" : "Configure"}
      </Button>
    </div>
  );
}

// ── Configure modal ─────────────────────────────────────────────────────────

interface ConfigureModalProps {
  provider: CatalogProvider | null;
  onOpenChange: (open: boolean) => void;
}

function ConfigureModal({ provider, onOpenChange }: ConfigureModalProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (v: string) => {
      if (!provider) throw new Error("No provider selected");
      return api.secrets.create({
        name: `${provider.name}/api-key`,
        value: v,
        backend: "env",
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["providers", "catalog", "status"] });
      toast({
        variant: "success",
        title: "Provider configured",
        description: provider ? `${provider.name} api-key saved to your workspace` : undefined,
      });
      reset();
      onOpenChange(false);
    },
    onError: (err: Error) => {
      const msg = err.message ?? "Failed to save secret";
      // 401/403 surface as toast (catalog status will not refresh).
      if (/401|unauthorized|403|forbidden/i.test(msg)) {
        toast({
          variant: "error",
          title: "Permission denied",
          description: "Your role does not allow writing secrets. Ask an admin or deployer.",
        });
        onOpenChange(false);
      } else {
        setError(msg);
      }
    },
  });

  const reset = () => {
    setValue("");
    setError(null);
  };

  const open = provider !== null;
  const canSubmit = value.trim().length > 0 && !mutation.isPending;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
    >
      <DialogContent className="sm:max-w-md" data-testid="configure-modal">
        <DialogHeader>
          <DialogTitle className="capitalize">
            {provider ? `Configure ${provider.name}` : "Configure provider"}
          </DialogTitle>
          <DialogDescription>
            Save an api-key for{" "}
            <span className="font-mono text-foreground">{provider?.api_key_env}</span> to your
            workspace secrets backend. The value never leaves your workspace.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-2">
          <label htmlFor="api-key-input" className="text-xs font-medium">
            API key <span className="text-destructive">*</span>
          </label>
          <Input
            id="api-key-input"
            type="password"
            placeholder={provider ? `Paste your ${provider.api_key_env} value` : ""}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoComplete="off"
            data-testid="configure-modal-input"
            className="h-8 font-mono text-xs"
          />
          {provider?.docs && (
            <a
              href={provider.docs}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
            >
              Where do I find this?
              <ExternalLink className="size-3" />
            </a>
          )}
          {error && (
            <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>
          )}
        </div>

        <DialogFooter>
          <DialogClose render={<Button variant="outline" size="sm" />}>Cancel</DialogClose>
          <Button
            size="sm"
            onClick={() => mutation.mutate(value)}
            disabled={!canSubmit}
            data-testid="configure-modal-submit"
          >
            {mutation.isPending ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
