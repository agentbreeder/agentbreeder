/**
 * Agent Builder — capstone page for composing agent configurations from
 * all registries (models, tools, prompts). Supports both YAML and Visual modes.
 */

import { useState, useCallback, useMemo, useRef, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Code2,
  LayoutGrid,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Rocket,
  Save,
  Copy,
  Check,
  AlertCircle,
  CheckCircle2,
  AlertTriangle,
  X,
  Plus,
  Trash2,
  Bot,
  Wrench,
  FileText,
  Cpu,
  Shield,
  Workflow,
  Globe,
  Languages,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DeployDialog } from "@/components/deploy/DeployDialog";
import { SubmitForReview } from "@/components/submit-for-review";
import {
  RegistryPicker,
  getMockModels,
  getMockTools,
  filterModelsByStatus,
} from "@/components/registry-picker";
import { VisualBuilder } from "@/components/visual-builder/VisualBuilder";
import type { CanvasNodeData } from "@/components/visual-builder/types";
import { graphToYaml, yamlToGraph } from "@/lib/graph-to-yaml";
import {
  formDataToYaml,
  yamlToFormData,
  type AgentFormData,
  type AgentLanguage,
  type GatewayOverride,
} from "@/lib/agent-yaml-emit";
import { cn } from "@/lib/utils";
import { highlightYaml, validateYamlBasic } from "@/lib/yaml";
import { api } from "@/lib/api";
import { useToast } from "@/hooks/use-toast";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_YAML = `name: my-agent
version: "0.1.0"
description: ""
team: engineering
owner: user@example.com

model:
  primary: claude-sonnet-4
  temperature: 0.7
  max_tokens: 4096

framework: langgraph

tools: []

prompts:
  system: ""

guardrails: []

deploy:
  cloud: local
  runtime: docker-compose`;

const FRAMEWORK_OPTIONS = [
  { value: "langgraph", label: "LangGraph" },
  { value: "openai_agents", label: "OpenAI Agents" },
  { value: "crewai", label: "CrewAI" },
  { value: "claude_sdk", label: "Claude SDK" },
  { value: "google_adk", label: "Google ADK" },
  { value: "custom", label: "Custom" },
] as const;

const CLOUD_OPTIONS = [
  { value: "local", label: "Local Docker", runtime: "docker-compose" },
  { value: "gcp", label: "Google Cloud Run", runtime: "cloud-run" },
  { value: "aws", label: "AWS ECS Fargate", runtime: "ecs-fargate" },
  { value: "kubernetes", label: "Kubernetes", runtime: "deployment" },
] as const;

const GUARDRAIL_OPTIONS = [
  { value: "pii_detection", label: "PII Detection", description: "Strips PII from outputs" },
  { value: "content_filter", label: "Content Filter", description: "Blocks harmful content" },
  { value: "hallucination_check", label: "Hallucination Check", description: "Flags low-confidence responses" },
] as const;

const FRAMEWORK_COLORS: Record<string, string> = {
  langgraph: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  crewai: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20",
  claude_sdk: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  openai_agents: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  google_adk: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  custom: "bg-muted text-muted-foreground border-border",
};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ValidationItem {
  key: string;
  label: string;
  status: "pass" | "fail" | "warn";
  message: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function validateAgentYaml(yaml: string): ValidationItem[] {
  const items: ValidationItem[] = [];
  const basicResult = validateYamlBasic(yaml);

  if (!basicResult.valid) {
    items.push({ key: "syntax", label: "YAML Syntax", status: "fail", message: basicResult.error ?? "Invalid YAML" });
    return items;
  }

  items.push({ key: "syntax", label: "YAML Syntax", status: "pass", message: "Valid YAML structure" });

  const data = yamlToFormData(yaml);

  // Name
  if (!data.name || data.name === "my-agent") {
    items.push({ key: "name", label: "Agent Name", status: data.name ? "warn" : "fail", message: data.name ? "Using default name" : "Name is required" });
  } else {
    const nameValid = /^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(data.name) || data.name.length === 1;
    items.push({ key: "name", label: "Agent Name", status: nameValid ? "pass" : "fail", message: nameValid ? `"${data.name}"` : "Must be lowercase alphanumeric with hyphens" });
  }

  // Version
  const versionValid = /^\d+\.\d+\.\d+$/.test(data.version);
  items.push({ key: "version", label: "Version", status: versionValid ? "pass" : "fail", message: versionValid ? `v${data.version}` : "Must be semver (e.g., 1.0.0)" });

  // Model
  if (data.model.primary) {
    items.push({ key: "model", label: "Model", status: "pass", message: data.model.primary });
  } else {
    items.push({ key: "model", label: "Model", status: "fail", message: "Primary model is required" });
  }

  // Framework
  const knownFrameworks = ["langgraph", "crewai", "claude_sdk", "openai_agents", "google_adk", "custom"];
  if (knownFrameworks.includes(data.framework)) {
    items.push({ key: "framework", label: "Framework", status: "pass", message: data.framework });
  } else {
    items.push({ key: "framework", label: "Framework", status: "fail", message: `Unknown framework: "${data.framework}"` });
  }

  // Tools
  if (data.tools.length === 0) {
    items.push({ key: "tools", label: "Tools", status: "warn", message: "No tools defined" });
  } else {
    items.push({ key: "tools", label: "Tools", status: "pass", message: `${data.tools.length} tool${data.tools.length !== 1 ? "s" : ""} configured` });
  }

  // Prompts
  if (!data.prompts.system) {
    items.push({ key: "prompts", label: "System Prompt", status: "warn", message: "No system prompt defined" });
  } else {
    items.push({ key: "prompts", label: "System Prompt", status: "pass", message: "Configured" });
  }

  // Deploy
  const knownClouds = ["local", "gcp", "aws", "kubernetes"];
  if (knownClouds.includes(data.deploy.cloud)) {
    items.push({ key: "deploy", label: "Deploy Target", status: "pass", message: `${data.deploy.cloud} / ${data.deploy.runtime}` });
  } else {
    items.push({ key: "deploy", label: "Deploy Target", status: "fail", message: "Invalid cloud target" });
  }

  // Guardrails
  if (data.guardrails.length === 0) {
    items.push({ key: "guardrails", label: "Guardrails", status: "warn", message: "No guardrails enabled" });
  } else {
    items.push({ key: "guardrails", label: "Guardrails", status: "pass", message: `${data.guardrails.length} enabled` });
  }

  return items;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ValidationStatusIcon({ status }: { status: "pass" | "fail" | "warn" }) {
  switch (status) {
    case "pass":
      return <CheckCircle2 className="size-3.5 text-emerald-500" />;
    case "fail":
      return <AlertCircle className="size-3.5 text-destructive" />;
    case "warn":
      return <AlertTriangle className="size-3.5 text-amber-500" />;
  }
}

function ValidationPanel({ yaml }: { yaml: string }) {
  const validationItems = useMemo(() => validateAgentYaml(yaml), [yaml]);
  const formData = useMemo(() => yamlToFormData(yaml), [yaml]);

  const passCount = validationItems.filter((i) => i.status === "pass").length;
  const failCount = validationItems.filter((i) => i.status === "fail").length;
  const warnCount = validationItems.filter((i) => i.status === "warn").length;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-border px-3 py-2.5">
        <h3 className="text-xs font-semibold tracking-tight">Validation</h3>
        <div className="mt-1 flex items-center gap-3 text-[10px]">
          <span className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="size-3" />
            {passCount}
          </span>
          <span className="flex items-center gap-1 text-destructive">
            <AlertCircle className="size-3" />
            {failCount}
          </span>
          <span className="flex items-center gap-1 text-amber-500">
            <AlertTriangle className="size-3" />
            {warnCount}
          </span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Validation checks */}
        <div className="border-b border-border/50 px-3 py-2">
          <h4 className="mb-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            Checks
          </h4>
          <div className="space-y-1.5">
            {validationItems.map((item) => (
              <div key={item.key} className="flex items-start gap-2">
                <ValidationStatusIcon status={item.status} />
                <div className="min-w-0 flex-1">
                  <div className="text-[11px] font-medium">{item.label}</div>
                  <div className="truncate text-[10px] text-muted-foreground">{item.message}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Agent preview card */}
        <div className="px-3 py-2">
          <h4 className="mb-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            Preview
          </h4>
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="flex items-start gap-2.5">
              <div className="flex size-8 items-center justify-center rounded-lg bg-foreground/5">
                <Bot className="size-4 text-foreground/60" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold">
                    {formData.name || "untitled"}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    v{formData.version}
                  </span>
                </div>
                {formData.description && (
                  <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                    {formData.description}
                  </p>
                )}
              </div>
            </div>

            <div className="mt-3 flex flex-wrap gap-1.5">
              <Badge
                variant="outline"
                className={cn("text-[9px]", FRAMEWORK_COLORS[formData.framework] ?? FRAMEWORK_COLORS.custom)}
              >
                {formData.framework}
              </Badge>
              <Badge variant="outline" className="text-[9px]">
                <Cpu className="mr-1 size-2.5" />
                {formData.model.primary || "no model"}
              </Badge>
              <Badge variant="outline" className="text-[9px]">
                <Wrench className="mr-1 size-2.5" />
                {formData.tools.length} tools
              </Badge>
              {formData.guardrails.length > 0 && (
                <Badge variant="outline" className="text-[9px]">
                  <Shield className="mr-1 size-2.5" />
                  {formData.guardrails.length} guardrails
                </Badge>
              )}
            </div>

            <div className="mt-2 flex items-center gap-3 text-[9px] text-muted-foreground">
              <span>{formData.team}</span>
              <span>{formData.deploy.cloud} / {formData.deploy.runtime}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// YAML Editor
// ---------------------------------------------------------------------------

function YamlEditor({
  yaml,
  onChange,
}: {
  yaml: string;
  onChange: (value: string) => void;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const lineCountRef = useRef<HTMLDivElement>(null);

  const lineCount = yaml.split("\n").length;
  const highlightedHtml = useMemo(() => highlightYaml(yaml), [yaml]);

  // Sync scroll between textarea and line numbers
  const handleScroll = useCallback(() => {
    if (textareaRef.current && lineCountRef.current) {
      lineCountRef.current.scrollTop = textareaRef.current.scrollTop;
    }
  }, []);

  // Handle Tab key for indentation
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const ta = e.currentTarget;
      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const newValue = yaml.slice(0, start) + "  " + yaml.slice(end);
      onChange(newValue);
      requestAnimationFrame(() => {
        ta.selectionStart = ta.selectionEnd = start + 2;
      });
    }
  };

  return (
    <div className="relative flex h-full overflow-hidden rounded-lg border border-border bg-background font-mono text-xs">
      {/* Line numbers */}
      <div
        ref={lineCountRef}
        className="flex flex-col items-end overflow-hidden border-r border-border bg-muted/30 px-2 py-3 text-[11px] leading-5 text-muted-foreground/50 select-none"
      >
        {Array.from({ length: lineCount }, (_, i) => (
          <div key={i + 1}>{i + 1}</div>
        ))}
      </div>

      {/* Highlighted preview (behind textarea) */}
      <div className="relative flex-1 overflow-hidden">
        <pre
          className="pointer-events-none absolute inset-0 overflow-auto whitespace-pre p-3 text-[11px] leading-5"
          dangerouslySetInnerHTML={{ __html: highlightedHtml }}
          aria-hidden
        />
        <textarea
          ref={textareaRef}
          value={yaml}
          onChange={(e) => onChange(e.target.value)}
          onScroll={handleScroll}
          onKeyDown={handleKeyDown}
          spellCheck={false}
          className="absolute inset-0 resize-none bg-transparent p-3 text-[11px] leading-5 text-transparent caret-foreground outline-none"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Visual Builder
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Gateways Panel — per-gateway override editor (Track H / #164).
// Most agents leave gateways empty; the panel is collapsed by default and
// only emits YAML for gateways that have at least one override field set.
// ---------------------------------------------------------------------------

const KNOWN_GATEWAYS = ["litellm", "openrouter", "vllm", "bedrock"] as const;
const FALLBACK_POLICIES = ["fastest", "cheapest", "first"] as const;

function GatewaysPanel({
  gateways,
  onChange,
}: {
  gateways: Record<string, GatewayOverride>;
  onChange: (gateways: Record<string, GatewayOverride>) => void;
}) {
  const [open, setOpen] = useState(Object.keys(gateways).length > 0);
  const [newGatewayName, setNewGatewayName] = useState("");

  const addGateway = useCallback(
    (name: string) => {
      const trimmed = name.trim().toLowerCase();
      if (!trimmed || gateways[trimmed]) return;
      onChange({ ...gateways, [trimmed]: {} });
    },
    [gateways, onChange],
  );

  const removeGateway = useCallback(
    (name: string) => {
      const next = { ...gateways };
      delete next[name];
      onChange(next);
    },
    [gateways, onChange],
  );

  const updateGateway = useCallback(
    (name: string, patch: Partial<GatewayOverride>) => {
      const current = gateways[name] ?? {};
      onChange({ ...gateways, [name]: { ...current, ...patch } });
    },
    [gateways, onChange],
  );

  const entries = Object.entries(gateways);

  return (
    <section data-testid="gateways-section">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mb-3 flex w-full items-center justify-between text-sm font-semibold"
      >
        <span className="flex items-center gap-2">
          <Globe className="size-4" />
          Gateways{" "}
          <span className="text-[10px] font-normal text-muted-foreground">
            ({entries.length} configured)
          </span>
        </span>
        <span className="text-[10px] text-muted-foreground">{open ? "Hide" : "Show"}</span>
      </button>

      {open && (
        <div className="space-y-3">
          <p className="text-[11px] text-muted-foreground">
            Override the catalog defaults for a gateway (URL, api-key env var, fallback policy).
            Most agents leave this empty and inherit the org-level catalog.
          </p>

          {entries.map(([name, override]) => (
            <div
              key={name}
              data-testid={`gateway-${name}`}
              className="space-y-2 rounded-lg border border-border bg-muted/20 p-3"
            >
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs font-medium">{name}</span>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-1.5 text-muted-foreground hover:text-destructive"
                  onClick={() => removeGateway(name)}
                  aria-label={`Remove ${name} override`}
                >
                  <Trash2 className="size-3" />
                </Button>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="mb-1 block text-[10px] font-medium text-muted-foreground">URL</label>
                  <Input
                    value={override.url ?? ""}
                    onChange={(e) => updateGateway(name, { url: e.target.value || undefined })}
                    placeholder="http://litellm.local:4000"
                    className="h-7 text-[11px]"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[10px] font-medium text-muted-foreground">
                    API key env var
                  </label>
                  <Input
                    value={override.api_key_env ?? ""}
                    onChange={(e) =>
                      updateGateway(name, { api_key_env: e.target.value || undefined })
                    }
                    placeholder="LITELLM_API_KEY"
                    className="h-7 text-[11px]"
                  />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-[10px] font-medium text-muted-foreground">
                  Fallback policy
                </label>
                <select
                  value={override.fallback_policy ?? ""}
                  onChange={(e) =>
                    updateGateway(name, {
                      fallback_policy:
                        (e.target.value as GatewayOverride["fallback_policy"]) || undefined,
                    })
                  }
                  className="h-7 w-full rounded-md border border-input bg-background px-2 text-[11px] outline-none"
                >
                  <option value="">(none)</option>
                  {FALLBACK_POLICIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          ))}

          <div className="flex items-center gap-2">
            <select
              value=""
              onChange={(e) => {
                if (e.target.value) addGateway(e.target.value);
              }}
              className="h-7 flex-1 rounded-md border border-input bg-background px-2 text-[11px] outline-none"
              aria-label="Add a known gateway override"
            >
              <option value="">Add a known gateway…</option>
              {KNOWN_GATEWAYS.filter((g) => !gateways[g]).map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </select>
            <Input
              value={newGatewayName}
              onChange={(e) => setNewGatewayName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  if (newGatewayName.trim()) {
                    addGateway(newGatewayName);
                    setNewGatewayName("");
                  }
                }
              }}
              placeholder="custom name"
              className="h-7 flex-1 text-[11px]"
            />
            <Button
              size="sm"
              variant="outline"
              className="h-7 px-2 text-[11px]"
              onClick={() => {
                if (newGatewayName.trim()) {
                  addGateway(newGatewayName);
                  setNewGatewayName("");
                }
              }}
            >
              <Plus className="size-3" />
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

function VisualBuilderForm({
  formData,
  onChange,
}: {
  formData: AgentFormData;
  onChange: (data: AgentFormData) => void;
}) {
  const allModels = useMemo(() => getMockModels(), []);
  const tools = useMemo(() => getMockTools(), []);
  const [tagInput, setTagInput] = useState("");
  const [showDeprecatedModels, setShowDeprecatedModels] = useState(false);
  // Always include the currently-selected model in the dropdown so existing
  // configs with a deprecated/retired model don't silently drop the value.
  const visibleModels = useMemo(() => {
    const filtered = filterModelsByStatus(allModels, showDeprecatedModels);
    const selected = allModels.find((m) => m.name === formData.model.primary);
    if (selected && !filtered.some((m) => m.id === selected.id)) {
      return [...filtered, selected];
    }
    return filtered;
  }, [allModels, showDeprecatedModels, formData.model.primary]);
  const selectedModel = useMemo(
    () => allModels.find((m) => m.name === formData.model.primary),
    [allModels, formData.model.primary],
  );
  const selectedModelStatus = selectedModel?.status ?? "active";

  const update = useCallback(
    (patch: Partial<AgentFormData>) => {
      onChange({ ...formData, ...patch });
    },
    [formData, onChange]
  );

  const addTag = useCallback(() => {
    const tag = tagInput.trim().toLowerCase();
    if (tag && !formData.tags.includes(tag)) {
      update({ tags: [...formData.tags, tag] });
      setTagInput("");
    }
  }, [tagInput, formData.tags, update]);

  const removeTag = useCallback(
    (tag: string) => {
      update({ tags: formData.tags.filter((t) => t !== tag) });
    },
    [formData.tags, update]
  );

  const addTool = useCallback(
    (toolRef: string) => {
      if (!formData.tools.includes(toolRef)) {
        update({ tools: [...formData.tools, toolRef] });
      }
    },
    [formData.tools, update]
  );

  const removeTool = useCallback(
    (toolRef: string) => {
      update({ tools: formData.tools.filter((t) => t !== toolRef) });
    },
    [formData.tools, update]
  );

  const toggleGuardrail = useCallback(
    (value: string) => {
      if (formData.guardrails.includes(value)) {
        update({ guardrails: formData.guardrails.filter((g) => g !== value) });
      } else {
        update({ guardrails: [...formData.guardrails, value] });
      }
    },
    [formData.guardrails, update]
  );

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-2xl space-y-8 p-6">
        {/* Identity */}
        <section>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Bot className="size-4" />
            Identity
          </h3>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Name</label>
                <Input
                  value={formData.name}
                  onChange={(e) => update({ name: e.target.value })}
                  placeholder="my-agent"
                  className="h-8 text-xs"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Version</label>
                <Input
                  value={formData.version}
                  onChange={(e) => update({ version: e.target.value })}
                  placeholder="0.1.0"
                  className="h-8 text-xs"
                />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Description</label>
              <Input
                value={formData.description}
                onChange={(e) => update({ description: e.target.value })}
                placeholder="What does this agent do?"
                className="h-8 text-xs"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Team</label>
                <Input
                  value={formData.team}
                  onChange={(e) => update({ team: e.target.value })}
                  placeholder="engineering"
                  className="h-8 text-xs"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Owner</label>
                <Input
                  value={formData.owner}
                  onChange={(e) => update({ owner: e.target.value })}
                  placeholder="user@example.com"
                  className="h-8 text-xs"
                />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Tags</label>
              <div className="flex items-center gap-2">
                <Input
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addTag())}
                  placeholder="Add a tag..."
                  className="h-8 flex-1 text-xs"
                />
                <Button size="sm" variant="outline" className="h-8 text-xs" onClick={addTag}>
                  Add
                </Button>
              </div>
              {formData.tags.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {formData.tags.map((tag) => (
                    <Badge key={tag} variant="outline" className="gap-1 text-[10px]">
                      {tag}
                      <button onClick={() => removeTag(tag)}>
                        <X className="size-2.5" />
                      </button>
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          </div>
        </section>

        {/* Model */}
        <section>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Cpu className="size-4" />
            Model
          </h3>
          <div className="space-y-3">
            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="block text-[11px] font-medium text-muted-foreground">Primary Model</label>
                <label className="flex cursor-pointer items-center gap-1.5 text-[10px] text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={showDeprecatedModels}
                    onChange={(e) => setShowDeprecatedModels(e.target.checked)}
                    className="size-3 rounded accent-foreground"
                  />
                  Show deprecated
                </label>
              </div>
              <select
                value={formData.model.primary}
                onChange={(e) => update({ model: { ...formData.model, primary: e.target.value } })}
                className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
              >
                <option value="">Select a model...</option>
                {visibleModels.map((m) => {
                  const status = m.status ?? "active";
                  const suffix =
                    status === "deprecated"
                      ? " · deprecated"
                      : status === "retired"
                        ? " · retired"
                        : status === "beta"
                          ? " · beta"
                          : "";
                  return (
                    <option key={m.id} value={m.name}>
                      {m.name} ({m.provider}){suffix}
                    </option>
                  );
                })}
              </select>
              {(selectedModelStatus === "deprecated" || selectedModelStatus === "retired") && (
                <div
                  role="alert"
                  data-testid="model-status-warning"
                  className="mt-1.5 flex items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-700 dark:text-amber-400"
                >
                  <AlertTriangle className="size-3" />
                  Selected model is {selectedModelStatus}. Consider switching to an active model.
                </div>
              )}
            </div>
            <div>
              <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
                Fallback Model <span className="text-muted-foreground/50">(optional)</span>
              </label>
              <select
                value={formData.model.fallback}
                onChange={(e) => update({ model: { ...formData.model, fallback: e.target.value } })}
                className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
              >
                <option value="">None</option>
                {visibleModels
                  .filter((m) => m.name !== formData.model.primary)
                  .map((m) => (
                    <option key={m.id} value={m.name}>
                      {m.name} ({m.provider})
                    </option>
                  ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 flex items-center justify-between text-[11px] font-medium text-muted-foreground">
                  <span>Temperature</span>
                  <span className="font-mono">{formData.model.temperature.toFixed(1)}</span>
                </label>
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.1}
                  value={formData.model.temperature}
                  onChange={(e) => update({ model: { ...formData.model, temperature: parseFloat(e.target.value) } })}
                  className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-muted accent-foreground"
                />
                <div className="mt-0.5 flex justify-between text-[9px] text-muted-foreground/50">
                  <span>Precise</span>
                  <span>Creative</span>
                </div>
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Max Tokens</label>
                <Input
                  type="number"
                  value={formData.model.max_tokens}
                  onChange={(e) => update({ model: { ...formData.model, max_tokens: parseInt(e.target.value) || 4096 } })}
                  className="h-8 text-xs"
                />
              </div>
            </div>
          </div>
        </section>

        {/* Language — Track I (#165). Maps to top-level `framework:` for python,
            or `runtime: { language, framework }` for typescript/node. */}
        <section data-testid="language-section">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Languages className="size-4" />
            Language
          </h3>
          <div className="grid grid-cols-2 gap-2">
            {(["python", "typescript"] as const).map((lang) => (
              <button
                key={lang}
                data-testid={`language-${lang}`}
                onClick={() => update({ language: lang })}
                className={cn(
                  "rounded-lg border p-3 text-left transition-all",
                  formData.language === lang
                    ? "border-foreground/30 bg-foreground/5 ring-1 ring-foreground/10"
                    : "border-border hover:border-border/80 hover:bg-muted/30",
                )}
              >
                <div className="text-xs font-medium capitalize">{lang}</div>
                <div className="mt-0.5 text-[10px] text-muted-foreground">
                  {lang === "python" ? "Default Python runtime" : "Polyglot Node.js / TypeScript"}
                </div>
              </button>
            ))}
          </div>
        </section>

        {/* Framework */}
        <section>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Code2 className="size-4" />
            Framework
          </h3>
          <div className="grid grid-cols-3 gap-2">
            {FRAMEWORK_OPTIONS.map((fw) => (
              <button
                key={fw.value}
                onClick={() => update({ framework: fw.value })}
                className={cn(
                  "rounded-lg border p-3 text-left transition-all",
                  formData.framework === fw.value
                    ? "border-foreground/30 bg-foreground/5 ring-1 ring-foreground/10"
                    : "border-border hover:border-border/80 hover:bg-muted/30"
                )}
              >
                <div className="text-xs font-medium">{fw.label}</div>
              </button>
            ))}
          </div>
        </section>

        {/* Tools */}
        <section>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Wrench className="size-4" />
            Tools
          </h3>
          {formData.tools.length > 0 && (
            <div className="mb-3 space-y-1.5">
              {formData.tools.map((tool) => (
                <div
                  key={tool}
                  className="flex items-center justify-between rounded-md border border-border bg-muted/20 px-3 py-2"
                >
                  <div className="flex items-center gap-2">
                    <Wrench className="size-3.5 text-muted-foreground" />
                    <span className="font-mono text-xs">{tool}</span>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-1.5 text-muted-foreground hover:text-destructive"
                    onClick={() => removeTool(tool)}
                  >
                    <Trash2 className="size-3" />
                  </Button>
                </div>
              ))}
            </div>
          )}
          <div>
            <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Add from registry</label>
            <div className="grid grid-cols-2 gap-1.5">
              {tools
                .filter((t) => !formData.tools.includes(`tools/${t.name}`))
                .slice(0, 6)
                .map((tool) => (
                  <button
                    key={tool.id}
                    onClick={() => addTool(`tools/${tool.name}`)}
                    className="flex items-center gap-2 rounded-md border border-dashed border-border p-2 text-left transition-colors hover:border-foreground/30 hover:bg-muted/30"
                  >
                    <Plus className="size-3 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[11px] font-medium">{tool.name}</div>
                      <div className="truncate text-[9px] text-muted-foreground">{tool.description}</div>
                    </div>
                  </button>
                ))}
            </div>
          </div>
        </section>

        {/* Prompts */}
        <section>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <FileText className="size-4" />
            System Prompt
          </h3>
          <div>
            <textarea
              value={formData.prompts.system}
              onChange={(e) => update({ prompts: { system: e.target.value } })}
              placeholder="Enter the system prompt for your agent, or use a registry reference like prompts/support-system-v3"
              className="min-h-[120px] w-full resize-y rounded-lg border border-input bg-background p-3 text-xs outline-none placeholder:text-muted-foreground/50 focus:ring-1 focus:ring-ring"
            />
            <div className="mt-1 text-right text-[10px] text-muted-foreground">
              {formData.prompts.system.length} characters
            </div>
          </div>
        </section>

        {/* Guardrails */}
        <section>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Shield className="size-4" />
            Guardrails
          </h3>
          <div className="space-y-2">
            {GUARDRAIL_OPTIONS.map((g) => (
              <label
                key={g.value}
                className={cn(
                  "flex cursor-pointer items-center gap-3 rounded-lg border p-3 transition-all",
                  formData.guardrails.includes(g.value)
                    ? "border-foreground/20 bg-foreground/5"
                    : "border-border hover:bg-muted/30"
                )}
              >
                <input
                  type="checkbox"
                  checked={formData.guardrails.includes(g.value)}
                  onChange={() => toggleGuardrail(g.value)}
                  className="size-3.5 rounded accent-foreground"
                />
                <div>
                  <div className="text-xs font-medium">{g.label}</div>
                  <div className="text-[10px] text-muted-foreground">{g.description}</div>
                </div>
              </label>
            ))}
          </div>
        </section>

        {/* Gateways — Track H (#164). Optional per-gateway overrides for the
            org-level catalog. Most agents leave this empty. */}
        <GatewaysPanel
          gateways={formData.gateways}
          onChange={(gateways) => update({ gateways })}
        />

        {/* Deploy */}
        <section>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Rocket className="size-4" />
            Deployment
          </h3>
          <div className="space-y-3">
            <div>
              <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Cloud Target</label>
              <select
                value={formData.deploy.cloud}
                onChange={(e) => {
                  const cloud = CLOUD_OPTIONS.find((c) => c.value === e.target.value);
                  update({
                    deploy: {
                      ...formData.deploy,
                      cloud: e.target.value,
                      runtime: cloud?.runtime ?? "docker-compose",
                    },
                  });
                }}
                className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
              >
                {CLOUD_OPTIONS.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Min Instances</label>
                <Input
                  type="number"
                  min={0}
                  max={100}
                  value={formData.deploy.scalingMin}
                  onChange={(e) => update({ deploy: { ...formData.deploy, scalingMin: parseInt(e.target.value) || 1 } })}
                  className="h-8 text-xs"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Max Instances</label>
                <Input
                  type="number"
                  min={1}
                  max={100}
                  value={formData.deploy.scalingMax}
                  onChange={(e) => update({ deploy: { ...formData.deploy, scalingMax: parseInt(e.target.value) || 10 } })}
                  className="h-8 text-xs"
                />
              </div>
            </div>
          </div>
        </section>

        {/* Spacer at bottom */}
        <div className="h-12" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function AgentBuilderPage() {
  const { id } = useParams();
  const { toast } = useToast();
  const { markDirty, markClean, isBlocked, confirmNavigation, cancelNavigation } = useUnsavedChanges();

  const [mode, setMode] = useState<"yaml" | "visual" | "canvas">("yaml");
  const [leftSidebarOpen, setLeftSidebarOpen] = useState(true);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(true);
  const [yaml, setYaml] = useState(DEFAULT_YAML);
  const [formData, setFormData] = useState<AgentFormData>(() => yamlToFormData(DEFAULT_YAML));
  const [deployOpen, setDeployOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  // Canvas state for the visual builder (ReactFlow)
  const [canvasNodes, setCanvasNodes] = useState<import("@xyflow/react").Node<CanvasNodeData>[]>(() => {
    const { nodes } = yamlToGraph(DEFAULT_YAML);
    return nodes;
  });
  const [canvasEdges, setCanvasEdges] = useState<import("@xyflow/react").Edge[]>(() => {
    const { edges } = yamlToGraph(DEFAULT_YAML);
    return edges;
  });

  // Load existing agent config when editing
  const { data: agentData } = useQuery({
    queryKey: ["agent", id],
    queryFn: () => api.agents.get(id!),
    enabled: !!id,
  });

  // Populate editor with existing agent data
  useEffect(() => {
    if (agentData?.data) {
      const agent = agentData.data;
      const config = agent.config_snapshot;
      if (config && Object.keys(config).length > 0) {
        // Build YAML from config_snapshot
        const runtimeBlock = config.runtime as Record<string, unknown> | undefined;
        const runtimeLang = runtimeBlock?.language as string | undefined;
        const language: AgentLanguage =
          runtimeLang === "node" || runtimeLang === "typescript" ? "typescript" : "python";
        const gateways =
          (config.gateways as Record<string, GatewayOverride> | undefined) ?? {};
        const loadedFormData: AgentFormData = {
          name: agent.name,
          version: agent.version,
          description: agent.description,
          team: agent.team,
          owner: agent.owner,
          tags: agent.tags ?? [],
          language,
          model: {
            primary: agent.model_primary,
            fallback: agent.model_fallback ?? "",
            temperature: (config.model as Record<string, unknown>)?.temperature as number ?? 0.7,
            max_tokens: (config.model as Record<string, unknown>)?.max_tokens as number ?? 4096,
          },
          framework: agent.framework,
          tools: ((config.tools as Array<Record<string, unknown>>) ?? [])
            .map((t) => (t.ref as string) ?? "")
            .filter(Boolean),
          prompts: {
            system: ((config.prompts as Record<string, unknown>)?.system as string) ?? "",
          },
          guardrails: ((config.guardrails as string[]) ?? []),
          gateways,
          deploy: {
            cloud: ((config.deploy as Record<string, unknown>)?.cloud as string) ?? "local",
            runtime: ((config.deploy as Record<string, unknown>)?.runtime as string) ?? "docker-compose",
            scalingMin: 1,
            scalingMax: 10,
          },
        };
        // eslint-disable-next-line react-hooks/set-state-in-effect -- sync from API response
        setFormData(loadedFormData);
        setYaml(formDataToYaml(loadedFormData));
      }
    }
  }, [agentData]);

  const handleYamlChange = useCallback(
    (newYaml: string) => {
      setYaml(newYaml);
      markDirty();
    },
    [markDirty]
  );

  const handleFormDataChange = useCallback(
    (newData: AgentFormData) => {
      setFormData(newData);
      markDirty();
    },
    [markDirty]
  );

  // Switch between modes with data sync
  const switchMode = useCallback(
    (newMode: "yaml" | "visual" | "canvas") => {
      if (newMode === mode) return;

      // Sync data out of the current mode
      let currentYamlValue = yaml;
      if (mode === "visual") {
        currentYamlValue = formDataToYaml(formData);
        setYaml(currentYamlValue);
      } else if (mode === "canvas") {
        currentYamlValue = graphToYaml(canvasNodes, canvasEdges);
        setYaml(currentYamlValue);
      }

      // Sync data into the new mode
      if (newMode === "visual") {
        setFormData(yamlToFormData(currentYamlValue));
      } else if (newMode === "canvas") {
        const { nodes, edges } = yamlToGraph(currentYamlValue);
        setCanvasNodes(nodes);
        setCanvasEdges(edges);
      }

      setMode(newMode);
    },
    [mode, yaml, formData, canvasNodes, canvasEdges]
  );

  // Canvas change handlers
  const handleCanvasNodesChange = useCallback(
    (nodes: import("@xyflow/react").Node<CanvasNodeData>[]) => {
      setCanvasNodes(nodes);
      markDirty();
    },
    [markDirty]
  );

  const handleCanvasEdgesChange = useCallback(
    (edges: import("@xyflow/react").Edge[]) => {
      setCanvasEdges(edges);
      markDirty();
    },
    [markDirty]
  );

  // Registry picker insert handlers
  const insertIntoYaml = useCallback(
    (section: string, value: string) => {
      if (mode === "visual") {
        // In visual mode, update formData directly
        if (section === "model") {
          setFormData((prev) => ({ ...prev, model: { ...prev.model, primary: value } }));
        } else if (section === "tool") {
          setFormData((prev) => ({
            ...prev,
            tools: prev.tools.includes(value) ? prev.tools : [...prev.tools, value],
          }));
        } else if (section === "prompt") {
          setFormData((prev) => ({ ...prev, prompts: { system: value } }));
        }
        markDirty();
        return;
      }

      // In YAML mode, insert into the YAML text
      let newYaml = yaml;
      if (section === "model") {
        newYaml = yaml.replace(/(primary:\s*).+/, `$1${value}`);
      } else if (section === "tool") {
        // Add tool reference
        if (yaml.includes("tools: []")) {
          newYaml = yaml.replace("tools: []", `tools:\n  - ref: ${value}`);
        } else {
          newYaml = yaml.replace(/(tools:\n(?:\s+- ref: .+\n?)*)/, `$1  - ref: ${value}\n`);
        }
      } else if (section === "prompt") {
        newYaml = yaml.replace(/(system:\s*).+/, `$1${value}`);
      }
      setYaml(newYaml);
      markDirty();
    },
    [mode, yaml, markDirty]
  );

  const handleCopy = useCallback(() => {
    const content = mode === "yaml" ? yaml : formDataToYaml(formData);
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
    toast({ title: "YAML copied to clipboard", variant: "success" });
  }, [mode, yaml, formData, toast]);

  const handleSave = useCallback(() => {
    const content = mode === "yaml" ? yaml : formDataToYaml(formData);
    const validation = validateAgentYaml(content);
    const hasErrors = validation.some((i) => i.status === "fail");

    if (hasErrors) {
      toast({ title: "Fix validation errors before saving", variant: "error" });
      return;
    }

    markClean();
    toast({ title: "Agent configuration saved", variant: "success" });
  }, [mode, yaml, formData, markClean, toast]);

  // Current YAML for validation panel (sync from visual/canvas mode if needed)
  const currentYaml = mode === "yaml"
    ? yaml
    : mode === "visual"
      ? formDataToYaml(formData)
      : graphToYaml(canvasNodes, canvasEdges);

  return (
    <>
      {/* Unsaved changes dialog */}
      <Dialog open={isBlocked} onOpenChange={() => cancelNavigation()}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Unsaved changes</DialogTitle>
            <DialogDescription>
              You have unsaved changes. Are you sure you want to leave?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={cancelNavigation}>
              Stay
            </Button>
            <Button variant="destructive" onClick={confirmNavigation}>
              Discard
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Deploy dialog */}
      <DeployDialog open={deployOpen} onOpenChange={setDeployOpen} yaml={currentYaml} />

      <div className="flex h-full flex-col">
        {/* Top bar */}
        <div className="flex items-center justify-between border-b border-border bg-background px-4 py-2">
          <div className="flex items-center gap-3">
            <Link
              to={id ? `/agents/${id}` : "/agents"}
              className="flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              <ArrowLeft className="size-3.5" />
              Back
            </Link>
            <div className="h-4 w-px bg-border" />
            <h2 className="text-sm font-semibold">
              {id ? "Edit Agent" : "New Agent"}
            </h2>
          </div>

          <div className="flex items-center gap-2">
            {/* Mode toggle */}
            <div className="flex items-center rounded-lg border border-border bg-muted/30 p-0.5">
              <button
                onClick={() => switchMode("yaml")}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-all",
                  mode === "yaml"
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <Code2 className="size-3.5" />
                YAML
              </button>
              <button
                onClick={() => switchMode("visual")}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-all",
                  mode === "visual"
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <LayoutGrid className="size-3.5" />
                Visual
              </button>
              <button
                onClick={() => switchMode("canvas")}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-all",
                  mode === "canvas"
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <Workflow className="size-3.5" />
                Canvas
              </button>
            </div>

            <div className="h-4 w-px bg-border" />

            {/* Sidebar toggles */}
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2"
              onClick={() => setLeftSidebarOpen(!leftSidebarOpen)}
              title="Toggle registry sidebar"
            >
              {leftSidebarOpen ? <PanelLeftClose className="size-3.5" /> : <PanelLeftOpen className="size-3.5" />}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2"
              onClick={() => setRightSidebarOpen(!rightSidebarOpen)}
              title="Toggle validation sidebar"
            >
              {rightSidebarOpen ? <PanelRightClose className="size-3.5" /> : <PanelRightOpen className="size-3.5" />}
            </Button>

            <div className="h-4 w-px bg-border" />

            {/* Actions */}
            <Button size="sm" variant="ghost" className="h-7 gap-1.5 px-2 text-xs" onClick={handleCopy}>
              {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
              {copied ? "Copied" : "Copy"}
            </Button>
            <Button size="sm" variant="outline" className="h-7 gap-1.5 px-2 text-xs" onClick={handleSave}>
              <Save className="size-3.5" />
              Save
            </Button>
            <SubmitForReview
              resourceType="agent"
              resourceName={formData.name || "my-agent"}
              content={currentYaml}
              variant="outline"
              className="h-7 gap-1.5 px-2 text-xs"
            />
            <Button
              size="sm"
              className="h-7 gap-1.5 px-3 text-xs"
              onClick={() => setDeployOpen(true)}
            >
              <Rocket className="size-3.5" />
              Deploy
            </Button>
          </div>
        </div>

        {/* Main content area */}
        <div className="flex flex-1 overflow-hidden">
          {/* Canvas mode takes full width — it has its own palette + property panel */}
          {mode === "canvas" ? (
            <div className="flex-1 overflow-hidden">
              <VisualBuilder
                key="canvas"
                initialNodes={canvasNodes}
                initialEdges={canvasEdges}
                onNodesChange={handleCanvasNodesChange}
                onEdgesChange={handleCanvasEdgesChange}
                framework={formData.framework}
              />
            </div>
          ) : (
            <>
              {/* Left sidebar — Registry picker */}
              {leftSidebarOpen && (
                <aside className="w-72 shrink-0 border-r border-border bg-background">
                  <RegistryPicker
                    onInsertModel={(name) => insertIntoYaml("model", name)}
                    onInsertTool={(ref) => insertIntoYaml("tool", ref)}
                    onInsertPrompt={(ref) => insertIntoYaml("prompt", ref)}
                  />
                </aside>
              )}

              {/* Center — Editor */}
              <div className="flex-1 overflow-hidden">
                {mode === "yaml" ? (
                  <div className="h-full p-4">
                    <YamlEditor yaml={yaml} onChange={handleYamlChange} />
                  </div>
                ) : (
                  <VisualBuilderForm formData={formData} onChange={handleFormDataChange} />
                )}
              </div>

              {/* Right sidebar — Validation */}
              {rightSidebarOpen && (
                <aside className="w-72 shrink-0 border-l border-border bg-background">
                  <ValidationPanel yaml={currentYaml} />
                </aside>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}
