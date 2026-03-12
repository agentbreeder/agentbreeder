import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Plus,
  Trash2,
  Search,
  Database,
  HardDrive,
  MessageSquare,
  Clock,
  Loader2,
  Brain,
  ChevronRight,
  User,
  Bot as BotIcon,
  AlertCircle,
  X,
} from "lucide-react";
import { api, type MemoryConfig, type ConversationSummary, type MemoryStats, type MemorySearchHit } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

// ---------------------------------------------------------------------------
// Create Config Dialog
// ---------------------------------------------------------------------------

function CreateConfigDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [backendType, setBackendType] = useState("in_memory");
  const [memoryType, setMemoryType] = useState("buffer_window");
  const [maxMessages, setMaxMessages] = useState(100);
  const [namespacePattern, setNamespacePattern] = useState("{agent_id}:{session_id}");
  const [scope, setScope] = useState("agent");
  const [description, setDescription] = useState("");

  const createMutation = useMutation({
    mutationFn: () =>
      api.memory.createConfig({
        name,
        backend_type: backendType,
        memory_type: memoryType,
        max_messages: maxMessages,
        namespace_pattern: namespacePattern,
        scope,
        description,
      }),
    onSuccess: () => {
      onCreated();
      onClose();
      setName("");
      setDescription("");
    },
  });

  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="fixed inset-x-0 top-[10%] z-50 mx-auto w-full max-w-lg">
        <div className="rounded-xl border border-border bg-card shadow-2xl">
          <div className="flex items-center justify-between border-b border-border px-5 py-3">
            <h2 className="text-sm font-semibold">New Memory Configuration</h2>
            <button onClick={onClose} className="rounded p-1 text-muted-foreground hover:text-foreground">
              <X className="size-4" />
            </button>
          </div>
          <div className="space-y-4 p-5">
            <div>
              <label className="mb-1 block text-xs font-medium">Name</label>
              <Input
                placeholder="customer-support-memory"
                value={name}
                onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9-_]/g, "-"))}
                className="h-8 text-xs font-mono"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium">Backend Type</label>
                <select
                  value={backendType}
                  onChange={(e) => setBackendType(e.target.value)}
                  className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
                >
                  <option value="in_memory">In-Memory</option>
                  <option value="postgresql">PostgreSQL</option>
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium">Memory Type</label>
                <select
                  value={memoryType}
                  onChange={(e) => setMemoryType(e.target.value)}
                  className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
                >
                  <option value="buffer_window">Buffer Window</option>
                  <option value="buffer">Buffer (Full)</option>
                </select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium">Max Messages</label>
                <Input
                  type="number"
                  value={maxMessages}
                  onChange={(e) => setMaxMessages(Number(e.target.value))}
                  className="h-8 text-xs"
                  min={1}
                  max={100000}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium">Scope</label>
                <select
                  value={scope}
                  onChange={(e) => setScope(e.target.value)}
                  className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
                >
                  <option value="agent">Agent</option>
                  <option value="team">Team</option>
                  <option value="global">Global</option>
                </select>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">Namespace Pattern</label>
              <Input
                value={namespacePattern}
                onChange={(e) => setNamespacePattern(e.target.value)}
                className="h-8 text-xs font-mono"
              />
              <p className="mt-1 text-[10px] text-muted-foreground">
                Variables: {"{agent_id}"}, {"{session_id}"}, {"{user_id}"}
              </p>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">Description</label>
              <textarea
                placeholder="What is this memory used for?"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
                className="w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-xs outline-none focus:border-ring focus:ring-2 focus:ring-ring/50"
              />
            </div>
            {createMutation.error && (
              <div className="flex items-center gap-1.5 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                <AlertCircle className="size-3 shrink-0" />
                {(createMutation.error as Error).message}
              </div>
            )}
          </div>
          <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
            <button
              onClick={onClose}
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium transition-colors hover:bg-accent"
            >
              Cancel
            </button>
            <button
              onClick={() => createMutation.mutate()}
              disabled={!name.trim() || createMutation.isPending}
              className="flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background transition-colors hover:bg-foreground/90 disabled:opacity-50"
            >
              {createMutation.isPending && <Loader2 className="size-3 animate-spin" />}
              Create
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Conversation Viewer
// ---------------------------------------------------------------------------

function ConversationViewer({
  configId,
  sessionId,
  onBack,
}: {
  configId: string;
  sessionId: string;
  onBack: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["memory-conversation", configId, sessionId],
    queryFn: () => api.memory.getConversation(configId, sessionId),
  });
  const messages = data?.data ?? [];

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-border px-4 py-3">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" />
          Back
        </button>
        <span className="text-xs text-muted-foreground">/</span>
        <span className="text-xs font-medium font-mono">{sessionId}</span>
        <Badge variant="outline" className="text-[10px]">
          {messages.length} messages
        </Badge>
      </div>

      {isLoading ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      ) : messages.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center text-muted-foreground">
          <MessageSquare className="mb-2 size-8 opacity-30" />
          <p className="text-xs">No messages in this conversation</p>
        </div>
      ) : (
        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={cn(
                "flex gap-3",
                msg.role === "assistant" ? "flex-row" : "flex-row-reverse"
              )}
            >
              <div
                className={cn(
                  "flex size-7 shrink-0 items-center justify-center rounded-full",
                  msg.role === "user"
                    ? "bg-blue-500/10 text-blue-500"
                    : msg.role === "assistant"
                    ? "bg-emerald-500/10 text-emerald-500"
                    : msg.role === "system"
                    ? "bg-amber-500/10 text-amber-500"
                    : "bg-purple-500/10 text-purple-500"
                )}
              >
                {msg.role === "user" ? (
                  <User className="size-3.5" />
                ) : msg.role === "assistant" ? (
                  <BotIcon className="size-3.5" />
                ) : (
                  <Database className="size-3.5" />
                )}
              </div>
              <div
                className={cn(
                  "max-w-[75%] rounded-lg border border-border p-3",
                  msg.role === "user"
                    ? "bg-blue-500/5"
                    : msg.role === "assistant"
                    ? "bg-emerald-500/5"
                    : "bg-muted/40"
                )}
              >
                <div className="mb-1 flex items-center gap-2">
                  <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                    {msg.role}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    {formatDate(msg.timestamp)}
                  </span>
                </div>
                <p className="whitespace-pre-wrap text-xs leading-relaxed">
                  {msg.content}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Config Detail Panel
// ---------------------------------------------------------------------------

function ConfigDetail({
  config,
  onBack,
}: {
  config: MemoryConfig;
  onBack: () => void;
}) {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<"conversations" | "search">("conversations");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchTrigger, setSearchTrigger] = useState("");
  const [viewingSession, setViewingSession] = useState<string | null>(null);

  // Stats
  const { data: statsData } = useQuery({
    queryKey: ["memory-stats", config.id],
    queryFn: () => api.memory.getStats(config.id),
  });
  const stats: MemoryStats | null = statsData?.data ?? null;

  // Conversations
  const { data: convosData, isLoading: loadingConvos } = useQuery({
    queryKey: ["memory-conversations", config.id],
    queryFn: () => api.memory.listConversations(config.id),
  });
  const conversations: ConversationSummary[] = convosData?.data ?? [];

  // Search
  const { data: searchData, isLoading: searchLoading } = useQuery({
    queryKey: ["memory-search", config.id, searchTrigger],
    queryFn: () => api.memory.search(config.id, searchTrigger),
    enabled: !!searchTrigger,
  });
  const searchResults: MemorySearchHit[] = searchData?.data ?? [];

  // Delete conversation
  const deleteMutation = useMutation({
    mutationFn: (sessionId: string) =>
      api.memory.deleteConversations(config.id, { session_id: sessionId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memory-conversations", config.id] });
      queryClient.invalidateQueries({ queryKey: ["memory-stats", config.id] });
    },
  });

  const handleSearch = useCallback(() => {
    if (searchQuery.trim()) {
      setSearchTrigger(searchQuery.trim());
    }
  }, [searchQuery]);

  if (viewingSession) {
    return (
      <ConversationViewer
        configId={config.id}
        sessionId={viewingSession}
        onBack={() => setViewingSession(null)}
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-6 py-3">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" />
            Memory Configs
          </button>
          <span className="text-xs text-muted-foreground">/</span>
          <h1 className="text-sm font-semibold">{config.name}</h1>
          <Badge variant="outline" className="text-[10px]">
            {config.backend_type}
          </Badge>
          <Badge variant="secondary" className="text-[10px]">
            {config.memory_type}
          </Badge>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-4 gap-4 border-b border-border px-6 py-4">
        <div>
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            Messages
          </p>
          <p className="text-lg font-semibold">{stats?.message_count ?? 0}</p>
        </div>
        <div>
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            Sessions
          </p>
          <p className="text-lg font-semibold">{stats?.session_count ?? 0}</p>
        </div>
        <div>
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            Storage
          </p>
          <p className="text-lg font-semibold">
            {formatBytes(stats?.storage_size_bytes ?? 0)}
          </p>
        </div>
        <div>
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            Linked Agents
          </p>
          <p className="text-lg font-semibold">{stats?.linked_agent_count ?? 0}</p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 border-b border-border px-6">
        <button
          onClick={() => setActiveTab("conversations")}
          className={cn(
            "border-b-2 px-3 py-2 text-xs font-medium transition-colors",
            activeTab === "conversations"
              ? "border-foreground text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          )}
        >
          <MessageSquare className="mr-1.5 inline size-3" />
          Conversations
        </button>
        <button
          onClick={() => setActiveTab("search")}
          className={cn(
            "border-b-2 px-3 py-2 text-xs font-medium transition-colors",
            activeTab === "search"
              ? "border-foreground text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          )}
        >
          <Search className="mr-1.5 inline size-3" />
          Search
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === "conversations" ? (
          <div className="p-6">
            {loadingConvos ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            ) : conversations.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <MessageSquare className="mb-2 size-8 opacity-30" />
                <p className="text-xs">No conversations stored yet</p>
              </div>
            ) : (
              <div className="space-y-2">
                {conversations.map((convo) => (
                  <div
                    key={convo.session_id}
                    className="flex items-center justify-between rounded-lg border border-border p-3 transition-colors hover:bg-accent/30"
                  >
                    <button
                      onClick={() => setViewingSession(convo.session_id)}
                      className="flex flex-1 items-center gap-3 text-left"
                    >
                      <div className="flex size-8 items-center justify-center rounded-full bg-muted">
                        <MessageSquare className="size-3.5 text-muted-foreground" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-xs font-medium font-mono">
                          {convo.session_id}
                        </p>
                        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                          <span>{convo.message_count} messages</span>
                          {convo.agent_id && (
                            <>
                              <span className="text-border">|</span>
                              <span>agent: {convo.agent_id}</span>
                            </>
                          )}
                          {convo.last_message_at && (
                            <>
                              <span className="text-border">|</span>
                              <Clock className="size-2.5" />
                              <span>{formatDate(convo.last_message_at)}</span>
                            </>
                          )}
                        </div>
                      </div>
                      <ChevronRight className="size-3.5 text-muted-foreground" />
                    </button>
                    <button
                      onClick={() => deleteMutation.mutate(convo.session_id)}
                      className="ml-2 rounded p-1.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                      title="Delete conversation"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="p-6">
            <div className="mb-4 flex items-center gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search across all stored messages..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                  className="h-8 pl-9 text-xs"
                />
              </div>
              <button
                onClick={handleSearch}
                disabled={!searchQuery.trim() || searchLoading}
                className="flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background transition-colors hover:bg-foreground/90 disabled:opacity-50"
              >
                {searchLoading ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <Search className="size-3" />
                )}
                Search
              </button>
            </div>

            {searchResults.length > 0 ? (
              <div className="space-y-2">
                {searchResults.map((hit, i) => (
                  <div key={i} className="rounded-lg border border-border p-3">
                    <div className="mb-1 flex items-center gap-2 text-[10px] text-muted-foreground">
                      <Badge
                        variant="outline"
                        className={cn(
                          "text-[9px]",
                          hit.message.role === "user"
                            ? "border-blue-500/30 text-blue-500"
                            : "border-emerald-500/30 text-emerald-500"
                        )}
                      >
                        {hit.message.role}
                      </Badge>
                      <span className="font-mono">{hit.message.session_id}</span>
                      <span>{formatDate(hit.message.timestamp)}</span>
                    </div>
                    <p className="text-xs leading-relaxed">{hit.highlight}</p>
                  </div>
                ))}
              </div>
            ) : searchTrigger ? (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <Search className="mb-2 size-6 opacity-30" />
                <p className="text-xs">No results found for &quot;{searchTrigger}&quot;</p>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <Search className="mb-2 size-6 opacity-30" />
                <p className="text-xs">Enter a query to search across stored conversations</p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function MemoryBuilderPage() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedConfigId, setSelectedConfigId] = useState<string | null>(null);

  // Fetch configs
  const { data, isLoading } = useQuery({
    queryKey: ["memory-configs"],
    queryFn: () => api.memory.listConfigs(),
  });
  const configs: MemoryConfig[] = data?.data ?? [];
  const selectedConfig = configs.find((c) => c.id === selectedConfigId) ?? null;

  // Delete config
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.memory.deleteConfig(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memory-configs"] });
      setSelectedConfigId(null);
    },
  });

  if (selectedConfig) {
    return (
      <ConfigDetail
        config={selectedConfig}
        onBack={() => setSelectedConfigId(null)}
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold">Memory</h1>
          <p className="text-xs text-muted-foreground">
            Manage memory backends for agent conversations
          </p>
        </div>
        <button
          onClick={() => setCreateOpen(true)}
          className="flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background transition-colors hover:bg-foreground/90"
        >
          <Plus className="size-3" />
          New Memory Config
        </button>
      </div>

      <CreateConfigDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => queryClient.invalidateQueries({ queryKey: ["memory-configs"] })}
      />

      {/* List */}
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : configs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
            <Brain className="mb-3 size-10 opacity-30" />
            <p className="mb-1 text-sm font-medium">No memory configurations yet</p>
            <p className="mb-4 text-xs">
              Create a memory backend to store agent conversations
            </p>
            <button
              onClick={() => setCreateOpen(true)}
              className="flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background transition-colors hover:bg-foreground/90"
            >
              <Plus className="size-3" />
              Create First Config
            </button>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {configs.map((config) => (
              <MemoryConfigCard
                key={config.id}
                config={config}
                onSelect={() => setSelectedConfigId(config.id)}
                onDelete={() => deleteMutation.mutate(config.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Config Card
// ---------------------------------------------------------------------------

function MemoryConfigCard({
  config,
  onSelect,
  onDelete,
}: {
  config: MemoryConfig;
  onSelect: () => void;
  onDelete: () => void;
}) {
  const { data: statsData } = useQuery({
    queryKey: ["memory-stats", config.id],
    queryFn: () => api.memory.getStats(config.id),
  });
  const stats: MemoryStats | null = statsData?.data ?? null;

  return (
    <div
      className="group relative rounded-lg border border-border p-4 transition-colors hover:border-foreground/20 hover:bg-accent/30 cursor-pointer"
      onClick={onSelect}
    >
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        className="absolute right-2 top-2 rounded p-1 text-muted-foreground opacity-0 transition-all group-hover:opacity-100 hover:bg-destructive/10 hover:text-destructive"
      >
        <Trash2 className="size-3" />
      </button>

      <div className="mb-3 flex items-center gap-2">
        <div className="flex size-8 items-center justify-center rounded-lg bg-muted">
          {config.backend_type === "postgresql" ? (
            <Database className="size-4 text-muted-foreground" />
          ) : (
            <HardDrive className="size-4 text-muted-foreground" />
          )}
        </div>
        <div>
          <p className="text-sm font-medium">{config.name}</p>
          <div className="flex items-center gap-1.5">
            <Badge variant="outline" className="text-[9px]">
              {config.backend_type}
            </Badge>
            <Badge variant="secondary" className="text-[9px]">
              {config.memory_type}
            </Badge>
          </div>
        </div>
      </div>

      {config.description && (
        <p className="mb-3 text-xs text-muted-foreground line-clamp-2">
          {config.description}
        </p>
      )}

      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <p className="text-sm font-semibold">{stats?.message_count ?? 0}</p>
          <p className="text-[10px] text-muted-foreground">Messages</p>
        </div>
        <div>
          <p className="text-sm font-semibold">{stats?.session_count ?? 0}</p>
          <p className="text-[10px] text-muted-foreground">Sessions</p>
        </div>
        <div>
          <p className="text-sm font-semibold">
            {formatBytes(stats?.storage_size_bytes ?? 0)}
          </p>
          <p className="text-[10px] text-muted-foreground">Storage</p>
        </div>
      </div>

      <div className="mt-3 flex items-center justify-between text-[10px] text-muted-foreground">
        <span>max {config.max_messages} msgs</span>
        <span>scope: {config.scope}</span>
      </div>
    </div>
  );
}
