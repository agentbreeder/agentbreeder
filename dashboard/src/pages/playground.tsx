import { useState, useRef, useEffect, useCallback } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  Send,
  Bot,
  User,
  Wrench,
  ChevronDown,
  ChevronRight,
  Trash2,
  Save,
  Settings2,
  Zap,
  Loader2,
  Copy,
  Check,
  Clock,
  Coins,
  Hash,
  Eye,
  EyeOff,
  FileText,
} from "lucide-react";
import {
  api,
  type Agent,
  type ConversationMessage,
  type PlaygroundToolCall,
  type PlaygroundChatResponse,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  tool_calls?: PlaygroundToolCall[];
  token_count?: number;
  cost_estimate?: number;
  latency_ms?: number;
  model_used?: string;
  timestamp: Date;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function generateId(): string {
  return crypto.randomUUID?.() ?? Math.random().toString(36).slice(2);
}

function formatCost(cost: number): string {
  if (cost < 0.001) return `$${cost.toFixed(6)}`;
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(3)}`;
}

function formatLatency(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// ---------------------------------------------------------------------------
// Tool Call Card
// ---------------------------------------------------------------------------

function ToolCallCard({ call }: { call: PlaygroundToolCall }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border border-border bg-muted/30 text-xs">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/50"
      >
        <Wrench className="size-3.5 shrink-0 text-amber-500" />
        <span className="font-medium text-foreground">{call.tool_name}</span>
        {call.duration_ms > 0 && (
          <Badge variant="secondary" className="ml-auto text-[10px]">
            {formatLatency(call.duration_ms)}
          </Badge>
        )}
        {expanded ? (
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
        )}
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-border px-3 py-2">
          <div>
            <div className="mb-1 font-medium text-muted-foreground">Input</div>
            <pre className="overflow-x-auto rounded bg-background p-2 text-[11px] leading-relaxed">
              {JSON.stringify(call.tool_input, null, 2)}
            </pre>
          </div>
          <div>
            <div className="mb-1 font-medium text-muted-foreground">Output</div>
            <pre className="overflow-x-auto rounded bg-background p-2 text-[11px] leading-relaxed">
              {JSON.stringify(call.tool_output, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Message Bubble
// ---------------------------------------------------------------------------

function MessageBubble({
  message,
  verbose,
  onSaveEval,
  isSavingEval,
}: {
  message: ChatMessage;
  verbose: boolean;
  onSaveEval: (message: ChatMessage) => void;
  isSavingEval: boolean;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const isUser = message.role === "user";

  return (
    <div className={cn("group flex gap-3", isUser && "flex-row-reverse")}>
      {/* Avatar */}
      <div
        className={cn(
          "flex size-8 shrink-0 items-center justify-center rounded-full",
          isUser
            ? "bg-foreground text-background"
            : "bg-primary/10 text-primary"
        )}
      >
        {isUser ? <User className="size-4" /> : <Bot className="size-4" />}
      </div>

      {/* Content */}
      <div
        className={cn(
          "flex max-w-[75%] flex-col gap-2",
          isUser && "items-end"
        )}
      >
        <div
          className={cn(
            "rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
            isUser
              ? "rounded-tr-md bg-foreground text-background"
              : "rounded-tl-md bg-muted text-foreground"
          )}
        >
          {/* Tool calls (before response text) */}
          {!isUser &&
            message.tool_calls &&
            message.tool_calls.length > 0 &&
            verbose && (
              <div className="mb-3 space-y-2">
                {message.tool_calls.map((call, i) => (
                  <ToolCallCard key={i} call={call} />
                ))}
              </div>
            )}

          {/* Tool calls badge (non-verbose mode) */}
          {!isUser &&
            message.tool_calls &&
            message.tool_calls.length > 0 &&
            !verbose && (
              <div className="mb-2">
                <Badge variant="secondary" className="text-[10px]">
                  <Wrench className="mr-1 size-3" />
                  {message.tool_calls.length} tool call
                  {message.tool_calls.length > 1 ? "s" : ""}
                </Badge>
              </div>
            )}

          <div className="whitespace-pre-wrap">{message.content}</div>
        </div>

        {/* Meta row for assistant messages */}
        {!isUser && (
          <div className="flex items-center gap-2 px-1 text-[11px] text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
            {verbose && message.token_count != null && (
              <span className="flex items-center gap-1">
                <Hash className="size-3" />
                {message.token_count} tokens
              </span>
            )}
            {verbose && message.cost_estimate != null && (
              <span className="flex items-center gap-1">
                <Coins className="size-3" />
                {formatCost(message.cost_estimate)}
              </span>
            )}
            {verbose && message.latency_ms != null && (
              <span className="flex items-center gap-1">
                <Clock className="size-3" />
                {formatLatency(message.latency_ms)}
              </span>
            )}
            {verbose && message.model_used && (
              <Badge variant="outline" className="text-[10px]">
                {message.model_used}
              </Badge>
            )}

            <div className="flex items-center gap-1">
              <button
                onClick={handleCopy}
                className="rounded p-1 transition-colors hover:bg-muted"
                title="Copy message"
              >
                {copied ? (
                  <Check className="size-3 text-green-500" />
                ) : (
                  <Copy className="size-3" />
                )}
              </button>
              <button
                onClick={() => onSaveEval(message)}
                disabled={isSavingEval}
                className="rounded p-1 transition-colors hover:bg-muted disabled:opacity-50"
                title="Save as eval case"
              >
                <Save className="size-3" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Streaming text effect
// ---------------------------------------------------------------------------

function useStreamingText(text: string, isStreaming: boolean): string {
  const [displayed, setDisplayed] = useState("");
  const indexRef = useRef(0);

  useEffect(() => {
    if (!isStreaming) {
      setDisplayed(text);
      indexRef.current = text.length;
      return;
    }

    setDisplayed("");
    indexRef.current = 0;

    const interval = setInterval(() => {
      indexRef.current += Math.floor(Math.random() * 3) + 1;
      if (indexRef.current >= text.length) {
        indexRef.current = text.length;
        clearInterval(interval);
      }
      setDisplayed(text.slice(0, indexRef.current));
    }, 15);

    return () => clearInterval(interval);
  }, [text, isStreaming]);

  return displayed;
}

// ---------------------------------------------------------------------------
// Streaming Message Bubble (used for the currently streaming response)
// ---------------------------------------------------------------------------

function StreamingBubble({
  content,
  toolCalls,
  verbose,
}: {
  content: string;
  toolCalls?: PlaygroundToolCall[];
  verbose: boolean;
}) {
  const displayed = useStreamingText(content, true);

  return (
    <div className="group flex gap-3">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Bot className="size-4" />
      </div>
      <div className="flex max-w-[75%] flex-col gap-2">
        <div className="rounded-2xl rounded-tl-md bg-muted px-4 py-2.5 text-sm leading-relaxed text-foreground">
          {toolCalls && toolCalls.length > 0 && verbose && (
            <div className="mb-3 space-y-2">
              {toolCalls.map((call, i) => (
                <ToolCallCard key={i} call={call} />
              ))}
            </div>
          )}
          <div className="whitespace-pre-wrap">
            {displayed}
            <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-foreground" />
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Playground Page
// ---------------------------------------------------------------------------

export default function PlaygroundPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [selectedAgentId, setSelectedAgentId] = useState<string>("");
  const [modelOverride, setModelOverride] = useState<string>("");
  const [systemPromptOverride, setSystemPromptOverride] = useState<string>("");
  const [promptOverrideOpen, setPromptOverrideOpen] = useState(false);
  const [verbose, setVerbose] = useState(false);
  const [streamingResponse, setStreamingResponse] = useState<PlaygroundChatResponse | null>(null);
  const [agentDropdownOpen, setAgentDropdownOpen] = useState(false);
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const agentDropdownRef = useRef<HTMLDivElement>(null);
  const modelDropdownRef = useRef<HTMLDivElement>(null);

  // Fetch agents for the selector
  const { data: agentsData, isLoading: agentsLoading } = useQuery({
    queryKey: ["playground-agents"],
    queryFn: () => api.agents.list({ per_page: 100 }),
  });

  const agents: Agent[] = agentsData?.data ?? [];
  const selectedAgent = agents.find((a) => a.id === selectedAgentId);

  // Fetch models for override selector
  const { data: modelsData } = useQuery({
    queryKey: ["playground-models"],
    queryFn: () => api.models.list({ page: 1 }),
  });

  const models = modelsData?.data ?? [];

  // Auto-select first agent
  useEffect(() => {
    if (!selectedAgentId && agents.length > 0) {
      setSelectedAgentId(agents[0].id);
    }
  }, [agents, selectedAgentId]);

  // Auto-scroll on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, streamingResponse]);

  // Close dropdowns on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        agentDropdownRef.current &&
        !agentDropdownRef.current.contains(e.target as Node)
      ) {
        setAgentDropdownOpen(false);
      }
      if (
        modelDropdownRef.current &&
        !modelDropdownRef.current.contains(e.target as Node)
      ) {
        setModelDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Chat mutation
  const chatMutation = useMutation({
    mutationFn: async (message: string) => {
      const history: ConversationMessage[] = messages.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const res = await api.playground.chat({
        agent_id: selectedAgentId,
        message,
        model_override: modelOverride || undefined,
        system_prompt_override: systemPromptOverride || undefined,
        conversation_history: history,
      });
      return res.data;
    },
    onSuccess: (data) => {
      // Show streaming effect
      setStreamingResponse(data);

      // After the "streaming" animation duration, add to messages
      const estimatedDuration = Math.min(data.response.length * 15, 3000);
      setTimeout(() => {
        setStreamingResponse(null);
        setMessages((prev) => [
          ...prev,
          {
            id: generateId(),
            role: "assistant",
            content: data.response,
            tool_calls: data.tool_calls,
            token_count: data.token_count,
            cost_estimate: data.cost_estimate,
            latency_ms: data.latency_ms,
            model_used: data.model_used,
            timestamp: new Date(),
          },
        ]);
      }, estimatedDuration);
    },
  });

  // Save eval case mutation
  const evalMutation = useMutation({
    mutationFn: async (assistantMessage: ChatMessage) => {
      const history: ConversationMessage[] = messages
        .filter((m) => m.timestamp <= assistantMessage.timestamp)
        .map((m) => ({
          role: m.role,
          content: m.content,
        }));

      const res = await api.playground.saveEvalCase({
        agent_id: selectedAgentId,
        conversation_history: history,
        assistant_message: assistantMessage.content,
        model_used: assistantMessage.model_used ?? "",
        tags: [],
      });
      return res.data;
    },
  });

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || !selectedAgentId || chatMutation.isPending) return;

    const userMessage: ChatMessage = {
      id: generateId(),
      role: "user",
      content: trimmed,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    chatMutation.mutate(trimmed);

    // Reset textarea height
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }
  }, [input, selectedAgentId, chatMutation]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleClear = () => {
    setMessages([]);
    setStreamingResponse(null);
    chatMutation.reset();
  };

  // Auto-resize textarea
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  const isLoading = chatMutation.isPending || streamingResponse !== null;

  // Totals
  const totalTokens = messages
    .filter((m) => m.role === "assistant")
    .reduce((sum, m) => sum + (m.token_count ?? 0), 0);
  const totalCost = messages
    .filter((m) => m.role === "assistant")
    .reduce((sum, m) => sum + (m.cost_estimate ?? 0), 0);

  return (
    <div className="flex h-full flex-col">
      {/* Top Bar */}
      <div className="flex shrink-0 items-center gap-3 border-b border-border px-6 py-3">
        {/* Agent Selector */}
        <div ref={agentDropdownRef} className="relative">
          <button
            onClick={() => setAgentDropdownOpen(!agentDropdownOpen)}
            className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-1.5 text-sm transition-colors hover:bg-muted"
          >
            <Bot className="size-4 text-muted-foreground" />
            <span className="max-w-[200px] truncate">
              {agentsLoading
                ? "Loading..."
                : selectedAgent
                  ? selectedAgent.name
                  : "Select agent"}
            </span>
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </button>
          {agentDropdownOpen && (
            <div className="absolute left-0 top-full z-50 mt-1 max-h-64 w-72 overflow-y-auto rounded-lg border border-border bg-card shadow-lg">
              {agents.length === 0 ? (
                <div className="px-3 py-4 text-center text-xs text-muted-foreground">
                  No agents found
                </div>
              ) : (
                agents.map((agent) => (
                  <button
                    key={agent.id}
                    onClick={() => {
                      setSelectedAgentId(agent.id);
                      setAgentDropdownOpen(false);
                    }}
                    className={cn(
                      "flex w-full flex-col gap-0.5 px-3 py-2 text-left transition-colors hover:bg-muted",
                      agent.id === selectedAgentId && "bg-accent"
                    )}
                  >
                    <div className="flex items-center gap-2 text-sm font-medium">
                      {agent.name}
                      <Badge variant="secondary" className="text-[10px]">
                        {agent.framework}
                      </Badge>
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {agent.description || "No description"}
                    </div>
                  </button>
                ))
              )}
            </div>
          )}
        </div>

        {/* Model Override */}
        <div ref={modelDropdownRef} className="relative">
          <button
            onClick={() => setModelDropdownOpen(!modelDropdownOpen)}
            className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-1.5 text-sm transition-colors hover:bg-muted"
          >
            <Settings2 className="size-3.5 text-muted-foreground" />
            <span className="max-w-[160px] truncate text-xs">
              {modelOverride || "Default model"}
            </span>
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </button>
          {modelDropdownOpen && (
            <div className="absolute left-0 top-full z-50 mt-1 max-h-64 w-64 overflow-y-auto rounded-lg border border-border bg-card shadow-lg">
              <button
                onClick={() => {
                  setModelOverride("");
                  setModelDropdownOpen(false);
                }}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-muted",
                  !modelOverride && "bg-accent"
                )}
              >
                <Zap className="size-3.5 text-muted-foreground" />
                Default (agent config)
              </button>
              {models.map((model) => (
                <button
                  key={model.id}
                  onClick={() => {
                    setModelOverride(model.name);
                    setModelDropdownOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-muted",
                    modelOverride === model.name && "bg-accent"
                  )}
                >
                  <span className="truncate">{model.name}</span>
                  <Badge variant="outline" className="ml-auto shrink-0 text-[10px]">
                    {model.provider}
                  </Badge>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex-1" />

        {/* Session stats */}
        {messages.length > 0 && (
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Hash className="size-3" />
              {totalTokens.toLocaleString()} tokens
            </span>
            <span className="flex items-center gap-1">
              <Coins className="size-3" />
              {formatCost(totalCost)}
            </span>
          </div>
        )}

        {/* Prompt Override toggle */}
        <Button
          variant={promptOverrideOpen ? "secondary" : "ghost"}
          size="sm"
          onClick={() => setPromptOverrideOpen(!promptOverrideOpen)}
          title={promptOverrideOpen ? "Hide prompt override" : "Override system prompt"}
        >
          <FileText className="size-3.5" />
          <span className="ml-1 text-xs">Prompt</span>
          {systemPromptOverride && (
            <span className="ml-1 size-1.5 rounded-full bg-primary" />
          )}
        </Button>

        {/* Verbose toggle */}
        <Button
          variant={verbose ? "secondary" : "ghost"}
          size="sm"
          onClick={() => setVerbose(!verbose)}
          title={verbose ? "Hide details" : "Show details"}
        >
          {verbose ? (
            <Eye className="size-3.5" />
          ) : (
            <EyeOff className="size-3.5" />
          )}
          <span className="ml-1 text-xs">Verbose</span>
        </Button>

        {/* Clear button */}
        <Button
          variant="ghost"
          size="sm"
          onClick={handleClear}
          disabled={messages.length === 0 && !streamingResponse}
          title="Clear conversation"
        >
          <Trash2 className="size-3.5" />
        </Button>
      </div>

      {/* Prompt Override Panel (collapsible) */}
      {promptOverrideOpen && (
        <div className="shrink-0 border-b border-border bg-muted/20 px-6 py-3">
          <div className="mx-auto max-w-3xl">
            <div className="mb-1.5 flex items-center justify-between">
              <label className="text-xs font-medium text-muted-foreground">
                System Prompt Override
              </label>
              {systemPromptOverride && (
                <button
                  onClick={() => setSystemPromptOverride("")}
                  className="text-[10px] text-muted-foreground transition-colors hover:text-foreground"
                >
                  Clear
                </button>
              )}
            </div>
            <textarea
              value={systemPromptOverride}
              onChange={(e) => setSystemPromptOverride(e.target.value)}
              placeholder="Enter a custom system prompt to override the agent's default for this session..."
              rows={3}
              className="w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-xs outline-none transition-colors placeholder:text-muted-foreground focus:border-ring focus:ring-2 focus:ring-ring/20"
            />
            <p className="mt-1 text-[10px] text-muted-foreground">
              This overrides the agent&apos;s configured system prompt for the current session only.
            </p>
          </div>
        </div>
      )}

      {/* Chat Area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
        {messages.length === 0 && !streamingResponse && (
          <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
            <div className="flex size-16 items-center justify-center rounded-2xl bg-primary/10">
              <Bot className="size-8 text-primary" />
            </div>
            <div>
              <h2 className="text-lg font-semibold">Agent Playground</h2>
              <p className="mt-1 max-w-md text-sm text-muted-foreground">
                Select an agent and start chatting to test its behavior.
                Tool calls, token counts, and costs are tracked per message.
              </p>
            </div>
            {selectedAgent && (
              <div className="rounded-lg border border-border bg-muted/30 px-4 py-3 text-sm">
                <div className="flex items-center gap-2">
                  <Bot className="size-4 text-primary" />
                  <span className="font-medium">{selectedAgent.name}</span>
                  <Badge variant="secondary" className="text-[10px]">
                    {selectedAgent.framework}
                  </Badge>
                  <Badge variant="outline" className="text-[10px]">
                    {selectedAgent.model_primary}
                  </Badge>
                </div>
                {selectedAgent.description && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {selectedAgent.description}
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        <div className="mx-auto max-w-3xl space-y-6">
          {messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              message={msg}
              verbose={verbose}
              onSaveEval={(m) => evalMutation.mutate(m)}
              isSavingEval={evalMutation.isPending}
            />
          ))}

          {/* Streaming response */}
          {streamingResponse && (
            <StreamingBubble
              content={streamingResponse.response}
              toolCalls={streamingResponse.tool_calls}
              verbose={verbose}
            />
          )}

          {/* Loading indicator (before response arrives) */}
          {chatMutation.isPending && !streamingResponse && (
            <div className="flex gap-3">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                <Bot className="size-4" />
              </div>
              <div className="flex items-center gap-2 rounded-2xl rounded-tl-md bg-muted px-4 py-3">
                <Loader2 className="size-4 animate-spin text-muted-foreground" />
                <span className="text-sm text-muted-foreground">Thinking...</span>
              </div>
            </div>
          )}

          {/* Error */}
          {chatMutation.isError && (
            <div className="mx-auto max-w-md rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-center text-sm text-destructive">
              {chatMutation.error.message}
            </div>
          )}
        </div>
      </div>

      {/* Input Area */}
      <div className="shrink-0 border-t border-border bg-background px-6 py-4">
        <div className="mx-auto flex max-w-3xl items-end gap-3">
          <div className="relative flex-1">
            <textarea
              ref={inputRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder={
                selectedAgentId
                  ? "Type a message... (Shift+Enter for new line)"
                  : "Select an agent to start chatting"
              }
              disabled={!selectedAgentId || isLoading}
              rows={1}
              className="w-full resize-none rounded-xl border border-border bg-muted/30 px-4 py-3 pr-12 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-ring focus:ring-2 focus:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>
          <Button
            onClick={handleSend}
            disabled={!input.trim() || !selectedAgentId || isLoading}
            size="lg"
            className="shrink-0"
          >
            {isLoading ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Send className="size-4" />
            )}
          </Button>
        </div>

        {/* Eval save success toast (inline) */}
        {evalMutation.isSuccess && (
          <div className="mx-auto mt-2 max-w-3xl text-center text-xs text-green-600 dark:text-green-400">
            <Check className="mr-1 inline size-3" />
            Eval case saved successfully
          </div>
        )}
      </div>
    </div>
  );
}
