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
  FileCode2,
  GitFork,
  Clipboard,
  Eye,
  EyeOff,
  Lock,
  Variable,
  Shield,
  Pencil,
  Save,
  X,
  AlertCircle,
  CheckCircle2,
  GitCompareArrows,
  Play,
  Loader2,
} from "lucide-react";
import { api, type Agent, type AgentStatus, type DeployJob, type AgentInvokeResponse } from "@/lib/api";
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
import { RelativeTime } from "@/components/ui/relative-time";
import { ConfigDiffViewer } from "@/components/config-diff-viewer";
import { VersionSelector, type VersionEntry } from "@/components/version-selector";
import { cn } from "@/lib/utils";
import { jsonToYaml, highlightYaml, validateYamlBasic } from "@/lib/yaml";
import { useState, useMemo, useCallback, useRef } from "react";
import { useUrlState } from "@/hooks/use-url-state";
import { useToast } from "@/hooks/use-toast";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import { UnsavedChangesDialog } from "@/components/unsaved-changes-dialog";

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
    color: "text-blue-600 dark:text-blue-400",
    bg: "bg-blue-500/10",
    dot: "bg-blue-500",
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
  degraded: {
    label: "Degraded",
    color: "text-yellow-600 dark:text-yellow-400",
    bg: "bg-yellow-500/10",
    dot: "bg-yellow-500",
  },
  error: {
    label: "Error",
    color: "text-red-600 dark:text-red-400",
    bg: "bg-red-500/10",
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
      <div className="flex items-center gap-2">
        <Link to={`/agents/builder/${agent.id}`}>
          <Button variant="outline" size="sm">
            <Pencil className="size-3" />
            Edit in Builder
          </Button>
        </Link>
        <CloneAgentDialog agent={agent} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Clone Agent Dialog
// ---------------------------------------------------------------------------

function CloneAgentDialog({ agent }: { agent: Agent }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(`${agent.name}-copy`);
  const [version, setVersion] = useState("0.1.0");
  const { toast } = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => api.agents.clone(agent.id, { name, version }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      setOpen(false);
      toast({
        title: "Agent cloned successfully",
        description: `Created "${name}" from ${agent.name}`,
        variant: "success",
      });
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
                <RelativeTime date={agent.created_at} />
              </span>
            </Field>
            <Field label="Last Updated">
              <span className="flex items-center gap-1.5 text-sm">
                <Clock className="size-3 text-muted-foreground" />
                <RelativeTime date={agent.updated_at} />
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

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Configuration (YAML Viewer + Inline Editor) Tab
// ---------------------------------------------------------------------------

// Mock version data for compare feature
const MOCK_VERSIONS: VersionEntry[] = [
  { version: "v1.2.0", date: "2026-03-10", author: "alice" },
  { version: "v1.1.0", date: "2026-03-05", author: "bob" },
  { version: "v1.0.0", date: "2026-02-28", author: "alice" },
];

const MOCK_VERSION_YAML: Record<string, string> = {
  "v1.0.0": `name: ${"{agent}"}\nversion: "1.0.0"\nframework: langgraph\nmodel:\n  primary: claude-3-5-sonnet\n  temperature: 0.7\n  max_tokens: 4096\ntools:\n  - ref: tools/web-search\ndeploy:\n  cloud: local\n  runtime: docker-compose\n  scaling:\n    min: 1\n    max: 3`,
  "v1.1.0": `name: ${"{agent}"}\nversion: "1.1.0"\nframework: langgraph\nmodel:\n  primary: claude-sonnet-4\n  temperature: 0.7\n  max_tokens: 4096\ntools:\n  - ref: tools/web-search\n  - ref: tools/database-query\ndeploy:\n  cloud: local\n  runtime: docker-compose\n  scaling:\n    min: 1\n    max: 5`,
  "v1.2.0": `name: ${"{agent}"}\nversion: "1.2.0"\nframework: langgraph\nmodel:\n  primary: claude-sonnet-4\n  fallback: gpt-4o\n  temperature: 0.5\n  max_tokens: 8192\ntools:\n  - ref: tools/web-search\n  - ref: tools/database-query\n  - ref: tools/slack-notify\nguardrails:\n  - pii_detection\n  - content_filter\ndeploy:\n  cloud: gcp\n  runtime: cloud-run\n  scaling:\n    min: 2\n    max: 10`,
};

function ConfigurationTab({ agent }: { agent: Agent }) {
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [showSaveSuccess, setShowSaveSuccess] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [diffVersionA, setDiffVersionA] = useState("v1.0.0");
  const [diffVersionB, setDiffVersionB] = useState("v1.1.0");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { toast } = useToast();
  const unsaved = useUnsavedChanges();

  const yamlStr = useMemo(() => {
    const snapshot = agent.config_snapshot;
    if (!snapshot || Object.keys(snapshot).length === 0) return null;
    return jsonToYaml(snapshot);
  }, [agent.config_snapshot]);

  const highlighted = useMemo(() => {
    if (!yamlStr) return null;
    return highlightYaml(yamlStr);
  }, [yamlStr]);

  const validation = useMemo(() => {
    if (!editing) return { valid: true, error: null };
    return validateYamlBasic(editContent);
  }, [editing, editContent]);

  const handleEdit = useCallback(() => {
    setEditContent(yamlStr ?? "");
    setEditing(true);
  }, [yamlStr]);

  const handleCancel = useCallback(() => {
    setEditing(false);
    setEditContent("");
    unsaved.markClean();
  }, [unsaved]);

  const handleContentChange = useCallback((value: string) => {
    setEditContent(value);
    unsaved.markDirty();
  }, [unsaved]);

  const handleSave = useCallback(() => {
    if (!validation.valid) return;
    setEditing(false);
    setShowSaveSuccess(true);
    unsaved.markClean();
    toast({ title: "Configuration saved", variant: "success" });
    setTimeout(() => setShowSaveSuccess(false), 2000);
  }, [validation.valid, toast, unsaved]);

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
    navigator.clipboard.writeText(editing ? editContent : yamlStr);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="pt-6">
      {/* Save success banner */}
      {showSaveSuccess && (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-green-500/30 bg-green-50 px-4 py-2.5 text-sm text-green-900 dark:border-green-500/30 dark:bg-green-950 dark:text-green-100">
          <CheckCircle2 className="size-4" />
          Configuration saved successfully
        </div>
      )}

      <div className="relative overflow-hidden rounded-lg border border-border bg-[#fafafa] dark:bg-[#0d1117]">
        {/* Header bar */}
        <div className="flex items-center justify-between border-b border-border bg-muted/30 px-4 py-2">
          <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <FileCode2 className="size-3.5" />
            agent.yaml
          </span>
          <div className="flex items-center gap-2">
            {editing && (
              <div className="flex items-center gap-1.5 text-xs">
                {validation.valid ? (
                  <span className="flex items-center gap-1 text-green-600 dark:text-green-400">
                    <CheckCircle2 className="size-3" />
                    Valid
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-red-600 dark:text-red-400" title={validation.error ?? ""}>
                    <AlertCircle className="size-3" />
                    {validation.error}
                  </span>
                )}
              </div>
            )}
            {!editing && (
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
            )}
            {editing ? (
              <div className="flex items-center gap-1.5">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleCancel}
                  className="h-7 gap-1 px-2 text-xs"
                >
                  <X className="size-3" />
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={handleSave}
                  disabled={!validation.valid}
                  className="h-7 gap-1 px-2 text-xs"
                >
                  <Save className="size-3" />
                  Save
                </Button>
              </div>
            ) : (
              <>
                <button
                  onClick={() => setShowDiff(!showDiff)}
                  className={cn(
                    "flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors hover:bg-muted hover:text-foreground",
                    showDiff ? "bg-muted text-foreground" : "text-muted-foreground"
                  )}
                >
                  <GitCompareArrows className="size-3" />
                  Compare
                </button>
                <button
                  onClick={handleEdit}
                  className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  <Pencil className="size-3" />
                  Edit
                </button>
              </>
            )}
          </div>
        </div>

        {editing ? (
          /* Editor mode */
          <div className="flex overflow-x-auto">
            {/* Line numbers gutter */}
            <div className="shrink-0 select-none border-r border-border bg-muted/20 py-3 font-mono text-[13px] leading-6">
              {editContent.split("\n").map((_, i) => (
                <div
                  key={i}
                  className="w-12 pr-3 text-right text-xs leading-6 text-muted-foreground/50"
                >
                  {i + 1}
                </div>
              ))}
            </div>
            {/* Textarea */}
            <textarea
              ref={textareaRef}
              value={editContent}
              onChange={(e) => handleContentChange(e.target.value)}
              spellCheck={false}
              className={cn(
                "min-h-[300px] flex-1 resize-y bg-transparent p-3 font-mono text-[13px] leading-6 text-foreground outline-none",
                !validation.valid && "ring-1 ring-inset ring-red-500/30"
              )}
            />
          </div>
        ) : (
          /* Read-only mode */
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
        )}
      </div>

      {/* Compare Versions Panel */}
      {showDiff && (
        <div className="mt-4 space-y-3">
          <div className="flex items-center gap-3">
            <VersionSelector
              versions={MOCK_VERSIONS}
              selected={diffVersionA}
              onChange={setDiffVersionA}
              label="Before"
            />
            <span className="text-xs text-muted-foreground">vs</span>
            <VersionSelector
              versions={MOCK_VERSIONS}
              selected={diffVersionB}
              onChange={setDiffVersionB}
              label="After"
            />
          </div>
          <ConfigDiffViewer
            before={MOCK_VERSION_YAML[diffVersionA]?.replace("{agent}", agent.name) ?? ""}
            after={MOCK_VERSION_YAML[diffVersionB]?.replace("{agent}", agent.name) ?? ""}
            beforeLabel={diffVersionA}
            afterLabel={diffVersionB}
          />
        </div>
      )}

      <UnsavedChangesDialog
        isBlocked={unsaved.isBlocked}
        onConfirm={unsaved.confirmNavigation}
        onCancel={unsaved.cancelNavigation}
      />
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

function getDependencyPath(item: DependencyNode): string | null {
  if (!item.isRef) return null;
  // Registry refs like "tools/zendesk-mcp" → /tools/zendesk-mcp
  // or "prompts/support-system-v3" → /prompts/support-system-v3
  // or "kb/product-docs" → /knowledge-bases/product-docs
  const name = item.name;
  if (item.type === "tool" && name.startsWith("tools/")) {
    return `/${name}`;
  }
  if (item.type === "prompt" && name.startsWith("prompts/")) {
    return `/${name}`;
  }
  if (item.type === "knowledge_base" && name.startsWith("kb/")) {
    return `/knowledge-bases/${name.slice(3)}`;
  }
  return null;
}

// -- SVG Graph constants --

const DEP_TYPE_COLORS: Record<DependencyNode["type"], { fill: string; stroke: string; text: string; bg: string; badge: string }> = {
  model: {
    fill: "#3b82f6",
    stroke: "#2563eb",
    text: "text-blue-600 dark:text-blue-400",
    bg: "bg-blue-500/10",
    badge: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  },
  tool: {
    fill: "#10b981",
    stroke: "#059669",
    text: "text-emerald-600 dark:text-emerald-400",
    bg: "bg-emerald-500/10",
    badge: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  },
  prompt: {
    fill: "#a855f7",
    stroke: "#9333ea",
    text: "text-purple-600 dark:text-purple-400",
    bg: "bg-purple-500/10",
    badge: "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20",
  },
  knowledge_base: {
    fill: "#f59e0b",
    stroke: "#d97706",
    text: "text-amber-600 dark:text-amber-400",
    bg: "bg-amber-500/10",
    badge: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
  },
};

const DEP_TYPE_LABELS: Record<DependencyNode["type"], string> = {
  model: "Model",
  tool: "Tool",
  prompt: "Prompt",
  knowledge_base: "KB",
};

interface FlatDep {
  node: DependencyNode;
  groupType: DependencyNode["type"];
  groupLabel: string;
}

function flattenDeps(groups: DependencyGroup[]): FlatDep[] {
  const flat: FlatDep[] = [];
  for (const g of groups) {
    for (const item of g.items) {
      flat.push({ node: item, groupType: g.type, groupLabel: g.label });
    }
  }
  return flat;
}

/** Truncate a label to fit inside an SVG node */
function truncateLabel(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max - 1) + "\u2026";
}

// ---------------------------------------------------------------------------
// Dependency Graph (Visual SVG Star + Table)
// ---------------------------------------------------------------------------

function DependencyGraph({ agent }: { agent: Agent }) {
  const navigate = useNavigate();
  const groups = useMemo(() => extractDependencies(agent), [agent]);
  const allDeps = useMemo(() => flattenDeps(groups), [groups]);
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  const handleNodeClick = useCallback(
    (dep: FlatDep) => {
      const path = getDependencyPath(dep.node);
      if (path) navigate(path);
    },
    [navigate]
  );

  if (allDeps.length === 0) {
    return (
      <div className="flex flex-col items-center py-16 text-center">
        <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-border">
          <Activity className="size-5 text-muted-foreground" />
        </div>
        <h3 className="text-sm font-medium">No dependencies found</h3>
        <p className="mt-1 max-w-xs text-xs text-muted-foreground">
          This agent does not reference any tools, models, prompts, or knowledge bases in its configuration.
        </p>
      </div>
    );
  }

  // -- Graph layout: star topology --
  const SVG_W = 700;
  const SVG_H = 420;
  const CX = SVG_W / 2;
  const CY = SVG_H / 2;
  const RADIUS = 155;
  const NODE_RX = 56;
  const NODE_RY = 32;
  const CENTER_R = 40;

  // Position outer nodes evenly around the center
  const nodePositions = allDeps.map((_, i) => {
    const angle = (2 * Math.PI * i) / allDeps.length - Math.PI / 2;
    return {
      x: CX + RADIUS * Math.cos(angle),
      y: CY + RADIUS * Math.sin(angle),
    };
  });

  return (
    <div className="space-y-6 pt-6">
      {/* Visual graph */}
      <div className="rounded-lg border border-border bg-muted/5 p-4 overflow-x-auto">
        <svg
          viewBox={`0 0 ${SVG_W} ${SVG_H}`}
          className="mx-auto block w-full max-w-[700px]"
          style={{ minWidth: 400 }}
        >
          <defs>
            {/* Glow filter for center node */}
            <filter id="dep-center-glow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="4" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* Connection lines */}
          {nodePositions.map((pos, i) => {
            const colors = DEP_TYPE_COLORS[allDeps[i].groupType];
            const isHovered = hoveredIdx === i;
            return (
              <line
                key={`line-${i}`}
                x1={CX}
                y1={CY}
                x2={pos.x}
                y2={pos.y}
                stroke={isHovered ? colors.fill : "currentColor"}
                className={isHovered ? "" : "text-border"}
                strokeWidth={isHovered ? 2 : 1}
                strokeDasharray={allDeps[i].node.isRef ? "none" : "4 3"}
                opacity={isHovered ? 1 : 0.5}
              />
            );
          })}

          {/* Center node (the agent) */}
          <g filter="url(#dep-center-glow)">
            <circle cx={CX} cy={CY} r={CENTER_R} className="fill-foreground/5 stroke-foreground/30" strokeWidth={2} />
            <circle cx={CX} cy={CY} r={CENTER_R - 4} className="fill-background stroke-foreground/20" strokeWidth={1} />
          </g>
          {/* Agent icon (activity/pulse) */}
          <path
            d={`M${CX - 10} ${CY} l4 -7 3 14 3 -14 4 7`}
            fill="none"
            className="stroke-foreground"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <text
            x={CX}
            y={CY + CENTER_R + 16}
            textAnchor="middle"
            className="fill-foreground text-[11px] font-medium"
          >
            {truncateLabel(agent.name, 20)}
          </text>
          <text
            x={CX}
            y={CY + CENTER_R + 29}
            textAnchor="middle"
            className="fill-muted-foreground text-[9px]"
          >
            v{agent.version}
          </text>

          {/* Outer dependency nodes */}
          {nodePositions.map((pos, i) => {
            const dep = allDeps[i];
            const colors = DEP_TYPE_COLORS[dep.groupType];
            const isHovered = hoveredIdx === i;
            const isClickable = getDependencyPath(dep.node) !== null;
            const shortName = dep.node.name.includes("/")
              ? dep.node.name.split("/").pop()!
              : dep.node.name;

            return (
              <g
                key={`node-${i}`}
                onMouseEnter={() => setHoveredIdx(i)}
                onMouseLeave={() => setHoveredIdx(null)}
                onClick={() => handleNodeClick(dep)}
                className={isClickable ? "cursor-pointer" : "cursor-default"}
              >
                {/* Node background */}
                <rect
                  x={pos.x - NODE_RX}
                  y={pos.y - NODE_RY}
                  width={NODE_RX * 2}
                  height={NODE_RY * 2}
                  rx={10}
                  ry={10}
                  fill={isHovered ? colors.fill : "var(--color-background, white)"}
                  fillOpacity={isHovered ? 0.12 : 1}
                  stroke={colors.fill}
                  strokeWidth={isHovered ? 2 : 1.5}
                  strokeOpacity={isHovered ? 1 : 0.4}
                />
                {/* Type color dot */}
                <circle
                  cx={pos.x - NODE_RX + 14}
                  cy={pos.y - NODE_RY + 12}
                  r={4}
                  fill={colors.fill}
                />
                {/* Type badge text */}
                <text
                  x={pos.x - NODE_RX + 22}
                  y={pos.y - NODE_RY + 15}
                  className="fill-muted-foreground text-[8px] font-medium uppercase"
                >
                  {DEP_TYPE_LABELS[dep.groupType]}
                </text>
                {/* Name */}
                <text
                  x={pos.x}
                  y={pos.y + 2}
                  textAnchor="middle"
                  className="fill-foreground text-[10px] font-medium"
                >
                  {truncateLabel(shortName, 16)}
                </text>
                {/* Detail (e.g. "primary", "fallback", "system") */}
                {dep.node.detail && (
                  <text
                    x={pos.x}
                    y={pos.y + 14}
                    textAnchor="middle"
                    className="fill-muted-foreground text-[8px]"
                  >
                    {dep.node.detail}
                  </text>
                )}
                {/* Ref indicator */}
                {dep.node.isRef && (
                  <text
                    x={pos.x + NODE_RX - 12}
                    y={pos.y - NODE_RY + 15}
                    textAnchor="end"
                    className="fill-muted-foreground text-[7px] italic"
                  >
                    ref
                  </text>
                )}
              </g>
            );
          })}
        </svg>

        {/* Legend */}
        <div className="mt-3 flex flex-wrap items-center justify-center gap-4">
          {(["model", "tool", "prompt", "knowledge_base"] as const).map((t) => {
            const colors = DEP_TYPE_COLORS[t];
            const count = allDeps.filter((d) => d.groupType === t).length;
            if (count === 0) return null;
            return (
              <div key={t} className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                <span
                  className="inline-block size-2.5 rounded-full"
                  style={{ background: colors.fill }}
                />
                <span className="capitalize">{DEP_TYPE_LABELS[t]}</span>
                <span>({count})</span>
              </div>
            );
          })}
          <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
            <svg width="20" height="6"><line x1="0" y1="3" x2="20" y2="3" stroke="currentColor" strokeWidth="1" strokeDasharray="4 3" /></svg>
            <span>inline</span>
          </div>
          <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
            <svg width="20" height="6"><line x1="0" y1="3" x2="20" y2="3" stroke="currentColor" strokeWidth="1" /></svg>
            <span>registry ref</span>
          </div>
        </div>
      </div>

      {/* Dependency table */}
      <div className="rounded-lg border border-border overflow-hidden">
        <div className="flex items-center gap-2 border-b border-border bg-muted/30 px-4 py-2.5">
          <Activity className="size-3.5 text-muted-foreground" />
          <span className="text-xs font-medium text-muted-foreground">
            All Dependencies
          </span>
          <span className="text-[10px] text-muted-foreground">({allDeps.length})</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/10 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                <th className="px-4 py-2 text-left">Type</th>
                <th className="px-4 py-2 text-left">Name</th>
                <th className="px-4 py-2 text-left">Detail</th>
                <th className="px-4 py-2 text-left">Source</th>
                <th className="px-4 py-2 text-left">Link</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {allDeps.map((dep, i) => {
                const colors = DEP_TYPE_COLORS[dep.groupType];
                const path = getDependencyPath(dep.node);
                return (
                  <tr
                    key={`${dep.node.name}-${i}`}
                    className="transition-colors hover:bg-muted/10"
                    onMouseEnter={() => setHoveredIdx(i)}
                    onMouseLeave={() => setHoveredIdx(null)}
                  >
                    <td className="px-4 py-2.5">
                      <Badge
                        variant="outline"
                        className={cn("text-[10px] border", colors.badge)}
                      >
                        <span
                          className="mr-1 inline-block size-1.5 rounded-full"
                          style={{ background: colors.fill }}
                        />
                        {DEP_TYPE_LABELS[dep.groupType]}
                      </Badge>
                    </td>
                    <td className="px-4 py-2.5">
                      <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                        {dep.node.name}
                      </code>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground">
                      {dep.node.detail ?? "\u2014"}
                    </td>
                    <td className="px-4 py-2.5">
                      {dep.node.isRef ? (
                        <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                          <ExternalLink className="size-2.5" />
                          registry
                        </span>
                      ) : (
                        <span className="text-[10px] text-muted-foreground">inline</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      {path ? (
                        <Link
                          to={path}
                          className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline dark:text-blue-400"
                        >
                          View
                          <ChevronRight className="size-3" />
                        </Link>
                      ) : (
                        <span className="text-[10px] text-muted-foreground">\u2014</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Environment Tab
// ---------------------------------------------------------------------------

interface EnvEntry {
  key: string;
  value: string;
  isSecret: boolean;
}

function extractEnvEntries(agent: Agent): EnvEntry[] {
  const snapshot = agent.config_snapshot ?? {};
  const deploy = (snapshot.deploy as Record<string, unknown>) ?? {};
  const entries: EnvEntry[] = [];

  // env_vars section (non-secret)
  const envVars = (deploy.env_vars as Record<string, unknown>) ?? {};
  for (const [key, val] of Object.entries(envVars)) {
    entries.push({ key, value: String(val), isSecret: false });
  }

  // secrets section
  const secrets = (deploy.secrets as unknown[]) ?? [];
  for (const s of secrets) {
    const name = typeof s === "string" ? s : String((s as Record<string, unknown>).name ?? s);
    entries.push({ key: name, value: "", isSecret: true });
  }

  return entries;
}

function EnvironmentTab({ agent }: { agent: Agent }) {
  const entries = useMemo(() => extractEnvEntries(agent), [agent]);
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});

  const toggleReveal = (key: string) =>
    setRevealed((prev) => ({ ...prev, [key]: !prev[key] }));

  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center py-16 text-center">
        <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-dashed border-border">
          <Variable className="size-5 text-muted-foreground" />
        </div>
        <h3 className="text-sm font-medium">No environment variables</h3>
        <p className="mt-1 max-w-xs text-xs text-muted-foreground">
          This agent does not have any configured environment variables or secrets.
        </p>
      </div>
    );
  }

  const envVars = entries.filter((e) => !e.isSecret);
  const secrets = entries.filter((e) => e.isSecret);

  return (
    <div className="space-y-6 pt-6">
      {/* Environment Variables */}
      {envVars.length > 0 && (
        <div className="rounded-lg border border-border">
          <div className="flex items-center gap-2 border-b border-border bg-muted/30 px-4 py-2.5">
            <Variable className="size-3.5 text-muted-foreground" />
            <span className="text-xs font-medium text-muted-foreground">
              Environment Variables
            </span>
            <span className="text-[10px] text-muted-foreground">({envVars.length})</span>
          </div>
          <div className="divide-y divide-border">
            {envVars.map((entry) => (
              <div
                key={entry.key}
                className="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/10"
              >
                <code className="min-w-[180px] shrink-0 font-mono text-xs font-medium text-foreground">
                  {entry.key}
                </code>
                <span className="text-muted-foreground">=</span>
                <code className="flex-1 truncate font-mono text-xs text-green-600 dark:text-green-400">
                  {entry.value}
                </code>
                <CopyButton text={`${entry.key}=${entry.value}`} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Secrets */}
      {secrets.length > 0 && (
        <div className="rounded-lg border border-border">
          <div className="flex items-center gap-2 border-b border-border bg-muted/30 px-4 py-2.5">
            <Shield className="size-3.5 text-muted-foreground" />
            <span className="text-xs font-medium text-muted-foreground">
              Secrets
            </span>
            <span className="text-[10px] text-muted-foreground">({secrets.length})</span>
          </div>
          <div className="divide-y divide-border">
            {secrets.map((entry) => (
              <div
                key={entry.key}
                className="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/10"
              >
                <div className="flex min-w-[180px] shrink-0 items-center gap-1.5">
                  <Lock className="size-3 text-amber-500" />
                  <code className="font-mono text-xs font-medium text-foreground">
                    {entry.key}
                  </code>
                </div>
                <span className="text-muted-foreground">=</span>
                <code className="flex-1 truncate font-mono text-xs text-muted-foreground">
                  {revealed[entry.key] ? entry.key : "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"}
                </code>
                <button
                  onClick={() => toggleReveal(entry.key)}
                  className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  title={revealed[entry.key] ? "Hide value" : "Reveal name"}
                >
                  {revealed[entry.key] ? (
                    <EyeOff className="size-3" />
                  ) : (
                    <Eye className="size-3" />
                  )}
                </button>
              </div>
            ))}
          </div>
          <div className="border-t border-border bg-muted/20 px-4 py-2">
            <p className="text-[10px] text-muted-foreground">
              Secret values are stored in your cloud provider's Secrets Manager and are not available in the dashboard.
            </p>
          </div>
        </div>
      )}
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
              <RelativeTime
                date={job.started_at}
                className="text-[10px] text-muted-foreground"
              />
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
  const [activeTab, setActiveTab] = useUrlState("tab", "overview" as string);
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

      <Tabs value={activeTab} onValueChange={(val) => setActiveTab(val as string)} className="mt-6">
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
          <TabsTrigger value="environment" className={TAB_TRIGGER_CLASS}>
            Environment
          </TabsTrigger>
          <TabsTrigger value="deploys" className={TAB_TRIGGER_CLASS}>
            Deploy History
          </TabsTrigger>
          <TabsTrigger value="logs" className={TAB_TRIGGER_CLASS}>
            Logs
          </TabsTrigger>
          <TabsTrigger value="invoke" className={TAB_TRIGGER_CLASS}>
            <Play className="size-3" />
            Invoke
          </TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <OverviewTab agent={agent} />
        </TabsContent>
        <TabsContent value="configuration">
          <ConfigurationTab agent={agent} />
        </TabsContent>
        <TabsContent value="dependencies">
          <DependencyGraph agent={agent} />
        </TabsContent>
        <TabsContent value="environment">
          <EnvironmentTab agent={agent} />
        </TabsContent>
        <TabsContent value="deploys">
          <DeployHistoryTab agentId={id!} />
        </TabsContent>
        <TabsContent value="logs">
          <div className="flex flex-col items-center py-16 text-center">
            <p className="text-sm text-muted-foreground">Live logs coming in M4.2</p>
          </div>
        </TabsContent>
        <TabsContent value="invoke">
          <InvokePanel agentId={id!} defaultEndpoint={agent.endpoint_url || ""} agentName={agent.name} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Invoke panel — chat with the agent's deployed runtime via the API proxy
// (POST /api/v1/agents/{id}/invoke). The bearer token is resolved server-side
// from the workspace secrets backend (key: agentbreeder/<agent>/auth-token)
// — see issue #176. The user only supplies the endpoint URL + the message.
// ────────────────────────────────────────────────────────────────────────────
function InvokePanel({
  agentId,
  defaultEndpoint,
  agentName,
}: {
  agentId: string;
  defaultEndpoint: string;
  agentName: string;
}) {
  const [endpoint, setEndpoint] = useState<string>(defaultEndpoint || "http://localhost:8080");
  const [input, setInput] = useState<string>("");
  const [sessionId, setSessionId] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [response, setResponse] = useState<AgentInvokeResponse | null>(null);

  const handleSend = async (): Promise<void> => {
    if (!input.trim()) return;
    setRunning(true);
    setResponse(null);
    try {
      const resp = await api.agents.invoke(agentId, {
        input,
        endpoint_url: endpoint || undefined,
        session_id: sessionId || undefined,
      });
      const result = resp.data;
      setResponse(result);
      if (result.session_id) setSessionId(result.session_id);
    } catch (e) {
      setResponse({
        output: "",
        session_id: null,
        duration_ms: 0,
        status_code: 0,
        error: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="mt-4 grid gap-6 md:grid-cols-2">
      <div className="space-y-3">
        <div className="space-y-2">
          <label className="text-xs font-medium">Endpoint URL</label>
          <input
            type="text"
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            placeholder="http://localhost:8080"
            className="h-8 w-full rounded-md border border-input bg-background px-2 font-mono text-xs outline-none focus:ring-1 focus:ring-ring"
          />
          <p className="text-[10px] text-muted-foreground">
            Where the agent's <code className="rounded bg-muted px-1">/invoke</code> endpoint is reachable.
          </p>
        </div>
        <div className="rounded-md border border-input bg-muted/30 p-2 text-[10px] leading-relaxed text-muted-foreground">
          Authentication token is resolved server-side from your workspace secrets.
          Set with{" "}
          <code className="rounded bg-muted px-1 font-mono">
            agentbreeder secret set {agentName}/auth-token
          </code>
          .
        </div>
        <div className="space-y-2">
          <label className="text-xs font-medium">Session ID <span className="text-muted-foreground">(auto-filled after first turn)</span></label>
          <input
            type="text"
            value={sessionId}
            onChange={(e) => setSessionId(e.target.value)}
            placeholder="(empty = new session)"
            className="h-8 w-full rounded-md border border-input bg-background px-2 font-mono text-xs outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
        <div className="space-y-2">
          <label className="text-xs font-medium">Message</label>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={`Ask ${agentName} something…`}
            spellCheck={false}
            className="h-32 w-full resize-none rounded-md border border-input bg-background p-2 text-xs leading-relaxed outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
        <div className="flex items-center justify-end">
          <Button
            size="sm"
            onClick={handleSend}
            disabled={running || !input.trim() || !endpoint.trim()}
          >
            {running ? <Loader2 className="size-3 animate-spin" /> : <Play className="size-3" />}
            {running ? "Sending..." : "Send"}
          </Button>
        </div>
      </div>

      <div className="space-y-3">
        <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Response
        </h3>
        {response === null && !running && (
          <div className="flex h-72 items-center justify-center rounded-md border border-dashed border-input text-xs text-muted-foreground">
            Send a message to see the agent's reply.
          </div>
        )}
        {running && (
          <div className="flex h-72 items-center justify-center rounded-md border border-input text-xs text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" />
            Calling /invoke through the proxy…
          </div>
        )}
        {response && (
          <div className="space-y-2">
            <div
              className={
                "flex items-center gap-2 rounded-md border p-2 text-xs " +
                (response.error
                  ? "border-destructive/30 bg-destructive/10 text-destructive"
                  : "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400")
              }
            >
              <span className="font-mono">
                status={response.status_code} • {response.duration_ms} ms
                {response.session_id && ` • session=${response.session_id.slice(0, 8)}`}
              </span>
            </div>
            {response.error && (
              <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md border border-destructive/30 bg-destructive/5 p-2 font-mono text-xs text-destructive">
                {response.error}
              </pre>
            )}
            {response.output && (
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-input bg-muted/40 p-3 text-xs leading-relaxed">
                {response.output}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
