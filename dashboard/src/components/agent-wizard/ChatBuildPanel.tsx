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
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Send, User, Key, Loader2, CheckCircle, AlertCircle, Plus } from "lucide-react";
import { api, type ChatBuildMessage, type ChatBuildResult } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAuth } from "@/hooks/use-auth";

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
}: {
  agentYaml: string;
  valid: boolean;
  errors: string[];
}) {
  const navigate = useNavigate();

  const createMutation = useMutation({
    mutationFn: () => api.agents.fromYaml(agentYaml),
    onSuccess: (res) => {
      const agent = res.data;
      navigate(`/agents/${agent.id}`);
    },
  });

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
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main ChatBuildPanel component
// ---------------------------------------------------------------------------

export function ChatBuildPanel() {
  const { user } = useAuth();
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState("");
  const [pendingSpec, setPendingSpec] = useState<ChatBuildResult | null>(null);
  const [keyConnected, setKeyConnected] = useState(false);

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

  // ── Auto-scroll ───────────────────────────────────────────────────────
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [entries, pendingSpec]);

  // ── Chat mutation ─────────────────────────────────────────────────────
  const chatMutation = useMutation({
    mutationFn: (msgs: ChatBuildMessage[]) => api.builders.chat(msgs),
    onSuccess: (res) => {
      const result = res.data;

      if (result.agent_yaml) {
        // Claude submitted a spec — show the spec card.
        setPendingSpec(result);
        if (result.assistant_message) {
          setEntries((prev) => [
            ...prev,
            { id: generateId(), role: "assistant", content: result.assistant_message },
          ]);
        }
      } else {
        // Plain text reply — add to conversation.
        setEntries((prev) => [
          ...prev,
          { id: generateId(), role: "assistant", content: result.assistant_message },
        ]);
      }
    },
  });

  // ── Build the message history to send ────────────────────────────────
  function buildHistory(currentInput: string): ChatBuildMessage[] {
    const history: ChatBuildMessage[] = entries.map((e) => ({
      role: e.role,
      content: e.content,
    }));
    history.push({ role: "user", content: currentInput });
    return history;
  }

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || chatMutation.isPending) return;

    // Add user message to UI immediately.
    setEntries((prev) => [
      ...prev,
      { id: generateId(), role: "user", content: trimmed },
    ]);
    setInput("");
    setPendingSpec(null);

    // Reset textarea height.
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }

    chatMutation.mutate(buildHistory(trimmed));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input, entries, chatMutation]);

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
        {entries.length === 0 && !chatMutation.isPending && (
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

        {/* Loading indicator */}
        {chatMutation.isPending && (
          <div className="flex gap-3">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Bot className="size-4" />
            </div>
            <div className="rounded-2xl rounded-tl-md bg-muted px-4 py-2.5">
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            </div>
          </div>
        )}

        {/* Spec card */}
        {pendingSpec && (
          <SpecReadyCard
            agentYaml={pendingSpec.agent_yaml!}
            valid={pendingSpec.valid}
            errors={pendingSpec.errors}
          />
        )}

        {/* Error banner */}
        {chatMutation.isError && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive flex items-center gap-2">
            <AlertCircle className="size-4 shrink-0" />
            {(chatMutation.error as Error).message ||
              "Something went wrong. Please try again."}
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
            disabled={!input.trim() || chatMutation.isPending}
            data-testid="send-btn"
            className="shrink-0"
          >
            {chatMutation.isPending ? (
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
