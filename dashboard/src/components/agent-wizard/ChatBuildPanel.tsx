/**
 * ChatBuildPanel — BYO-key conversational agent builder.
 *
 * Security notes:
 * - The Claude API key is stored in the workspace secrets backend (server-side).
 * - The key is never stored in component state, never logged, never sent to the API
 *   directly — only via POST /secrets where it is persisted server-side.
 * - The key-entry <input> is type="password" and is cleared immediately after submit.
 * - secrets.list() returns masked metadata only (no key values).
 * - The chat API never receives the key from the browser.
 */

import { useRef, useEffect, useCallback, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Send, User, Key, Loader2, CheckCircle, AlertCircle, Plus } from "lucide-react";
import {
  api,
  type ChatBuildMessage,
  type ChatBuildResult,
  type ChatBuildSetupRequest,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/use-auth";
import { SetupCard } from "./SetupCard";
import { CodeArtifactPanel } from "./CodeArtifactPanel";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/**
 * Prefix for the per-user BYO Claude API key secret.
 * The full secret name is built by appending the user's stable id:
 *   `${BUILDER_KEY_PREFIX}__${user.id}`
 *
 * Must match the Python helper _builder_key_name() in api/routes/builders.py.
 */
const BUILDER_KEY_PREFIX = "AGENTBREEDER_CLAUDE_BUILDER_KEY";

/**
 * Return the workspace secret name for the current user's BYO Claude API key.
 * Mirrors the Python function _builder_key_name(user) in api/routes/builders.py.
 */
function builderKeySecretName(userId: string): string {
  return `${BUILDER_KEY_PREFIX}__${userId}`;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ChatEntry {
  id: string;
  role: "user" | "assistant";
  content: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function generateId(): string {
  return crypto.randomUUID?.() ?? Math.random().toString(36).slice(2);
}

// ---------------------------------------------------------------------------
// Key-entry guard component
// ---------------------------------------------------------------------------

function KeyEntryGuard({
  userId,
  onKeyStored,
}: {
  userId: string;
  onKeyStored: () => void;
}) {
  const secretName = builderKeySecretName(userId);
  const [keyValue, setKeyValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const storeMutation = useMutation({
    mutationFn: async (value: string) => {
      // Send to the secrets backend — the key never touches our DB.
      await api.secrets.create({ name: secretName, value });
    },
    onSuccess: () => {
      // Clear from state immediately after the request completes.
      setKeyValue("");
      setError(null);
      // Invalidate the secrets list so the parent re-checks.
      void queryClient.invalidateQueries({ queryKey: ["secrets", "list"] });
      onKeyStored();
    },
    onError: (err: Error) => {
      setError(err.message || "Failed to store key. Please try again.");
      setKeyValue("");
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = keyValue.trim();
    if (!trimmed) return;
    storeMutation.mutate(trimmed);
  }

  return (
    <div className="flex flex-col items-center justify-center h-full gap-6 px-8 text-center">
      <div className="flex size-14 items-center justify-center rounded-full bg-primary/10">
        <Key className="size-6 text-primary" />
      </div>
      <div className="space-y-2 max-w-md">
        <h3 className="text-lg font-semibold">Connect your Claude API key</h3>
        <p className="text-sm text-muted-foreground">
          The chat-to-build feature uses your own Claude API key (stored securely in your
          workspace secrets backend — never in the database, never returned to the browser).
        </p>
      </div>

      <form onSubmit={handleSubmit} className="w-full max-w-sm space-y-3">
        <input
          type="password"
          placeholder="sk-ant-..."
          value={keyValue}
          onChange={(e) => setKeyValue(e.target.value)}
          autoComplete="off"
          data-testid="key-input"
          className={cn(
            "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm",
            "focus:outline-none focus:ring-2 focus:ring-primary/50",
          )}
        />
        {error && (
          <p className="text-xs text-destructive flex items-center gap-1">
            <AlertCircle className="size-3" />
            {error}
          </p>
        )}
        <Button
          type="submit"
          disabled={!keyValue.trim() || storeMutation.isPending}
          className="w-full"
          data-testid="store-key-btn"
        >
          {storeMutation.isPending ? (
            <>
              <Loader2 className="mr-2 size-4 animate-spin" />
              Storing key…
            </>
          ) : (
            <>
              <Plus className="mr-2 size-4" />
              Connect key
            </>
          )}
        </Button>
      </form>

      <p className="text-xs text-muted-foreground max-w-xs">
        Your key is stored via{" "}
        <span className="font-mono text-foreground">{secretName}</span> in your
        workspace secrets backend. Get a key at{" "}
        <a
          href="https://console.anthropic.com"
          target="_blank"
          rel="noopener noreferrer"
          className="underline"
        >
          console.anthropic.com
        </a>
        .
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chat message bubble
// ---------------------------------------------------------------------------

function ChatBubble({ entry }: { entry: ChatEntry }) {
  const isUser = entry.role === "user";
  return (
    <div className={cn("flex gap-3", isUser && "flex-row-reverse")}>
      <div
        className={cn(
          "flex size-8 shrink-0 items-center justify-center rounded-full",
          isUser ? "bg-foreground text-background" : "bg-primary/10 text-primary",
        )}
      >
        {isUser ? <User className="size-4" /> : <Bot className="size-4" />}
      </div>
      <div
        className={cn(
          "max-w-[75%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
          isUser
            ? "rounded-tr-md bg-foreground text-background"
            : "rounded-tl-md bg-muted text-foreground",
        )}
      >
        <div className="whitespace-pre-wrap">{entry.content}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent spec preview card — shown when Claude emits a valid spec
// ---------------------------------------------------------------------------

function SpecReadyCard({
  agentYaml,
  valid,
  errors,
  onEjectToCode,
  ejecting,
}: {
  agentYaml: string;
  valid: boolean;
  errors: string[];
  onEjectToCode: () => void;
  ejecting: boolean;
}) {
  const navigate = useNavigate();
  const [logs, setLogs] = useState<string[]>([]);
  const [endpoint, setEndpoint] = useState<string | null>(null);
  const [deploying, setDeploying] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: () => api.agents.fromYaml(agentYaml),
    onSuccess: (res) => {
      const agent = res.data;
      navigate(`/agents/${agent.id}`);
    },
  });

  async function handleDeploy() {
    setDeploying(true);
    setLogs([]);
    setEndpoint(null);
    setDeployError(null);
    try {
      // TODO(Wave 2): expose deploy target selector (currently local-only)
      const job = await api.deploys.create({ config_yaml: agentYaml, target: "local" });
      const jobId = job.data.id;
      const seen = new Set<string>();
      const terminal = new Set(["completed", "failed"]);
      // Poll up to ~5 min (250 * 1.2s) — guard against an unbounded loop.
      for (let i = 0; i < 250; i++) {
        const detail = (await api.deploys.getDetail(jobId)).data;
        for (const entry of detail.logs) {
          const key = `${entry.timestamp}:${entry.message}`;
          if (!seen.has(key)) {
            seen.add(key);
            setLogs((prev) => [...prev, entry.message]);
          }
        }
        if (terminal.has(detail.status)) {
          if (detail.status === "completed") {
            setEndpoint(`/agents/${detail.agent_id}`);
          } else {
            setDeployError(detail.error_message ?? "Deploy failed.");
          }
          break;
        }
        await new Promise((r) => setTimeout(r, 1200));
      }
    } catch (err) {
      setDeployError((err as Error).message || "Deploy failed.");
    } finally {
      setDeploying(false);
    }
  }

  if (!valid) {
    return (
      <div
        data-testid="spec-invalid-card"
        className="rounded-xl border border-destructive/40 bg-destructive/5 p-4 space-y-2"
      >
        <div className="flex items-center gap-2 text-sm font-medium text-destructive">
          <AlertCircle className="size-4" />
          Spec needs revision
        </div>
        <ul className="text-xs text-destructive/80 space-y-0.5 list-disc list-inside">
          {errors.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">
          Describe what you&apos;d like to change and I&apos;ll regenerate the spec.
        </p>
      </div>
    );
  }

  return (
    <div
      data-testid="spec-ready-card"
      className="rounded-xl border border-green-500/40 bg-green-500/5 p-4 space-y-3"
    >
      <div className="flex items-center gap-2 text-sm font-medium text-green-600 dark:text-green-400">
        <CheckCircle className="size-4" />
        Agent spec ready
      </div>
      <pre className="rounded-lg bg-background border border-border px-3 py-2 text-[11px] leading-relaxed overflow-x-auto max-h-48">
        {agentYaml}
      </pre>
      <Button
        data-testid="create-agent-btn"
        onClick={() => createMutation.mutate()}
        disabled={createMutation.isPending}
        className="w-full"
      >
        {createMutation.isPending ? (
          <>
            <Loader2 className="mr-2 size-4 animate-spin" />
            Creating agent…
          </>
        ) : (
          "Create agent"
        )}
      </Button>
      {createMutation.isError && (
        <p className="text-xs text-destructive flex items-center gap-1">
          <AlertCircle className="size-3" />
          {(createMutation.error as Error).message}
        </p>
      )}
      <Button
        data-testid="deploy-agent-btn"
        variant="secondary"
        onClick={() => void handleDeploy()}
        disabled={deploying}
        className="w-full"
      >
        {deploying ? (
          <>
            <Loader2 className="mr-2 size-4 animate-spin" />
            Deploying…
          </>
        ) : (
          "Deploy now"
        )}
      </Button>
      {deployError && (
        <p className="text-xs text-destructive flex items-center gap-1">
          <AlertCircle className="size-3" />
          {deployError}
        </p>
      )}
      <Button
        data-testid="eject-code-btn"
        variant="outline"
        onClick={onEjectToCode}
        disabled={ejecting}
        className="w-full"
      >
        {ejecting ? (
          <>
            <Loader2 className="mr-2 size-4 animate-spin" />
            Generating code…
          </>
        ) : (
          "Eject to code"
        )}
      </Button>
      {logs.length > 0 && (
        <pre className="rounded-lg bg-background border border-border px-3 py-2 text-[11px] max-h-40 overflow-y-auto">
          {logs.join("\n")}
        </pre>
      )}
      {endpoint && (
        <Link
          to={endpoint}
          className="text-xs underline text-green-600"
        >
          Agent deployed — view it
        </Link>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main ChatBuildPanel component
// ---------------------------------------------------------------------------

export function ChatBuildPanel({ initialPrompt }: { initialPrompt?: string } = {}) {
  const { user } = useAuth();
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState("");
  const [pendingSpec, setPendingSpec] = useState<ChatBuildResult | null>(null);
  const [keyConnected, setKeyConnected] = useState(false);
  const [streaming, setStreaming] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [pendingSetup, setPendingSetup] = useState<ChatBuildSetupRequest | null>(null);

  // ── Eject-to-code (Wave 3) ───────────────────────────────────────────
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [files, setFiles] = useState<Record<string, string>>({});
  const [artifactTab, setArtifactTab] = useState<"spec" | "code" | "deploy">("spec");
  const [ejecting, setEjecting] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Per-user secret name — stable once the user is loaded.
  const secretName = user ? builderKeySecretName(user.id) : null;

  // ── Check whether the key is already stored ──────────────────────────
  const { data: secretsList, isLoading: secretsLoading } = useQuery({
    queryKey: ["secrets", "list"],
    queryFn: () => api.secrets.list(),
  });

  const hasKey =
    keyConnected ||
    (secretName !== null &&
      (secretsList?.data ?? []).some((s) => s.name === secretName));

  // ── Auto-send ref — declared early; effect is placed after sendStreaming ──
  const autoSentRef = useRef(false);

  // ── Auto-scroll ───────────────────────────────────────────────────────
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [entries, pendingSpec, streaming]);

  // ── Build the message history to send ────────────────────────────────
  function buildHistory(currentInput: string): ChatBuildMessage[] {
    const history: ChatBuildMessage[] = entries.map((e) => ({
      role: e.role,
      content: e.content,
    }));
    history.push({ role: "user", content: currentInput });
    return history;
  }

  // ── Streaming send ────────────────────────────────────────────────────
  const sendStreaming = useCallback(
    async (msgs: ChatBuildMessage[]) => {
      setSending(true);
      setStreaming("");
      setSendError(null);
      let acc = "";
      let setupRequested = false;
      try {
        await api.builders.chatStream(msgs, (event, data) => {
          if (event === "token") {
            acc += (data as { text: string }).text;
            setStreaming(acc);
          } else if (event === "setup_request") {
            // The agent needs a dependency — render an inline card in the thread.
            setupRequested = true;
            setPendingSetup(data as ChatBuildSetupRequest);
          } else if (event === "done") {
            const result = data as ChatBuildResult;
            setStreaming(null);
            if (result.setup_request) {
              // Fallback: the done payload also carries the setup request.
              setupRequested = true;
              setPendingSetup(result.setup_request);
            }
            if (result.agent_yaml) {
              // Claude submitted a spec — show the spec card.
              setPendingSpec(result);
              if (result.assistant_message) {
                setEntries((prev) => [
                  ...prev,
                  { id: generateId(), role: "assistant", content: result.assistant_message },
                ]);
              }
            } else if (!setupRequested) {
              // Plain text reply — add to conversation. Skipped while a setup card
              // is pending so we don't show an empty assistant bubble.
              setEntries((prev) => [
                ...prev,
                { id: generateId(), role: "assistant", content: result.assistant_message || acc },
              ]);
            } else if (result.assistant_message) {
              // Setup pending, but the model also said something — keep it.
              setEntries((prev) => [
                ...prev,
                { id: generateId(), role: "assistant", content: result.assistant_message },
              ]);
            }
          } else if (event === "error") {
            setSendError((data as { detail?: string }).detail ?? "Something went wrong.");
          }
        });
      } catch (err) {
        setSendError((err as Error).message || "Something went wrong.");
      } finally {
        setSending(false);
        setStreaming(null);
      }
    },
    [],
  );

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || sending) return;

    // Add user message to UI immediately.
    setEntries((prev) => [
      ...prev,
      { id: generateId(), role: "user", content: trimmed },
    ]);
    setInput("");
    setPendingSpec(null);
    setPendingSetup(null);
    setSendError(null);

    // Reset textarea height.
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }

    void sendStreaming(buildHistory(trimmed));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input, entries, sending, sendStreaming]);

  // ── Inline setup card completed → thread the confirmation and continue ─
  // Defined as a plain closure (not memoised) so it always sees the latest
  // `entries` when SetupCard fires onComplete.
  function handleSetupComplete(confirmation: string) {
    setPendingSetup(null);
    setEntries((prev) => [
      ...prev,
      { id: generateId(), role: "user", content: confirmation },
    ]);
    void sendStreaming(buildHistory(confirmation));
  }

  // ── Lazy builder-session creation ─────────────────────────────────────
  // Created on first eject. Returns the session id (unwraps ApiResponse.data).
  const ensureSession = useCallback(async (): Promise<string> => {
    if (sessionId) return sessionId;
    const res = await api.builderSessions.create("claude");
    const id = res.data.id;
    setSessionId(id);
    return id;
  }, [sessionId]);

  // ── Eject the validated spec to generated code ────────────────────────
  const handleEject = useCallback(async () => {
    setEjecting(true);
    setSendError(null);
    try {
      const id = await ensureSession();
      await api.builderSessions.eject(
        id,
        "Generate the agent.py, tools, and tests for this spec.",
        (event, data) => {
          if (event === "file_change") {
            const change = data as { path: string; diff: string; content: string };
            setFiles((prev) => ({ ...prev, [change.path]: change.content }));
          } else if (event === "complete") {
            setEjecting(false);
            setArtifactTab("code");
          } else if (event === "error") {
            setEjecting(false);
            setSendError((data as { detail?: string }).detail ?? "Code generation failed.");
          }
        },
      );
    } catch (err) {
      setSendError((err as Error).message || "Code generation failed.");
    } finally {
      setEjecting(false);
    }
  }, [ensureSession]);

  // ── Auto-send initialPrompt once the key is confirmed ────────────────
  // Placed here so sendStreaming + buildHistory are already in scope.
  useEffect(() => {
    if (!hasKey || !initialPrompt || autoSentRef.current) return;
    autoSentRef.current = true;
    setEntries((prev) => [
      ...prev,
      { id: generateId(), role: "user", content: initialPrompt },
    ]);
    void sendStreaming(buildHistory(initialPrompt));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasKey]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  // ── Render: loading ───────────────────────────────────────────────────
  if (secretsLoading || !user) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // ── Render: key guard ─────────────────────────────────────────────────
  if (!hasKey) {
    return <KeyEntryGuard userId={user.id} onKeyStored={() => setKeyConnected(true)} />;
  }

  // ── Render: chat UI ───────────────────────────────────────────────────
  return (
    <div className="flex h-full flex-col">
      {/* Message list */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-4 space-y-4 min-h-0"
      >
        {entries.length === 0 && !sending && (
          <div className="text-center text-sm text-muted-foreground pt-8 space-y-2">
            <Bot className="size-8 mx-auto text-primary/40" />
            <p>Describe the agent you want to build.</p>
            <p className="text-xs">
              I&apos;ll ask a few quick questions and then generate a ready-to-deploy{" "}
              <span className="font-mono">agent.yaml</span>.
            </p>
          </div>
        )}

        {entries.map((entry) => (
          <ChatBubble key={entry.id} entry={entry} />
        ))}

        {/* Streaming bubble — shows incremental tokens or a spinner while waiting for the first token */}
        {streaming !== null && (
          <ChatBubble
            entry={{ id: "streaming", role: "assistant", content: streaming || "…" }}
          />
        )}

        {/* Loading spinner — shown while sending but before any token arrives */}
        {sending && streaming === null && (
          <div className="flex gap-3">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Bot className="size-4" />
            </div>
            <div className="rounded-2xl rounded-tl-md bg-muted px-4 py-2.5">
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            </div>
          </div>
        )}

        {/* Inline dependency-capture card */}
        {pendingSetup && (
          <SetupCard
            request={pendingSetup}
            onComplete={handleSetupComplete}
            onSkip={() => setPendingSetup(null)}
          />
        )}

        {/* Spec card / artifact panel.
            Invalid specs render the bare card (no tabs). A valid spec gets the
            tabbed artifact panel: Spec · Code · Deploy. */}
        {pendingSpec && !pendingSpec.valid && (
          <SpecReadyCard
            agentYaml={pendingSpec.agent_yaml!}
            valid={pendingSpec.valid}
            errors={pendingSpec.errors}
            onEjectToCode={() => void handleEject()}
            ejecting={ejecting}
          />
        )}

        {pendingSpec && pendingSpec.valid && (
          <div
            data-testid="artifact-panel"
            className="rounded-xl border border-border overflow-hidden"
          >
            <div role="tablist" className="flex border-b border-border bg-muted/40">
              {(["spec", "code", "deploy"] as const).map((tab) => {
                const label = tab === "spec" ? "Spec" : tab === "code" ? "Code" : "Deploy";
                const selected = artifactTab === tab;
                return (
                  <button
                    key={tab}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    data-testid={`artifact-tab-${tab}`}
                    onClick={() => setArtifactTab(tab)}
                    className={cn(
                      "px-4 py-2 text-sm font-medium transition-colors",
                      selected
                        ? "border-b-2 border-primary text-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
            <div role="tabpanel" className="p-4">
              {/* The spec card owns the deploy flow + logs, so it stays mounted
                  across tab switches (hidden, not unmounted, to preserve deploy
                  state). It backs both the Spec and Deploy tabs. */}
              <div className={cn(artifactTab === "code" && "hidden")}>
                <SpecReadyCard
                  agentYaml={pendingSpec.agent_yaml!}
                  valid={pendingSpec.valid}
                  errors={pendingSpec.errors}
                  onEjectToCode={() => void handleEject()}
                  ejecting={ejecting}
                />
              </div>
              {artifactTab === "code" && (
                <div className="h-72">
                  <CodeArtifactPanel files={files} />
                </div>
              )}
            </div>
          </div>
        )}

        {/* Error banner */}
        {sendError && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive flex items-center gap-2">
            <AlertCircle className="size-4 shrink-0" />
            {sendError}
          </div>
        )}
      </div>

      {/* Input area */}
      <div className="shrink-0 border-t border-border p-4">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            placeholder="Describe your agent…"
            rows={1}
            data-testid="chat-input"
            className={cn(
              "flex-1 resize-none rounded-lg border border-border bg-background px-3 py-2",
              "text-sm leading-relaxed focus:outline-none focus:ring-2 focus:ring-primary/50",
              "max-h-40 overflow-y-auto",
            )}
          />
          <Button
            size="icon"
            onClick={handleSend}
            disabled={!input.trim() || sending}
            data-testid="send-btn"
            className="shrink-0"
          >
            {sending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Send className="size-4" />
            )}
          </Button>
        </div>
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          Enter to send · Shift+Enter for new line
        </p>
      </div>
    </div>
  );
}
