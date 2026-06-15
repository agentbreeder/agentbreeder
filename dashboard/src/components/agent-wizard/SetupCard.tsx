/**
 * SetupCard — inline dependency-capture card rendered inside the chat thread.
 *
 * When the conversational builder needs a credential (secret), a model-provider
 * key, or an MCP server, the backend emits a `setup_request` SSE event and this
 * card appears as a first-class message. The captured value goes straight to the
 * secrets / MCP backend (server-side, masked) — it is never kept in component
 * state after submit and never enters the agent spec. The spec records only a
 * reference (name).
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { KeyRound, Plug, Server, Loader2, AlertCircle, Check, X } from "lucide-react";
import { api, type ChatBuildSetupRequest } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// MCP transports supported by POST /mcp-servers (McpServerCreate.transport).
const MCP_TRANSPORTS = ["stdio", "sse", "streamable_http"] as const;
type McpTransport = (typeof MCP_TRANSPORTS)[number];

function SetupIcon({ kind }: { kind: ChatBuildSetupRequest["kind"] }) {
  if (kind === "mcp") return <Server className="size-4" />;
  if (kind === "provider") return <Plug className="size-4" />;
  return <KeyRound className="size-4" />;
}

function titleFor(req: ChatBuildSetupRequest): string {
  if (req.kind === "mcp") return `Connect the “${req.name}” MCP server`;
  if (req.kind === "provider") return `Add your ${req.name} API key`;
  return `Add the secret “${req.name}”`;
}

export function SetupCard({
  request,
  onComplete,
  onSkip,
}: {
  request: ChatBuildSetupRequest;
  /** Called with a confirmation message to append to the thread and continue the build. */
  onComplete: (confirmation: string) => void;
  /** Called when the user dismisses the card without providing the dependency. */
  onSkip: () => void;
}) {
  const [value, setValue] = useState(""); // secret / provider key (password)
  const [endpoint, setEndpoint] = useState(""); // mcp endpoint
  const [transport, setTransport] = useState<McpTransport>("stdio");
  const [error, setError] = useState<string | null>(null);

  const submit = useMutation({
    mutationFn: async (): Promise<string> => {
      if (request.kind === "mcp") {
        const created = await api.mcpServers.create({
          name: request.name,
          endpoint: endpoint.trim(),
          transport,
        });
        const id = created.data.id;
        let toolCount = 0;
        try {
          const discovered = await api.mcpServers.discover(id);
          toolCount = discovered.data.tools?.length ?? discovered.data.total ?? 0;
        } catch {
          // Registration succeeded; tool discovery is best-effort (server may be
          // offline). The agent can still reference the server by name.
          toolCount = 0;
        }
        return `I've connected the MCP server '${request.name}' (${toolCount} tools discovered). You can reference its tools in the spec.`;
      }

      // secret + provider both persist a value via the secrets backend.
      const secretName =
        request.kind === "provider" ? `${request.name}/api-key` : request.name;
      await api.secrets.create({ name: secretName, value: value.trim() });

      if (request.kind === "provider") {
        return `I've added my ${request.name} API key.`;
      }
      return `I've added the secret \`${request.name}\`.`;
    },
    onSuccess: (confirmation) => {
      // Clear any captured value from state immediately — never retained.
      setValue("");
      setEndpoint("");
      setError(null);
      onComplete(confirmation);
    },
    onError: (err: Error) => {
      setValue("");
      setError(err.message || "Setup failed. Please try again.");
    },
  });

  const canSubmit =
    request.kind === "mcp" ? endpoint.trim().length > 0 : value.trim().length > 0;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || submit.isPending) return;
    submit.mutate();
  }

  return (
    <div
      data-testid="setup-card"
      className="rounded-xl border border-primary/30 bg-primary/5 p-4 space-y-3"
    >
      <div className="flex items-center gap-2 text-sm font-medium text-primary">
        <SetupIcon kind={request.kind} />
        {titleFor(request)}
      </div>
      {request.reason && (
        <p className="text-xs text-muted-foreground">{request.reason}</p>
      )}

      <form onSubmit={handleSubmit} className="space-y-3">
        {request.kind === "mcp" ? (
          <div className="space-y-2">
            <input
              data-testid="setup-mcp-endpoint"
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
              placeholder="Endpoint (URL or command)"
              autoComplete="off"
              className={cn(
                "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm",
                "focus:outline-none focus:ring-2 focus:ring-primary/50",
              )}
            />
            <select
              data-testid="setup-mcp-transport"
              value={transport}
              onChange={(e) => setTransport(e.target.value as McpTransport)}
              className={cn(
                "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm",
                "focus:outline-none focus:ring-2 focus:ring-primary/50",
              )}
            >
              {MCP_TRANSPORTS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <input
            data-testid="setup-secret-input"
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={request.kind === "provider" ? "API key" : "Secret value"}
            autoComplete="off"
            className={cn(
              "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm",
              "focus:outline-none focus:ring-2 focus:ring-primary/50",
            )}
          />
        )}

        {error && (
          <p className="text-xs text-destructive flex items-center gap-1">
            <AlertCircle className="size-3" />
            {error}
          </p>
        )}

        <div className="flex items-center gap-2">
          <Button
            type="submit"
            data-testid="setup-card-submit"
            disabled={!canSubmit || submit.isPending}
            className="flex-1"
          >
            {submit.isPending ? (
              <>
                <Loader2 className="mr-2 size-4 animate-spin" />
                Connecting…
              </>
            ) : (
              <>
                <Check className="mr-2 size-4" />
                Connect
              </>
            )}
          </Button>
          <Button
            type="button"
            data-testid="setup-card-skip"
            variant="ghost"
            onClick={onSkip}
            disabled={submit.isPending}
          >
            <X className="mr-1 size-4" />
            Skip
          </Button>
        </div>
      </form>

      <p className="text-[11px] text-muted-foreground">
        Stored securely in your workspace — never shown in the spec or returned to the browser.
      </p>
    </div>
  );
}
