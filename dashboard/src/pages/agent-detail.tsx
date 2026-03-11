import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Circle,
  ExternalLink,
  Copy,
  Check,
  Clock,
  User,
  Users,
  Tag,
  Activity,
  Target,
  Cpu,
  Wrench,
  MessageSquare,
  BookOpen,
  ChevronRight,
  ChevronDown,
  FileCode2,
  GitFork,
  Clipboard,
} from "lucide-react";
import { api, type Agent, type AgentStatus, type DeployJob } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { DeployPipeline } from "@/components/deploy-pipeline";
import { cn } from "@/lib/utils";
import { jsonToYaml, highlightYaml } from "@/lib/yaml";
import { useState, useMemo } from "react";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STATUS_MAP: Record<AgentStatus, { label: string; color: string; bg: string; dot: string }> = {
  running: {
    label: "Running",
    color: "text-emerald-600 dark:text-emerald-400",
    bg: "bg-emerald-500/10",
    dot: "bg-emerald-500",
  },
  deploying: {
    label: "Deploying",
    color: "text-amber-600 dark:text-amber-400",
    bg: "bg-amber-500/10",
    dot: "bg-amber-500",
  },
  stopped: {
    label: "Stopped",
    color: "text-muted-foreground",
    bg: "bg-muted",
    dot: "bg-muted-foreground",
  },
  failed: {
    label: "Failed",
    color: "text-destructive",
    bg: "bg-destructive/10",
    dot: "bg-red-500",
  },
};

const FRAMEWORK_COLORS: Record<string, string> = {
  langgraph: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/20",
  crewai: "bg-teal-500/10 text-teal-600 dark:text-teal-400 border-teal-500/20",
  claude_sdk: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  openai_agents: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  google_adk: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  custom: "bg-gray-500/10 text-gray-600 dark:text-gray-400 border-gray-500/20",
};

const TAG_COLORS = [
  "bg-sky-500/10 text-sky-700 dark:text-sky-400",
  "bg-violet-500/10 text-violet-700 dark:text-violet-400",
  "bg-rose-500/10 text-rose-700 dark:text-rose-400",
  "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  "bg-teal-500/10 text-teal-700 dark:text-teal-400",
  "bg-fuchsia-500/10 text-fuchsia-700 dark:text-fuchsia-400",
];

const TAB_TRIGGER_CLASS =
  "rounded-none border-b-2 border-transparent px-3 py-1.5 text-xs data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:shadow-none";

// ---------------------------------------------------------------------------
// Shared small components
// ---------------------------------------------------------------------------

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button
      onClick={copy}
      className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
    </button>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <dt className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function relativeTime(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMs = now - then;
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  const diffMon = Math.floor(diffDay / 30);
  if (diffMon < 12) return `${diffMon}mo ago`;
  return `${Math.floor(diffMon / 12)}y ago`;
}

// ---------------------------------------------------------------------------
// Agent Header (enhanced)
// ---------------------------------------------------------------------------

function AgentHeader({ agent }: { agent: Agent }) {
  const s = STATUS_MAP[agent.status];
  return (
    <div className="flex items-start justify-between">
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">{agent.name}</h1>
          <span className="text-sm text-muted-foreground">v{agent.version}</span>
          <div
            className={cn(
              "flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium",
              s.bg,
              s.color
            )}
          >
            <span className="relative flex size-2">
              <span
                className={cn(
                  "absolute inline-flex size-full rounded-full opacity-75",
                  s.dot,
                  agent.status === "running" && "animate-ping",
                  agent.status === "deploying" && "animate-ping"
                )}
              />
              <span className={cn("relative inline-flex size-2 rounded-full", s.dot)} />
            </span>
            {s.label}
          </div>
          <Badge
            variant="outline"
            className={cn(
              "text-[10px] border",
              FRAMEWORK_COLORS[agent.framework] ?? FRAMEWORK_COLORS.custom
            )}
          >
            <Cpu className="mr-0.5 size-2.5" />
            {agent.framework}
          </Badge>
        </div>
        {agent.description && (
          <p className="max-w-2xl text-sm text-muted-foreground">{agent.description}</p>
        )}
      </div>
      <CloneAgentDialog agent={agent} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Clone Agent Dialog
// ---------------------------------------------------------------------------

function CloneAgentDialog({ agent }: { agent: Agent }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(`${agent.name}-copy`);
  const [version, setVersion] = useState("1.0.0");
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => api.agents.clone(agent.id, { name, version }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      setOpen(false);
      navigate(`/agents/${data.data.id}`);
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button variant="outline" size="sm">
            <GitFork className="size-3" />
            Clone
          </Button>
        }
      />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Clone Agent</DialogTitle>
          <DialogDescription>
            Create a copy of <strong>{agent.name}</strong> with a new name.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 py-2">
          <div className="space-y-1.5">
            <label htmlFor="clone-name" className="text-xs font-medium text-muted-foreground">
              Agent Name
            </label>
            <Input
              id="clone-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-agent-copy"
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="clone-version" className="text-xs font-medium text-muted-foreground">
              Version
            </label>
            <Input
              id="clone-version"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              placeholder="1.0.0"
            />
          </div>
          {mutation.isError && (
            <p className="text-xs text-destructive">
              {(mutation.error as Error).message}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!name.trim() || mutation.isPending}
          >
            {mutation.isPending ? "Cloning..." : "Clone Agent"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Overview Tab (enhanced)
// ---------------------------------------------------------------------------

function OverviewTab({ agent }: { agent: Agent }) {
  return (
    <div className="grid gap-8 pt-6 md:grid-cols-2">
      {/* Left column */}
      <div className="space-y-6">
        <div className="rounded-lg border border-border p-4">
          <h3 className="mb-4 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Configuration
          </h3>
          <dl className="space-y-4">
            <Field label="Framework">
              <Badge
                variant="outline"
                className={cn(
                  "text-xs border",
                  FRAMEWORK_COLORS[agent.framework] ?? FRAMEWORK_COLORS.custom
                )}
              >
                <Cpu className="mr-0.5 size-2.5" />
                {agent.framework}
              </Badge>
            </Field>
            <Field label="Primary Model">
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                {agent.model_primary}
              </code>
            </Field>
            {agent.model_fallback && (
              <Field label="Fallback Model">
                <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                  {agent.model_fallback}
                </code>
              </Field>
            )}
            {agent.tags.length > 0 && (
              <Field label="Tags">
                <div className="flex flex-wrap gap-1">
                  {agent.tags.map((tag, i) => (
                    <span
                      key={tag}
                      className={cn(
                        "flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium",
                        TAG_COLORS[i % TAG_COLORS.length]
                      )}
                    >
                      <Tag className="size-2.5" />
                      {tag}
                    </span>
                  ))}
                </div>
              </Field>
            )}
          </dl>
        </div>
      </div>

      {/* Right column */}
      <div className="space-y-6">
        <div className="rounded-lg border border-border p-4">
          <h3 className="mb-4 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Metadata
          </h3>
          <dl className="space-y-4">
            <Field label="Owner">
              <span className="flex items-center gap-1.5 text-sm">
                <User className="size-3 text-muted-foreground" />
                {agent.owner}
              </span>
            </Field>
            <Field label="Team">
              <span className="flex items-center gap-1.5 text-sm">
                <Users className="size-3 text-muted-foreground" />
                {agent.team}
              </span>
            </Field>
            <Field label="Created">
              <span className="flex items-center gap-1.5 text-sm">
                <Clock className="size-3 text-muted-foreground" />
                {new Date(agent.created_at).toLocaleDateString("en-US", {
                  year: "numeric",
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </Field>
            <Field label="Last Updated">
              <span className="flex items-center gap-1.5 text-sm">
                <Clock className="size-3 text-muted-foreground" />
                <span>
                  {new Date(agent.updated_at).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
                <span className="text-xs text-muted-foreground">
                  ({relativeTime(agent.updated_at)})
                </span>
              </span>
            </Field>
          </dl>
        </div>

        {agent.endpoint_url && (
          <div className="rounded-lg border border-border p-4">
            <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Endpoint
            </h3>
            <div className="flex items-center gap-2 rounded-md bg-muted px-3 py-2">
              <code className="flex-1 truncate font-mono text-xs">
                {agent.endpoint_url}
              </code>
              <CopyButton text={agent.endpoint_url} />
              <a
                href={agent.endpoint_url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded p-1 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
              >
                <ExternalLink className="size-3" />
              </a>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Configuration (YAML Viewer) Tab
// ---------------------------------------------------------------------------

function ConfigurationTab({ agent }: { agent: Agent }) {
  const [copied, setCopied] = useState(false);

  const yamlStr = useMemo(() => {
    const snapshot = agent.config_snapshot;
    if (!snapshot || Object.keys(snapshot).length === 0) return null;
    return jsonToYaml(snapshot);
  }, [agent.config_snapshot]);

  const highlighted = useMemo(() => {
    if (!yamlStr) return null;
    return highlightYaml(yamlStr);
  }, [yamlStr]);

  if (!yamlStr || !highlighted) {
    return (
      <div className="flex flex-col items-center py-16 text-center">
        <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-border">
          <FileCode2 className="size-5 text-muted-foreground" />
        </div>
        <h3 className="text-sm font-medium">No configuration snapshot</h3>
        <p className="mt-1 max-w-xs text-xs text-muted-foreground">
          This agent does not have a stored configuration snapshot.
        </p>
      </div>
    );
  }

  const lines = highlighted.split("\n");

  const copyAll = () => {
    navigator.clipboard.writeText(yamlStr);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="pt-6">
      <div className="relative overflow-hidden rounded-lg border border-border bg-[#fafafa] dark:bg-[#0d1117]">
        {/* Header bar */}
        <div className="flex items-center justify-between border-b border-border bg-muted/30 px-4 py-2">
          <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <FileCode2 className="size-3.5" />
            agenthub.yaml
          </span>
          <button
            onClick={copyAll}
            className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {copied ? (
              <>
                <Check className="size-3" /> Copied
              </>
            ) : (
              <>
                <Clipboard className="size-3" /> Copy
              </>
            )}
          </button>
        </div>

        {/* Code block */}
        <div className="overflow-x-auto">
          <pre className="py-3 font-mono text-[13px] leading-6">
            <code>
              {lines.map((line, i) => (
                <div key={i} className="flex hover:bg-muted/30">
                  <span className="w-12 shrink-0 select-none pr-3 text-right text-xs leading-6 text-muted-foreground/50">
                    {i + 1}
                  </span>
                  <span
                    className="flex-1 pr-4"
                    dangerouslySetInnerHTML={{ __html: line || "&nbsp;" }}
                  />
                </div>
              ))}
            </code>
          </pre>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dependency Graph Tab
// ---------------------------------------------------------------------------

interface DependencyNode {
  type: "model" | "tool" | "prompt" | "knowledge_base";
  name: string;
  detail?: string;
  isRef: boolean;
}

interface DependencyGroup {
  type: "model" | "tool" | "prompt" | "knowledge_base";
  label: string;
  icon: React.ReactNode;
  colorClass: string;
  badgeClass: string;
  items: DependencyNode[];
}

function extractDependencies(agent: Agent): DependencyGroup[] {
  const snapshot = agent.config_snapshot ?? {};
  const groups: DependencyGroup[] = [];

  // Models
  const models: DependencyNode[] = [];
  models.push({
    type: "model",
    name: agent.model_primary,
    detail: "primary",
    isRef: false,
  });
  if (agent.model_fallback) {
    models.push({
      type: "model",
      name: agent.model_fallback,
      detail: "fallback",
      isRef: false,
    });
  }
  groups.push({
    type: "model",
    label: "Models",
    icon: <Cpu className="size-3.5" />,
    colorClass: "text-blue-600 dark:text-blue-400",
    badgeClass: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
    items: models,
  });

  // Tools
  const rawTools = (snapshot.tools as unknown[]) ?? [];
  const toolItems: DependencyNode[] = rawTools.map((t) => {
    if (typeof t === "string") return { type: "tool" as const, name: t, isRef: t.startsWith("tools/") || t.includes("/") };
    const obj = t as Record<string, unknown>;
    if (obj.ref) return { type: "tool" as const, name: String(obj.ref), isRef: true };
    return { type: "tool" as const, name: String(obj.name ?? "unnamed"), isRef: false };
  });
  if (toolItems.length > 0) {
    groups.push({
      type: "tool",
      label: "Tools",
      icon: <Wrench className="size-3.5" />,
      colorClass: "text-emerald-600 dark:text-emerald-400",
      badgeClass: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
      items: toolItems,
    });
  }

  // Prompts
  const rawPrompts = snapshot.prompts as Record<string, unknown> | undefined;
  const promptItems: DependencyNode[] = [];
  if (rawPrompts) {
    for (const [key, val] of Object.entries(rawPrompts)) {
      if (typeof val === "string") {
        promptItems.push({
          type: "prompt",
          name: val,
          detail: key,
          isRef: val.startsWith("prompts/") || val.includes("/"),
        });
      }
    }
  }
  if (promptItems.length > 0) {
    groups.push({
      type: "prompt",
      label: "Prompts",
      icon: <MessageSquare className="size-3.5" />,
      colorClass: "text-purple-600 dark:text-purple-400",
      badgeClass: "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20",
      items: promptItems,
    });
  }

  // Knowledge Bases
  const rawKBs = (snapshot.knowledge_bases as unknown[]) ?? [];
  const kbItems: DependencyNode[] = rawKBs.map((kb) => {
    if (typeof kb === "string") return { type: "knowledge_base" as const, name: kb, isRef: kb.startsWith("kb/") || kb.includes("/") };
    const obj = kb as Record<string, unknown>;
    if (obj.ref) return { type: "knowledge_base" as const, name: String(obj.ref), isRef: true };
    return { type: "knowledge_base" as const, name: String(obj.name ?? "unnamed"), isRef: false };
  });
  if (kbItems.length > 0) {
    groups.push({
      type: "knowledge_base",
      label: "Knowledge Bases",
      icon: <BookOpen className="size-3.5" />,
      colorClass: "text-amber-600 dark:text-amber-400",
      badgeClass: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
      items: kbItems,
    });
  }

  return groups;
}

function DependencyTree({ agent }: { agent: Agent }) {
  const groups = useMemo(() => extractDependencies(agent), [agent]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    groups.forEach((g) => (init[g.type] = true));
    return init;
  });

  const toggle = (type: string) =>
    setExpanded((prev) => ({ ...prev, [type]: !prev[type] }));

  if (groups.length === 0) {
    return (
      <div className="flex flex-col items-center py-16 text-center">
        <p className="text-sm text-muted-foreground">No dependencies found.</p>
      </div>
    );
  }

  const isLast = (groupIdx: number) => groupIdx === groups.length - 1;

  return (
    <div className="pt-6">
      <div className="rounded-lg border border-border p-5">
        {/* Root node */}
        <div className="flex items-center gap-2 font-mono text-sm">
          <Activity className="size-4 text-foreground" />
          <span className="font-medium">{agent.name}</span>
          <span className="text-muted-foreground">v{agent.version}</span>
        </div>

        {/* Groups */}
        <div className="mt-1 ml-2 border-l border-border/60">
          {groups.map((group, gIdx) => {
            const last = isLast(gIdx);
            const isOpen = expanded[group.type] ?? true;
            return (
              <div key={group.type} className="relative">
                {/* Group header */}
                <button
                  onClick={() => toggle(group.type)}
                  className="group/dep flex w-full items-center gap-1.5 py-1.5 font-mono text-sm hover:bg-muted/30"
                >
                  <span className="w-5 text-border/60">
                    {last ? "\u2514\u2500" : "\u251c\u2500"}
                  </span>
                  {isOpen ? (
                    <ChevronDown className={cn("size-3.5", group.colorClass)} />
                  ) : (
                    <ChevronRight className={cn("size-3.5", group.colorClass)} />
                  )}
                  <span className={cn("flex items-center gap-1.5", group.colorClass)}>
                    {group.icon}
                    <span className="font-medium">{group.label}</span>
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    ({group.items.length})
                  </span>
                </button>

                {/* Items */}
                {isOpen && (
                  <div className={cn("ml-5", !last && "border-l border-border/40")}>
                    {group.items.map((item, iIdx) => {
                      const itemLast = iIdx === group.items.length - 1;
                      return (
                        <div
                          key={`${item.name}-${iIdx}`}
                          className="flex items-center gap-1.5 py-1 font-mono text-sm"
                        >
                          <span className="w-5 text-border/40">
                            {itemLast ? "\u2514\u2500" : "\u251c\u2500"}
                          </span>
                          <span className={cn("size-1.5 rounded-full shrink-0", {
                            "bg-blue-500": group.type === "model",
                            "bg-emerald-500": group.type === "tool",
                            "bg-purple-500": group.type === "prompt",
                            "bg-amber-500": group.type === "knowledge_base",
                          })} />
                          <span className="text-foreground">{item.name}</span>
                          {item.detail && (
                            <span className="text-[10px] text-muted-foreground">
                              ({item.detail})
                            </span>
                          )}
                          {item.isRef && (
                            <Badge
                              variant="outline"
                              className={cn("text-[9px] px-1 py-0 border", group.badgeClass)}
                            >
                              ref
                            </Badge>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Deploy History Tab
// ---------------------------------------------------------------------------

const DEPLOY_STATUS_COLORS: Record<string, string> = {
  completed: "text-emerald-500",
  failed: "text-destructive",
  pending: "text-muted-foreground",
};

const ACTIVE_DEPLOY_STATUSES = new Set([
  "pending", "parsing", "building", "provisioning",
  "deploying", "health_checking", "registering",
]);

const TARGET_BADGE_COLORS: Record<string, string> = {
  local: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20",
  aws: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  gcp: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  kubernetes: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
};

function DeployHistoryTab({ agentId }: { agentId: string }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["deploys", { agent_id: agentId }],
    queryFn: () => api.deploys.list({ agent_id: agentId }),
    staleTime: 5_000,
    refetchInterval: 10_000,
  });

  const jobs = data?.data ?? [];

  if (isLoading) {
    return (
      <div className="space-y-3 pt-6">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="rounded-lg border border-border p-4">
            <div className="flex items-center gap-3">
              <div className="size-2 animate-pulse rounded-full bg-muted" />
              <div className="h-3.5 w-24 animate-pulse rounded bg-muted" />
              <div className="h-5 w-12 animate-pulse rounded-full bg-muted" />
              <div className="ml-auto h-3 w-16 animate-pulse rounded bg-muted" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="mt-6 rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center text-sm text-destructive">
        Failed to load deploy history: {(error as Error).message}
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="flex flex-col items-center py-16 text-center">
        <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-border">
          <Activity className="size-5 text-muted-foreground" />
        </div>
        <h3 className="text-sm font-medium">No deploy history</h3>
        <p className="mt-1 max-w-xs text-xs text-muted-foreground">
          This agent has no recorded deployments yet.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2 pt-6">
      {jobs.map((job: DeployJob) => {
        const isActive = ACTIVE_DEPLOY_STATUSES.has(job.status);
        const statusColor =
          DEPLOY_STATUS_COLORS[job.status] ??
          (isActive ? "text-amber-500 animate-pulse" : "text-muted-foreground");
        const isExpanded = expandedId === job.id;
        const duration = job.completed_at
          ? formatDeployDuration(new Date(job.started_at), new Date(job.completed_at))
          : isActive
            ? "in progress"
            : "\u2014";

        return (
          <div key={job.id} className="overflow-hidden rounded-lg border border-border">
            <div
              onClick={() => setExpandedId(isExpanded ? null : job.id)}
              className="flex cursor-pointer items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/20"
            >
              <Circle className={cn("size-2 shrink-0 fill-current", statusColor)} />
              <span className="text-xs font-medium capitalize">{job.status.replace("_", " ")}</span>
              <Badge
                variant="outline"
                className={cn(
                  "text-[10px]",
                  TARGET_BADGE_COLORS[job.target] ?? "bg-muted text-muted-foreground border-border"
                )}
              >
                <Target className="mr-0.5 size-2.5" />
                {job.target}
              </Badge>
              <span className="ml-auto flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
                <Clock className="size-2.5" />
                {duration}
              </span>
              <span className="text-[10px] text-muted-foreground">
                {new Date(job.started_at).toLocaleDateString("en-US", {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </div>

            {isExpanded && (
              <div className="border-t border-border/30 bg-muted/10 px-4 py-4">
                <DeployPipeline
                  status={job.status}
                  errorMessage={job.error_message}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function formatDeployDuration(start: Date, end: Date): string {
  const ms = end.getTime() - start.getTime();
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (minutes < 60) return `${minutes}m ${secs}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error } = useQuery({
    queryKey: ["agent", id],
    queryFn: () => api.agents.get(id!),
    enabled: !!id,
  });

  const agent = data?.data;

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <div className="mb-6 h-4 w-20 animate-pulse rounded bg-muted" />
        <div className="space-y-3">
          <div className="h-6 w-48 animate-pulse rounded bg-muted" />
          <div className="h-4 w-96 animate-pulse rounded bg-muted/60" />
        </div>
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <Link to="/agents" className="mb-4 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-3" /> Back to agents
        </Link>
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center">
          <p className="text-sm text-destructive">
            {error ? (error as Error).message : "Agent not found"}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl p-6">
      <Link
        to="/agents"
        className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3" /> Agents
      </Link>

      <AgentHeader agent={agent} />

      <Tabs defaultValue="overview" className="mt-6">
        <TabsList className="h-9 bg-transparent p-0">
          <TabsTrigger value="overview" className={TAB_TRIGGER_CLASS}>
            Overview
          </TabsTrigger>
          <TabsTrigger value="configuration" className={TAB_TRIGGER_CLASS}>
            Configuration
          </TabsTrigger>
          <TabsTrigger value="dependencies" className={TAB_TRIGGER_CLASS}>
            Dependencies
          </TabsTrigger>
          <TabsTrigger value="deploys" className={TAB_TRIGGER_CLASS}>
            Deploy History
          </TabsTrigger>
          <TabsTrigger value="logs" className={TAB_TRIGGER_CLASS}>
            Logs
          </TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <OverviewTab agent={agent} />
        </TabsContent>
        <TabsContent value="configuration">
          <ConfigurationTab agent={agent} />
        </TabsContent>
        <TabsContent value="dependencies">
          <DependencyTree agent={agent} />
        </TabsContent>
        <TabsContent value="deploys">
          <DeployHistoryTab agentId={id!} />
        </TabsContent>
        <TabsContent value="logs">
          <div className="flex flex-col items-center py-16 text-center">
            <p className="text-sm text-muted-foreground">Live logs coming in M4.2</p>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
