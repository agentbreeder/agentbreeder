/**
 * Registry Picker — collapsible sidebar with searchable lists of registry
 * resources (Models, Tools, Prompts) for inserting YAML references.
 */

import { useState, useMemo } from "react";
import {
  ChevronDown,
  ChevronRight,
  Search,
  Plus,
  Cpu,
  Wrench,
  FileText,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Mock registry data
// ---------------------------------------------------------------------------

/**
 * Model lifecycle status (Track G — #163).
 * - active: GA model, picker shows it by default
 * - beta: usable but warn user
 * - deprecated: hidden from picker unless `Show deprecated` toggled
 * - retired: hidden from picker unless `Show deprecated` toggled
 */
export type ModelStatus = "active" | "beta" | "deprecated" | "retired";

export interface RegistryModel {
  id: string;
  name: string;
  provider: "anthropic" | "openai" | "google" | "ollama" | "openrouter";
  description: string;
  version: string;
  inputPrice: string;
  outputPrice: string;
  /** Track G — lifecycle status. Defaults to "active" if absent. */
  status?: ModelStatus;
}

export interface RegistryTool {
  id: string;
  name: string;
  toolType: "mcp" | "function";
  description: string;
  version: string;
}

export interface RegistryPrompt {
  id: string;
  name: string;
  description: string;
  version: string;
  preview: string;
}

const MOCK_MODELS: RegistryModel[] = [
  { id: "m1", name: "claude-sonnet-4", provider: "anthropic", description: "Fast, balanced model for everyday tasks", version: "2025-06", inputPrice: "$3.00", outputPrice: "$15.00", status: "active" },
  { id: "m2", name: "claude-opus-4", provider: "anthropic", description: "Most capable model for complex reasoning", version: "2025-04", inputPrice: "$15.00", outputPrice: "$75.00", status: "active" },
  { id: "m3", name: "gpt-4o", provider: "openai", description: "Multimodal flagship model", version: "2025-02", inputPrice: "$2.50", outputPrice: "$10.00", status: "active" },
  { id: "m4", name: "gpt-4o-mini", provider: "openai", description: "Affordable small model for fast tasks", version: "2025-02", inputPrice: "$0.15", outputPrice: "$0.60", status: "active" },
  { id: "m5", name: "gemini-2.5-pro", provider: "google", description: "Google thinking model with deep research", version: "2025-03", inputPrice: "$1.25", outputPrice: "$10.00", status: "active" },
  { id: "m6", name: "llama-3.3-70b", provider: "ollama", description: "Open-weight model, self-hosted", version: "3.3", inputPrice: "Free", outputPrice: "Free", status: "active" },
  { id: "m7", name: "deepseek-r1", provider: "openrouter", description: "Reasoning model via OpenRouter", version: "2025-01", inputPrice: "$0.55", outputPrice: "$2.19", status: "beta" },
  // Deprecated/retired examples — hidden from picker unless `Show deprecated` toggled.
  { id: "m8", name: "claude-3-opus", provider: "anthropic", description: "Superseded by claude-opus-4", version: "2024-02", inputPrice: "$15.00", outputPrice: "$75.00", status: "deprecated" },
  { id: "m9", name: "gpt-3.5-turbo", provider: "openai", description: "Retired — use gpt-4o-mini", version: "2023-11", inputPrice: "$0.50", outputPrice: "$1.50", status: "retired" },
];

/**
 * Filter out deprecated/retired models unless the caller wants them shown.
 * Used by the agent-builder model picker — see issue #204 / Track G (#163).
 */
export function filterModelsByStatus(
  models: RegistryModel[],
  showDeprecated: boolean,
): RegistryModel[] {
  if (showDeprecated) return models;
  return models.filter((m) => {
    const status = m.status ?? "active";
    return status !== "deprecated" && status !== "retired";
  });
}

const MOCK_TOOLS: RegistryTool[] = [
  { id: "t1", name: "web-search", toolType: "mcp", description: "Search the web using Brave Search API", version: "1.2.0" },
  { id: "t2", name: "zendesk-mcp", toolType: "mcp", description: "Read and manage Zendesk tickets", version: "2.0.0" },
  { id: "t3", name: "order-lookup", toolType: "function", description: "Look up order status by order ID", version: "1.0.0" },
  { id: "t4", name: "github-mcp", toolType: "mcp", description: "Interact with GitHub repos, issues, PRs", version: "1.5.0" },
  { id: "t5", name: "slack-mcp", toolType: "mcp", description: "Send messages and manage Slack channels", version: "1.1.0" },
  { id: "t6", name: "calculator", toolType: "function", description: "Perform mathematical calculations", version: "1.0.0" },
  { id: "t7", name: "postgres-mcp", toolType: "mcp", description: "Query PostgreSQL databases safely", version: "2.1.0" },
  { id: "t8", name: "filesystem-mcp", toolType: "mcp", description: "Read and write files on the local filesystem", version: "1.3.0" },
];

const MOCK_PROMPTS: RegistryPrompt[] = [
  { id: "p1", name: "support-system-v3", description: "Customer support system prompt", version: "3.0.0", preview: "You are a helpful customer support agent..." },
  { id: "p2", name: "code-review-system", description: "Code review assistant prompt", version: "1.2.0", preview: "You are an expert code reviewer..." },
  { id: "p3", name: "data-analyst-system", description: "Data analysis and reporting prompt", version: "2.0.0", preview: "You are a data analyst who excels at..." },
  { id: "p4", name: "document-summarizer", description: "Document summarization prompt", version: "1.0.0", preview: "Summarize the following document concisely..." },
  { id: "p5", name: "safety-guardrail", description: "Safety-focused system prompt", version: "1.1.0", preview: "You must follow these safety guidelines..." },
];

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  openai: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  google: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  ollama: "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20",
  openrouter: "bg-pink-500/10 text-pink-600 dark:text-pink-400 border-pink-500/20",
};

const TOOL_TYPE_COLORS: Record<string, string> = {
  mcp: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  function: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20",
};

// ---------------------------------------------------------------------------
// Accordion Section
// ---------------------------------------------------------------------------

interface AccordionSectionProps {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  count: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

function AccordionSection({ title, icon: Icon, count, defaultOpen = false, children }: AccordionSectionProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="border-b border-border/50 last:border-0">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-xs font-medium transition-colors hover:bg-muted/30"
      >
        {open ? (
          <ChevronDown className="size-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 text-muted-foreground" />
        )}
        <Icon className="size-3.5" />
        <span className="flex-1">{title}</span>
        <Badge variant="outline" className="h-4 px-1.5 text-[9px]">
          {count}
        </Badge>
      </button>
      {open && <div className="px-2 pb-2">{children}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Registry Picker
// ---------------------------------------------------------------------------

interface RegistryPickerProps {
  onInsertModel: (modelName: string) => void;
  onInsertTool: (toolRef: string) => void;
  onInsertPrompt: (promptRef: string) => void;
}

export function RegistryPicker({ onInsertModel, onInsertTool, onInsertPrompt }: RegistryPickerProps) {
  const [modelSearch, setModelSearch] = useState("");
  const [toolSearch, setToolSearch] = useState("");
  const [promptSearch, setPromptSearch] = useState("");

  const filteredModels = useMemo(
    () =>
      MOCK_MODELS.filter(
        (m) =>
          m.name.toLowerCase().includes(modelSearch.toLowerCase()) ||
          m.provider.toLowerCase().includes(modelSearch.toLowerCase())
      ),
    [modelSearch]
  );

  const filteredTools = useMemo(
    () =>
      MOCK_TOOLS.filter(
        (t) =>
          t.name.toLowerCase().includes(toolSearch.toLowerCase()) ||
          t.description.toLowerCase().includes(toolSearch.toLowerCase())
      ),
    [toolSearch]
  );

  const filteredPrompts = useMemo(
    () =>
      MOCK_PROMPTS.filter(
        (p) =>
          p.name.toLowerCase().includes(promptSearch.toLowerCase()) ||
          p.description.toLowerCase().includes(promptSearch.toLowerCase())
      ),
    [promptSearch]
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-border px-3 py-2.5">
        <h3 className="text-xs font-semibold tracking-tight">Registry</h3>
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          Click Add to insert references
        </p>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Models */}
        <AccordionSection title="Models" icon={Cpu} count={MOCK_MODELS.length} defaultOpen>
          <div className="mb-2">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 size-3 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Filter models..."
                value={modelSearch}
                onChange={(e) => setModelSearch(e.target.value)}
                className="h-7 pl-7 text-[11px]"
              />
            </div>
          </div>
          <div className="space-y-1">
            {filteredModels.map((model) => (
              <div
                key={model.id}
                className="group flex items-start gap-2 rounded-md border border-transparent p-2 transition-colors hover:border-border hover:bg-muted/30"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-[11px] font-medium">{model.name}</span>
                    <Badge
                      variant="outline"
                      className={cn("h-3.5 px-1 text-[8px]", PROVIDER_COLORS[model.provider])}
                    >
                      {model.provider}
                    </Badge>
                  </div>
                  <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                    {model.description}
                  </p>
                  <p className="mt-0.5 text-[9px] text-muted-foreground/70">
                    In: {model.inputPrice} / Out: {model.outputPrice} per 1M tokens
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-1.5 text-[10px] opacity-0 transition-opacity group-hover:opacity-100"
                  onClick={() => onInsertModel(model.name)}
                >
                  <Plus className="mr-0.5 size-3" />
                  Add
                </Button>
              </div>
            ))}
            {filteredModels.length === 0 && (
              <p className="px-2 py-3 text-center text-[10px] text-muted-foreground">
                No models match your filter.
              </p>
            )}
          </div>
        </AccordionSection>

        {/* Tools */}
        <AccordionSection title="Tools" icon={Wrench} count={MOCK_TOOLS.length}>
          <div className="mb-2">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 size-3 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Filter tools..."
                value={toolSearch}
                onChange={(e) => setToolSearch(e.target.value)}
                className="h-7 pl-7 text-[11px]"
              />
            </div>
          </div>
          <div className="space-y-1">
            {filteredTools.map((tool) => (
              <div
                key={tool.id}
                className="group flex items-start gap-2 rounded-md border border-transparent p-2 transition-colors hover:border-border hover:bg-muted/30"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-[11px] font-medium">{tool.name}</span>
                    <Badge
                      variant="outline"
                      className={cn("h-3.5 px-1 text-[8px]", TOOL_TYPE_COLORS[tool.toolType])}
                    >
                      {tool.toolType}
                    </Badge>
                    <span className="text-[9px] text-muted-foreground">v{tool.version}</span>
                  </div>
                  <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                    {tool.description}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-1.5 text-[10px] opacity-0 transition-opacity group-hover:opacity-100"
                  onClick={() => onInsertTool(`tools/${tool.name}`)}
                >
                  <Plus className="mr-0.5 size-3" />
                  Add
                </Button>
              </div>
            ))}
            {filteredTools.length === 0 && (
              <p className="px-2 py-3 text-center text-[10px] text-muted-foreground">
                No tools match your filter.
              </p>
            )}
          </div>
        </AccordionSection>

        {/* Prompts */}
        <AccordionSection title="Prompts" icon={FileText} count={MOCK_PROMPTS.length}>
          <div className="mb-2">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 size-3 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Filter prompts..."
                value={promptSearch}
                onChange={(e) => setPromptSearch(e.target.value)}
                className="h-7 pl-7 text-[11px]"
              />
            </div>
          </div>
          <div className="space-y-1">
            {filteredPrompts.map((prompt) => (
              <div
                key={prompt.id}
                className="group flex items-start gap-2 rounded-md border border-transparent p-2 transition-colors hover:border-border hover:bg-muted/30"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-[11px] font-medium">{prompt.name}</span>
                    <span className="text-[9px] text-muted-foreground">v{prompt.version}</span>
                  </div>
                  <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                    {prompt.description}
                  </p>
                  <p className="mt-0.5 truncate text-[9px] italic text-muted-foreground/70">
                    &ldquo;{prompt.preview}&rdquo;
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-1.5 text-[10px] opacity-0 transition-opacity group-hover:opacity-100"
                  onClick={() => onInsertPrompt(`prompts/${prompt.name}`)}
                >
                  <Plus className="mr-0.5 size-3" />
                  Add
                </Button>
              </div>
            ))}
            {filteredPrompts.length === 0 && (
              <p className="px-2 py-3 text-center text-[10px] text-muted-foreground">
                No prompts match your filter.
              </p>
            )}
          </div>
        </AccordionSection>
      </div>
    </div>
  );
}

/**
 * Get mock models for use in the visual builder dropdowns.
 */
export function getMockModels(): RegistryModel[] {
  return MOCK_MODELS;
}

/**
 * Get mock tools for use in the visual builder tool picker.
 */
export function getMockTools(): RegistryTool[] {
  return MOCK_TOOLS;
}

/**
 * Get mock prompts for use in the visual builder prompt picker.
 */
export function getMockPrompts(): RegistryPrompt[] {
  return MOCK_PROMPTS;
}
