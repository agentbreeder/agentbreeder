import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound, RefreshCw, Loader2, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api, type SecretSummary } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useToast } from "@/hooks/use-toast";

/**
 * /settings/secrets — Track K dashboard view.
 *
 * Lists secrets in the active workspace (names + masked metadata only — values
 * are NEVER fetched by the client). Shows the workspace backend, supported
 * backends (read-only chooser stub), and a per-secret rotate action.
 */
export default function SettingsSecretsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [rotating, setRotating] = useState<string | null>(null);
  const [newValue, setNewValue] = useState("");

  const workspaceQuery = useQuery({
    queryKey: ["secrets", "workspace"],
    queryFn: () => api.secrets.workspace().then((r) => r.data),
  });

  const secretsQuery = useQuery({
    queryKey: ["secrets", "list"],
    queryFn: () => api.secrets.list().then((r) => r.data),
  });

  const rotateMut = useMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) =>
      api.secrets.rotate(name, value),
    onSuccess: () => {
      toast({ title: "Secret rotated", description: "Agents will pick up the new value." });
      setRotating(null);
      setNewValue("");
      queryClient.invalidateQueries({ queryKey: ["secrets", "list"] });
    },
    onError: (err: Error) => {
      toast({ title: "Rotate failed", description: err.message, variant: "error" });
    },
  });

  const isLoading = workspaceQuery.isLoading || secretsQuery.isLoading;
  const workspace = workspaceQuery.data;
  const secrets: SecretSummary[] = secretsQuery.data ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <KeyRound className="size-5 text-primary" />
            Secrets
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Workspace-scoped secrets. Values never leave the secrets backend.
          </p>
        </div>
      </header>

      {workspace && (
        <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <ShieldCheck className="size-4 text-emerald-500" />
            <div className="text-sm">
              <span className="font-medium">Workspace:</span>{" "}
              <span className="text-muted-foreground">{workspace.workspace}</span>
            </div>
            <div className="text-sm">
              <span className="font-medium">Backend:</span>{" "}
              <span className="rounded bg-muted px-2 py-0.5 font-mono text-xs">
                {workspace.backend}
              </span>
            </div>
          </div>
          <div className="text-xs text-muted-foreground">
            Backend chooser coming soon — switch via{" "}
            <code className="rounded bg-muted px-1">~/.agentbreeder/workspace.yaml</code>.
          </div>
        </div>
      )}

      <section className="rounded-lg border border-border bg-card">
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">Secrets ({secrets.length})</h2>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => queryClient.invalidateQueries({ queryKey: ["secrets", "list"] })}
          >
            Refresh
          </Button>
        </header>

        {isLoading ? (
          <div className="flex items-center justify-center p-10 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
          </div>
        ) : secrets.length === 0 ? (
          <div className="p-10 text-center text-sm text-muted-foreground">
            No secrets in this workspace yet. Run{" "}
            <code className="rounded bg-muted px-1 py-0.5">agentbreeder secret set NAME</code>.
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {secrets.map((s) => (
              <li key={s.name} className="px-4 py-3">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm font-medium">{s.name}</span>
                      <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                        {s.backend}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      <span className="font-mono">{s.masked_value}</span>
                      {s.updated_at && (
                        <span>updated {new Date(s.updated_at).toLocaleDateString()}</span>
                      )}
                      {s.mirror_destinations.length > 0 && (
                        <span>
                          mirrored to{" "}
                          <span className="font-medium">
                            {s.mirror_destinations.join(", ")}
                          </span>
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {rotating === s.name ? (
                      <>
                        <input
                          type="password"
                          autoFocus
                          value={newValue}
                          onChange={(e) => setNewValue(e.target.value)}
                          placeholder="paste new value"
                          className="rounded-md border border-border bg-background px-2 py-1 font-mono text-xs outline-none focus:border-primary"
                        />
                        <Button
                          size="sm"
                          disabled={!newValue || rotateMut.isPending}
                          onClick={() =>
                            rotateMut.mutate({ name: s.name, value: newValue })
                          }
                        >
                          {rotateMut.isPending ? (
                            <Loader2 className="size-3 animate-spin" />
                          ) : (
                            "Confirm"
                          )}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setRotating(null);
                            setNewValue("");
                          }}
                        >
                          Cancel
                        </Button>
                      </>
                    ) : (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setRotating(s.name);
                          setNewValue("");
                        }}
                      >
                        <RefreshCw className="mr-1.5 size-3" />
                        Rotate
                      </Button>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
