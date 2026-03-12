/**
 * Agent Builder — capstone page for composing agent configurations from
 * all registries (models, tools, prompts). Supports both YAML and Visual modes.
 */

import { useState, useCallback, useMemo, useRef, useEffect } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
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
  Circle,
  X,
  Plus,
  Trash2,
  Loader2,
  Bot,
  Wrench,
  FileText,
  Cpu,
  Shield,
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
import { DeployPipeline } from "@/components/deploy-pipeline";
import { RegistryPicker, getMockModels, getMockTools, getMockPrompts } from "@/components/registry-picker";
import { cn } from "@/lib/utils";
import { highlightYaml, validateYamlBasic } from "@/lib/yaml";
import { api, type Agent, type DeployJobStatus } from "@/lib/api";
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

interface AgentFormData {
  name: string;
  version: string;
  description: string;
  team: string;
  owner: string;
  tags: string[];
  model: {
    primary: string;
    fallback: string;
    temperature: number;
    max_tokens: number;
  };
  framework: string;
  tools: string[];
  prompts: {
    system: string;
  };
  guardrails: string[];
  deploy: {
    cloud: string;
    runtime: string;
    scalingMin: number;
    scalingMax: number;
  };
}

interface ValidationItem {
  key: string;
  label: string;
  status: "pass" | "fail" | "warn";
  message: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formDataToYaml(data: AgentFormData): string {
  const lines: string[] = [];
  lines.push(`name: ${data.name || "my-agent"}`);
  lines.push(`version: "${data.version || "0.1.0"}"`);
  lines.push(`description: "${data.description}"`);
  lines.push(`team: ${data.team || "engineering"}`);
  lines.push(`owner: ${data.owner || "user@example.com"}`);

  if (data.tags.length > 0) {
    lines.push(`tags: [${data.tags.join(", ")}]`);
  }

  lines.push("");
  lines.push("model:");
  lines.push(`  primary: ${data.model.primary || "claude-sonnet-4"}`);
  if (data.model.fallback) {
    lines.push(`  fallback: ${data.model.fallback}`);
  }
  lines.push(`  temperature: ${data.model.temperature}`);
  lines.push(`  max_tokens: ${data.model.max_tokens}`);

  lines.push("");
  lines.push(`framework: ${data.framework || "langgraph"}`);

  lines.push("");
  if (data.tools.length === 0) {
    lines.push("tools: []");
  } else {
    lines.push("tools:");
    for (const tool of data.tools) {
      lines.push(`  - ref: ${tool}`);
    }
  }

  lines.push("");
  lines.push("prompts:");
  if (data.prompts.system.startsWith("prompts/")) {
    lines.push(`  system: ${data.prompts.system}`);
  } else {
    lines.push(`  system: "${data.prompts.system.replace(/"/g, '\\"')}"`);
  }

  lines.push("");
  if (data.guardrails.length === 0) {
    lines.push("guardrails: []");
  } else {
    lines.push("guardrails:");
    for (const g of data.guardrails) {
      lines.push(`  - ${g}`);
    }
  }

  lines.push("");
  lines.push("deploy:");
  lines.push(`  cloud: ${data.deploy.cloud}`);
  lines.push(`  runtime: ${data.deploy.runtime}`);
  lines.push("  scaling:");
  lines.push(`    min: ${data.deploy.scalingMin}`);
  lines.push(`    max: ${data.deploy.scalingMax}`);

  return lines.join("\n");
}

function yamlToFormData(yaml: string): AgentFormData {
  const data: AgentFormData = {
    name: "",
    version: "0.1.0",
    description: "",
    team: "engineering",
    owner: "user@example.com",
    tags: [],
    model: { primary: "claude-sonnet-4", fallback: "", temperature: 0.7, max_tokens: 4096 },
    framework: "langgraph",
    tools: [],
    prompts: { system: "" },
    guardrails: [],
    deploy: { cloud: "local", runtime: "docker-compose", scalingMin: 1, scalingMax: 10 },
  };

  const lines = yaml.split("\n");
  let currentSection = "";

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    // Top-level fields (no leading whitespace)
    if (!line.startsWith(" ") && !line.startsWith("\t")) {
      const colonIdx = trimmed.indexOf(":");
      if (colonIdx === -1) continue;
      const key = trimmed.slice(0, colonIdx).trim();
      const val = trimmed.slice(colonIdx + 1).trim().replace(/^"|"$/g, "");
      currentSection = key;

      switch (key) {
        case "name": data.name = val; break;
        case "version": data.version = val; break;
        case "description": data.description = val; break;
        case "team": data.team = val; break;
        case "owner": data.owner = val; break;
        case "framework": data.framework = val; break;
        case "tags": {
          const tagMatch = val.match(/\[([^\]]*)\]/);
          if (tagMatch) {
            data.tags = tagMatch[1].split(",").map((t) => t.trim()).filter(Boolean);
          }
          break;
        }
        case "tools":
          if (val === "[]") data.tools = [];
          break;
        case "guardrails":
          if (val === "[]") data.guardrails = [];
          break;
      }
    } else {
      // Nested fields
      const colonIdx = trimmed.indexOf(":");
      if (colonIdx === -1) {
        // Array item like "- ref: xxx" or "- pii_detection"
        if (trimmed.startsWith("- ")) {
          const itemVal = trimmed.slice(2).trim();
          if (currentSection === "tools") {
            const refMatch = itemVal.match(/^ref:\s*(.+)$/);
            if (refMatch) data.tools.push(refMatch[1].trim());
          } else if (currentSection === "guardrails") {
            data.guardrails.push(itemVal);
          }
        }
        continue;
      }

      const key = trimmed.slice(0, colonIdx).trim().replace(/^- /, "");
      const val = trimmed.slice(colonIdx + 1).trim().replace(/^"|"$/g, "");

      if (key.startsWith("- ref") && currentSection === "tools") {
        data.tools.push(val);
        continue;
      }
      if (key.startsWith("- ") && currentSection === "guardrails") {
        data.guardrails.push(key.slice(2));
        continue;
      }

      if (currentSection === "model") {
        switch (key) {
          case "primary": data.model.primary = val; break;
          case "fallback": data.model.fallback = val; break;
          case "temperature": data.model.temperature = parseFloat(val) || 0.7; break;
          case "max_tokens": data.model.max_tokens = parseInt(val) || 4096; break;
        }
      } else if (currentSection === "prompts") {
        if (key === "system") data.prompts.system = val;
      } else if (currentSection === "deploy") {
        switch (key) {
          case "cloud": data.deploy.cloud = val; break;
          case "runtime": data.deploy.runtime = val; break;
          case "min": data.deploy.scalingMin = parseInt(val) || 1; break;
          case "max": data.deploy.scalingMax = parseInt(val) || 10; break;
        }
      }
    }
  }

  return data;
}

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
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
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
    },
    [yaml, onChange]
  );

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

function VisualBuilder({
  formData,
  onChange,
}: {
  formData: AgentFormData;
  onChange: (data: AgentFormData) => void;
}) {
  const models = useMemo(() => getMockModels(), []);
  const tools = useMemo(() => getMockTools(), []);
  const [tagInput, setTagInput] = useState("");

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
              <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Primary Model</label>
              <select
                value={formData.model.primary}
                onChange={(e) => update({ model: { ...formData.model, primary: e.target.value } })}
                className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
              >
                <option value="">Select a model...</option>
                {models.map((m) => (
                  <option key={m.id} value={m.name}>
                    {m.name} ({m.provider})
                  </option>
                ))}
              </select>
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
                {models
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
// Deploy Dialog
// ---------------------------------------------------------------------------

function DeployDialog({
  open,
  onOpenChange,
  yaml,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  yaml: string;
}) {
  const [deployStatus, setDeployStatus] = useState<DeployJobStatus | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const formData = useMemo(() => yamlToFormData(yaml), [yaml]);
  const validationItems = useMemo(() => validateAgentYaml(yaml), [yaml]);

  const hasErrors = validationItems.some((i) => i.status === "fail");
  const isDeploying = deployStatus !== null && deployStatus !== "completed" && deployStatus !== "failed";

  const deploySteps: DeployJobStatus[] = [
    "pending",
    "parsing",
    "building",
    "provisioning",
    "deploying",
    "health_checking",
    "registering",
    "completed",
  ];

  const startDeploy = useCallback(() => {
    setDeployStatus("pending");
    setErrorMessage(null);

    let stepIndex = 0;
    const interval = setInterval(() => {
      stepIndex++;
      if (stepIndex < deploySteps.length) {
        setDeployStatus(deploySteps[stepIndex]);
      } else {
        clearInterval(interval);
      }
    }, 800);
  }, []);

  // Reset on close
  useEffect(() => {
    if (!open) {
      setDeployStatus(null);
      setErrorMessage(null);
    }
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Rocket className="size-4" />
            Deploy Agent
          </DialogTitle>
          <DialogDescription>
            Deploy <span className="font-medium">{formData.name || "untitled"}</span> v{formData.version}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Target */}
          <div className="rounded-lg border border-border bg-muted/20 p-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium">Target</span>
              <Badge variant="outline" className="text-[10px]">
                {formData.deploy.cloud === "local" ? "Local Docker" : formData.deploy.cloud}
              </Badge>
            </div>
            {formData.deploy.cloud !== "local" && (
              <p className="mt-1 text-[10px] text-amber-500">
                Cloud deployment coming in v0.3
              </p>
            )}
          </div>

          {/* Pre-deploy checklist */}
          <div>
            <h4 className="mb-2 text-[11px] font-medium">Pre-deploy checklist</h4>
            <div className="space-y-1.5">
              {validationItems
                .filter((i) => ["name", "version", "model", "framework", "deploy"].includes(i.key))
                .map((item) => (
                  <div key={item.key} className="flex items-center gap-2 text-[11px]">
                    <ValidationStatusIcon status={item.status} />
                    <span className={cn(item.status === "fail" && "text-destructive")}>
                      {item.label}: {item.message}
                    </span>
                  </div>
                ))}
            </div>
          </div>

          {/* Deploy pipeline progress */}
          {deployStatus && (
            <div className="pt-2">
              <DeployPipeline status={deployStatus} errorMessage={errorMessage} />
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {deployStatus === "completed" ? "Close" : "Cancel"}
          </Button>
          {!deployStatus && (
            <Button
              onClick={startDeploy}
              disabled={hasErrors}
              className="gap-1.5"
            >
              <Rocket className="size-3.5" />
              Deploy
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function AgentBuilderPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { toast } = useToast();
  const { isDirty, markDirty, markClean, isBlocked, confirmNavigation, cancelNavigation } = useUnsavedChanges();

  const [mode, setMode] = useState<"yaml" | "visual">("yaml");
  const [leftSidebarOpen, setLeftSidebarOpen] = useState(true);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(true);
  const [yaml, setYaml] = useState(DEFAULT_YAML);
  const [formData, setFormData] = useState<AgentFormData>(() => yamlToFormData(DEFAULT_YAML));
  const [deployOpen, setDeployOpen] = useState(false);
  const [copied, setCopied] = useState(false);

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
        const loadedFormData: AgentFormData = {
          name: agent.name,
          version: agent.version,
          description: agent.description,
          team: agent.team,
          owner: agent.owner,
          tags: agent.tags ?? [],
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
          deploy: {
            cloud: ((config.deploy as Record<string, unknown>)?.cloud as string) ?? "local",
            runtime: ((config.deploy as Record<string, unknown>)?.runtime as string) ?? "docker-compose",
            scalingMin: 1,
            scalingMax: 10,
          },
        };
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
    (newMode: "yaml" | "visual") => {
      if (newMode === "visual" && mode === "yaml") {
        setFormData(yamlToFormData(yaml));
      } else if (newMode === "yaml" && mode === "visual") {
        setYaml(formDataToYaml(formData));
      }
      setMode(newMode);
    },
    [mode, yaml, formData]
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

  // Current YAML for validation panel (sync from visual mode if needed)
  const currentYaml = mode === "yaml" ? yaml : formDataToYaml(formData);

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
              <VisualBuilder formData={formData} onChange={handleFormDataChange} />
            )}
          </div>

          {/* Right sidebar — Validation */}
          {rightSidebarOpen && (
            <aside className="w-72 shrink-0 border-l border-border bg-background">
              <ValidationPanel yaml={currentYaml} />
            </aside>
          )}
        </div>
      </div>
    </>
  );
}
