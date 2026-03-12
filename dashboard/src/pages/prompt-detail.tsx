import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate, Link } from "react-router-dom";
import {
  ArrowLeft,
  Save,
  Trash2,
  Copy,
  Clock,
  Users,
  FileText,
  Loader2,
  Check,
  Eye,
  Pencil,
  Bold,
  Italic,
  Code,
  Link2,
  Heading,
  History,
  GitCompare,
  RotateCcw,
  Layers,
  Play,
  FlaskConical,
  ChevronDown,
  Hash,
} from "lucide-react";
import { api, type Prompt, type PromptVersion } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { useState, useCallback, useMemo, useRef } from "react";
import { useToast } from "@/hooks/use-toast";

/** Lightweight Markdown-to-HTML renderer for the preview panel. */
function renderMarkdown(md: string): string {
  let html = md
    // Escape HTML
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Code blocks (``` ... ```)
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_match, _lang, code) => {
    return `<pre class="rounded-md bg-muted/70 px-3 py-2 text-xs leading-relaxed overflow-x-auto"><code>${code.trim()}</code></pre>`;
  });

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code class="rounded bg-muted px-1 py-0.5 text-xs font-mono">$1</code>');

  // Headers
  html = html.replace(/^### (.+)$/gm, '<h3 class="text-sm font-semibold mt-4 mb-1">$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2 class="text-base font-semibold mt-4 mb-1">$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1 class="text-lg font-bold mt-4 mb-2">$1</h1>');

  // Bold and italic
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

  // Links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" class="text-blue-600 dark:text-blue-400 underline">$1</a>');

  // Unordered lists
  html = html.replace(/^- (.+)$/gm, '<li class="ml-4 list-disc text-sm">$1</li>');

  // Ordered lists
  html = html.replace(/^\d+\. (.+)$/gm, '<li class="ml-4 list-decimal text-sm">$1</li>');

  // Horizontal rule
  html = html.replace(/^---$/gm, '<hr class="my-3 border-border" />');

  // Paragraphs: wrap remaining lines (that are not already tags) in <p>
  html = html
    .split("\n\n")
    .map((block) => {
      const trimmed = block.trim();
      if (!trimmed) return "";
      if (trimmed.startsWith("<")) return trimmed;
      return `<p class="text-sm leading-relaxed mb-2">${trimmed.replace(/\n/g, "<br/>")}</p>`;
    })
    .join("\n");

  return html;
}

/** Simple line-by-line diff between two texts. Returns arrays of diff lines. */
function computeDiff(
  oldText: string,
  newText: string
): { left: DiffLine[]; right: DiffLine[] } {
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");
  const left: DiffLine[] = [];
  const right: DiffLine[] = [];

  // Simple LCS-based diff
  const lcs = buildLCS(oldLines, newLines);

  let oi = 0;
  let ni = 0;
  let li = 0;

  while (oi < oldLines.length || ni < newLines.length) {
    if (li < lcs.length && oi < oldLines.length && ni < newLines.length && oldLines[oi] === lcs[li] && newLines[ni] === lcs[li]) {
      // Common line
      left.push({ text: oldLines[oi], type: "same" });
      right.push({ text: newLines[ni], type: "same" });
      oi++;
      ni++;
      li++;
    } else if (oi < oldLines.length && (li >= lcs.length || oldLines[oi] !== lcs[li])) {
      left.push({ text: oldLines[oi], type: "removed" });
      right.push({ text: "", type: "spacer" });
      oi++;
    } else if (ni < newLines.length && (li >= lcs.length || newLines[ni] !== lcs[li])) {
      left.push({ text: "", type: "spacer" });
      right.push({ text: newLines[ni], type: "added" });
      ni++;
    } else {
      // Safety break
      break;
    }
  }

  return { left, right };
}

interface DiffLine {
  text: string;
  type: "same" | "added" | "removed" | "spacer";
}

function buildLCS(a: string[], b: string[]): string[] {
  const m = a.length;
  const n = b.length;
  // For very large texts, limit to prevent performance issues
  if (m > 500 || n > 500) {
    // Fallback: simple line-by-line comparison
    const result: string[] = [];
    let bi = 0;
    for (let ai = 0; ai < m && bi < n; ai++) {
      if (a[ai] === b[bi]) {
        result.push(a[ai]);
        bi++;
      }
    }
    return result;
  }

  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  const result: string[] = [];
  let i = m;
  let j = n;
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      result.unshift(a[i - 1]);
      i--;
      j--;
    } else if (dp[i - 1][j] > dp[i][j - 1]) {
      i--;
    } else {
      j--;
    }
  }
  return result;
}

/** Toolbar that inserts Markdown syntax into the editor. */
function EditorToolbar({
  textareaRef,
  content,
  onChange,
}: {
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  content: string;
  onChange: (value: string) => void;
}) {
  const insertSyntax = useCallback(
    (before: string, after: string, placeholder: string) => {
      const textarea = textareaRef.current;
      if (!textarea) return;
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const selected = content.substring(start, end);
      const text = selected || placeholder;
      const newContent =
        content.substring(0, start) + before + text + after + content.substring(end);
      onChange(newContent);
      requestAnimationFrame(() => {
        textarea.focus();
        const cursorStart = start + before.length;
        const cursorEnd = cursorStart + text.length;
        textarea.selectionStart = cursorStart;
        textarea.selectionEnd = cursorEnd;
      });
    },
    [textareaRef, content, onChange]
  );

  const buttons = [
    { icon: Bold, label: "Bold", before: "**", after: "**", placeholder: "bold text" },
    { icon: Italic, label: "Italic", before: "*", after: "*", placeholder: "italic text" },
    { icon: Code, label: "Code", before: "`", after: "`", placeholder: "code" },
    { icon: Link2, label: "Link", before: "[", after: "](url)", placeholder: "link text" },
    { icon: Heading, label: "Heading", before: "## ", after: "", placeholder: "heading" },
  ];

  return (
    <div className="flex items-center gap-0.5 border-b border-border bg-muted/30 px-2 py-1">
      {buttons.map(({ icon: Icon, label, before, after, placeholder }) => (
        <button
          key={label}
          type="button"
          title={label}
          onClick={() => insertSyntax(before, after, placeholder)}
          className="flex size-7 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Icon className="size-3.5" />
        </button>
      ))}
    </div>
  );
}

/** Version timeline for the Versions tab. */
function VersionTimeline({
  versions,
  currentId,
  onSelect,
  onRestore,
  selectedForCompare,
  onToggleCompare,
}: {
  versions: Prompt[];
  currentId: string;
  onSelect: (version: Prompt) => void;
  onRestore: (version: Prompt) => void;
  selectedForCompare: Set<string>;
  onToggleCompare: (id: string) => void;
}) {
  return (
    <div className="space-y-2">
      {versions.map((v, i) => (
        <div
          key={v.id}
          className={cn(
            "group relative rounded-lg border border-border p-3 transition-colors hover:bg-muted/20",
            v.id === currentId && "border-foreground/20 bg-muted/10"
          )}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="font-mono text-[10px]">
                  v{v.version}
                </Badge>
                {i === 0 && (
                  <span className="text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                    latest
                  </span>
                )}
                <span className="text-[10px] text-muted-foreground">
                  {new Date(v.created_at).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                  })}
                </span>
              </div>
              <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                {v.content.substring(0, 100)}
                {v.content.length > 100 ? "..." : ""}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
              <label className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <input
                  type="checkbox"
                  checked={selectedForCompare.has(v.id)}
                  onChange={() => onToggleCompare(v.id)}
                  className="size-3 rounded"
                />
                Compare
              </label>
              <Button
                variant="outline"
                size="sm"
                className="h-6 px-2 text-[10px]"
                onClick={() => onSelect(v)}
              >
                <Eye className="size-2.5" />
                View
              </Button>
              {i > 0 && (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-6 px-2 text-[10px]"
                  onClick={() => onRestore(v)}
                >
                  <RotateCcw className="size-2.5" />
                  Restore
                </Button>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

/** Diff viewer showing two versions side by side. */
function DiffViewer({
  leftVersion,
  rightVersion,
}: {
  leftVersion: Prompt;
  rightVersion: Prompt;
}) {
  const { left, right } = useMemo(
    () => computeDiff(leftVersion.content, rightVersion.content),
    [leftVersion.content, rightVersion.content]
  );

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      {/* Header */}
      <div className="flex border-b border-border bg-muted/30">
        <div className="flex-1 border-r border-border px-3 py-2">
          <span className="text-xs font-medium">v{leftVersion.version}</span>
          <span className="ml-2 text-[10px] text-muted-foreground">
            {new Date(leftVersion.created_at).toLocaleDateString()}
          </span>
        </div>
        <div className="flex-1 px-3 py-2">
          <span className="text-xs font-medium">v{rightVersion.version}</span>
          <span className="ml-2 text-[10px] text-muted-foreground">
            {new Date(rightVersion.created_at).toLocaleDateString()}
          </span>
        </div>
      </div>
      {/* Diff content */}
      <div className="flex max-h-[60vh] overflow-auto">
        <div className="flex-1 border-r border-border">
          {left.map((line, i) => (
            <div
              key={i}
              className={cn(
                "flex min-h-[1.5rem] items-start gap-2 px-3 py-0.5 font-mono text-xs",
                line.type === "removed" && "bg-red-500/10 text-red-700 dark:text-red-400",
                line.type === "spacer" && "bg-muted/20"
              )}
            >
              <span className="w-6 shrink-0 select-none text-right text-muted-foreground/50">
                {line.type !== "spacer" ? i + 1 : ""}
              </span>
              <span className="whitespace-pre-wrap break-all">
                {line.type === "removed" && <span className="mr-1 select-none">-</span>}
                {line.text}
              </span>
            </div>
          ))}
        </div>
        <div className="flex-1">
          {right.map((line, i) => (
            <div
              key={i}
              className={cn(
                "flex min-h-[1.5rem] items-start gap-2 px-3 py-0.5 font-mono text-xs",
                line.type === "added" && "bg-green-500/10 text-green-700 dark:text-green-400",
                line.type === "spacer" && "bg-muted/20"
              )}
            >
              <span className="w-6 shrink-0 select-none text-right text-muted-foreground/50">
                {line.type !== "spacer" ? i + 1 : ""}
              </span>
              <span className="whitespace-pre-wrap break-all">
                {line.type === "added" && <span className="mr-1 select-none">+</span>}
                {line.text}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Version content viewer in a modal-like panel. */
function VersionContentViewer({
  version,
  onClose,
}: {
  version: Prompt;
  onClose: () => void;
}) {
  const previewHtml = useMemo(() => renderMarkdown(version.content), [version.content]);

  return (
    <div className="rounded-lg border border-border">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="font-mono text-[10px]">
            v{version.version}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {new Date(version.created_at).toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
              year: "numeric",
            })}
          </span>
        </div>
        <Button variant="outline" size="sm" className="h-6 px-2 text-[10px]" onClick={onClose}>
          Close
        </Button>
      </div>
      <div className="max-h-[60vh] overflow-auto p-4">
        <div
          className="prose-sm max-w-none text-foreground"
          dangerouslySetInnerHTML={{ __html: previewHtml }}
        />
      </div>
    </div>
  );
}

/** Extract {{variable}} placeholders from prompt content. */
function extractVariables(content: string): string[] {
  const matches = content.match(/\{\{(\w+)\}\}/g);
  if (!matches) return [];
  return [...new Set(matches.map((m) => m.replace(/\{\{|\}\}/g, "")))];
}

/** Substitute {{variable}} placeholders with provided values. */
function renderTemplate(content: string, variables: Record<string, string>): string {
  let rendered = content;
  for (const [key, value] of Object.entries(variables)) {
    rendered = rendered.replace(new RegExp(`\\{\\{${key}\\}\\}`, "g"), value || `{{${key}}}`);
  }
  return rendered;
}

/** Estimate token count (~4 chars per token). */
function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

const MOCK_RESPONSES: Record<string, string> = {
  "gpt-4o":
    "Based on the prompt provided, I would approach this task by first analyzing the key requirements and then structuring a comprehensive response. The system prompt establishes clear guidelines for interaction, and I will adhere to those parameters while providing helpful, accurate information tailored to the user's needs.",
  "claude-sonnet-4":
    "I understand the context established by this prompt. Let me work through the request systematically. The prompt provides a well-structured framework for interaction, and I will respond within those boundaries while offering thorough, nuanced analysis of the topic at hand.",
  "llama3.2":
    "Following the instructions in the prompt, I will provide a focused and relevant response. The guidelines are clear, and I aim to deliver practical, actionable information that aligns with the stated objectives and constraints.",
};

const TEST_MODELS = ["gpt-4o", "claude-sonnet-4", "llama3.2"] as const;

/** Prompt Test Panel component. */
function PromptTestPanel({ content }: { content: string }) {
  const [selectedModel, setSelectedModel] = useState<string>(TEST_MODELS[0]);
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [isRunning, setIsRunning] = useState(false);
  const [testResponse, setTestResponse] = useState<string | null>(null);
  const [showRendered, setShowRendered] = useState(false);

  const templateVars = useMemo(() => extractVariables(content), [content]);
  const renderedContent = useMemo(
    () => renderTemplate(content, variables),
    [content, variables]
  );
  const tokenEstimate = useMemo(() => estimateTokens(renderedContent), [renderedContent]);

  const handleRunTest = useCallback(() => {
    setIsRunning(true);
    setTestResponse(null);
    // Simulate a 1.5s delay for mock LLM response
    setTimeout(() => {
      setTestResponse(
        MOCK_RESPONSES[selectedModel] ??
          "Response generated successfully. This is a placeholder response since LLM integration is not yet configured."
      );
      setIsRunning(false);
    }, 1500);
  }, [selectedModel]);

  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        {/* Left: Configuration */}
        <div className="space-y-4">
          {/* Model selector */}
          <div className="rounded-lg border border-border p-4">
            <label className="mb-2 block text-xs font-medium">Model</label>
            <div className="relative">
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                className="h-8 w-full appearance-none rounded-md border border-input bg-background px-3 pr-8 text-xs outline-none focus:ring-1 focus:ring-ring"
              >
                {TEST_MODELS.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2 top-2 size-3.5 text-muted-foreground" />
            </div>
          </div>

          {/* Template variables */}
          {templateVars.length > 0 && (
            <div className="rounded-lg border border-border p-4">
              <label className="mb-2 block text-xs font-medium">
                Template Variables
              </label>
              <div className="space-y-2">
                {templateVars.map((varName) => (
                  <div key={varName}>
                    <label className="mb-1 block text-[10px] font-mono text-muted-foreground">
                      {`{{${varName}}}`}
                    </label>
                    <input
                      type="text"
                      value={variables[varName] ?? ""}
                      onChange={(e) =>
                        setVariables((prev) => ({
                          ...prev,
                          [varName]: e.target.value,
                        }))
                      }
                      placeholder={`Enter value for ${varName}...`}
                      className="h-7 w-full rounded-md border border-input bg-background px-2 text-xs outline-none focus:ring-1 focus:ring-ring"
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Token estimate & run button */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
              <Hash className="size-3" />
              <span>~{tokenEstimate.toLocaleString()} tokens (estimated)</span>
            </div>
            <Button
              size="sm"
              onClick={handleRunTest}
              disabled={isRunning}
            >
              {isRunning ? (
                <Loader2 className="size-3 animate-spin" data-icon="inline-start" />
              ) : (
                <Play className="size-3" data-icon="inline-start" />
              )}
              {isRunning ? "Running..." : "Send Test"}
            </Button>
          </div>
        </div>

        {/* Right: Rendered prompt preview */}
        <div className="rounded-lg border border-border">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <span className="text-xs font-medium">
              {showRendered ? "Rendered Prompt" : "Raw Prompt"}
            </span>
            <button
              onClick={() => setShowRendered(!showRendered)}
              className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
            >
              {showRendered ? "Show raw" : "Show rendered"}
            </button>
          </div>
          <div className="max-h-[40vh] overflow-auto p-3">
            <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-foreground">
              {showRendered ? renderedContent : content}
            </pre>
          </div>
        </div>
      </div>

      {/* Test response */}
      {(testResponse || isRunning) && (
        <div className="rounded-lg border border-border">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <span className="text-xs font-medium">Response</span>
            <Badge variant="outline" className="text-[9px]">
              {selectedModel}
            </Badge>
            {testResponse && (
              <span className="ml-auto text-[10px] text-muted-foreground">
                ~{estimateTokens(testResponse).toLocaleString()} tokens
              </span>
            )}
          </div>
          <div className="p-4">
            {isRunning ? (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="size-3 animate-spin" />
                Generating response...
              </div>
            ) : (
              <p className="text-sm leading-relaxed text-foreground">
                {testResponse}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Version history panel that fetches real data from the API. */
function VersionHistoryPanel({
  promptId,
  versions,
  currentId,
  onRestore,
}: {
  promptId: string;
  versions: PromptVersion[];
  currentId: string;
  onRestore: (content: string) => void;
}) {
  const [viewingVersion, setViewingVersion] = useState<PromptVersion | null>(null);

  if (viewingVersion) {
    return (
      <div className="rounded-lg border border-border">
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="font-mono text-[10px]">
              Version {viewingVersion.version_number}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {new Date(viewingVersion.created_at).toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
                year: "numeric",
              })}
            </span>
            {viewingVersion.change_summary && (
              <span className="text-[10px] text-muted-foreground italic">
                {viewingVersion.change_summary}
              </span>
            )}
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-[10px]"
            onClick={() => setViewingVersion(null)}
          >
            Close
          </Button>
        </div>
        <div className="max-h-[60vh] overflow-auto p-4">
          <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-foreground">
            {viewingVersion.content}
          </pre>
        </div>
      </div>
    );
  }

  if (versions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <History className="mb-3 size-8 text-muted-foreground/30" />
        <p className="text-sm text-muted-foreground">
          No version snapshots yet. Save a version to start tracking history.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {versions.map((v, i) => (
        <div
          key={v.id}
          className="group relative rounded-lg border border-border p-3 transition-colors hover:bg-muted/20"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="font-mono text-[10px]">
                  Version {v.version_number}
                </Badge>
                {i === 0 && (
                  <span className="text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                    latest
                  </span>
                )}
                <span className="text-[10px] text-muted-foreground">
                  {new Date(v.created_at).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                  })}
                </span>
                {v.created_by && (
                  <span className="text-[10px] text-muted-foreground">
                    by {v.created_by}
                  </span>
                )}
              </div>
              {v.change_summary && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {v.change_summary}
                </p>
              )}
              <p className="mt-1 line-clamp-2 text-xs text-muted-foreground/60 font-mono">
                {v.content.substring(0, 120)}
                {v.content.length > 120 ? "..." : ""}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
              <Button
                variant="outline"
                size="sm"
                className="h-6 px-2 text-[10px]"
                onClick={() => setViewingVersion(v)}
              >
                <Eye className="size-2.5" />
                View
              </Button>
              {i > 0 && (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-6 px-2 text-[10px]"
                  onClick={() => {
                    if (
                      window.confirm(
                        `Restore version ${v.version_number}? This will update the current content.`
                      )
                    ) {
                      onRestore(v.content);
                    }
                  }}
                >
                  <RotateCcw className="size-2.5" />
                  Restore
                </Button>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

const TAB_TRIGGER_CLASS =
  "gap-1 rounded-none border-b-2 border-transparent px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors data-active:border-foreground data-active:text-foreground data-active:shadow-none hover:text-foreground";

export default function PromptDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const [editedContent, setEditedContent] = useState<string | null>(null);
  const [editedDescription, setEditedDescription] = useState<string | null>(null);
  const [activeEditorTab, setActiveEditorTab] = useState<"edit" | "preview">("edit");
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Version history state
  const [viewingVersion, setViewingVersion] = useState<Prompt | null>(null);
  const [compareSelection, setCompareSelection] = useState<Set<string>>(new Set());

  const { data: promptData, isLoading, error } = useQuery({
    queryKey: ["prompt", id],
    queryFn: () => api.prompts.get(id!),
    enabled: !!id,
  });

  const { data: versionsData } = useQuery({
    queryKey: ["prompt-versions", id],
    queryFn: () => api.prompts.versions(id!),
    enabled: !!id,
  });

  const { data: versionHistoryData } = useQuery({
    queryKey: ["prompt-version-history", id],
    queryFn: () => api.prompts.versionHistory(id!),
    enabled: !!id,
  });

  const prompt = promptData?.data;
  const versions = versionsData?.data ?? [];
  const versionHistory = versionHistoryData?.data ?? [];

  const content = editedContent ?? prompt?.content ?? "";
  const description = editedDescription ?? prompt?.description ?? "";
  const hasChanges = editedContent !== null || editedDescription !== null;

  const saveMutation = useMutation({
    mutationFn: async () => {
      const result = await api.prompts.update(id!, {
        ...(editedContent !== null ? { content: editedContent } : {}),
        ...(editedDescription !== null ? { description: editedDescription } : {}),
      });
      // Also create a version snapshot when content changes
      if (editedContent !== null) {
        await api.prompts.createVersion(id!, {
          content: editedContent,
          change_summary: "Content updated",
        }).catch(() => {
          // Version creation is best-effort; don't fail the save
        });
      }
      return result;
    },
    onSuccess: () => {
      setEditedContent(null);
      setEditedDescription(null);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2000);
      queryClient.invalidateQueries({ queryKey: ["prompt", id] });
      queryClient.invalidateQueries({ queryKey: ["prompt-versions", id] });
      queryClient.invalidateQueries({ queryKey: ["prompt-version-history", id] });
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      toast({ title: "Prompt saved", variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: "Failed to save", description: err.message, variant: "error" });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.prompts.delete(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      toast({ title: "Prompt deleted", variant: "success" });
      navigate("/prompts");
    },
    onError: (err: Error) => {
      toast({ title: "Failed to delete", description: err.message, variant: "error" });
    },
  });

  const duplicateMutation = useMutation({
    mutationFn: () => api.prompts.duplicate(id!),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      toast({ title: "Prompt duplicated", variant: "success" });
      navigate(`/prompts/${data.data.id}`);
    },
    onError: (err: Error) => {
      toast({ title: "Failed to duplicate", description: err.message, variant: "error" });
    },
  });

  const restoreMutation = useMutation({
    mutationFn: (versionContent: string) =>
      api.prompts.update(id!, { content: versionContent }),
    onSuccess: () => {
      setEditedContent(null);
      queryClient.invalidateQueries({ queryKey: ["prompt", id] });
      queryClient.invalidateQueries({ queryKey: ["prompt-versions", id] });
      toast({ title: "Version restored", variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: "Failed to restore", description: err.message, variant: "error" });
    },
  });

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // Handle Tab key for indentation
      if (e.key === "Tab") {
        e.preventDefault();
        const target = e.currentTarget;
        const start = target.selectionStart;
        const end = target.selectionEnd;
        const value = target.value;
        const newValue = value.substring(0, start) + "  " + value.substring(end);
        setEditedContent(newValue);
        requestAnimationFrame(() => {
          target.selectionStart = target.selectionEnd = start + 2;
        });
      }
      // Cmd/Ctrl+S to save
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        if (hasChanges) saveMutation.mutate();
      }
    },
    [hasChanges, saveMutation]
  );

  const previewHtml = useMemo(() => renderMarkdown(content), [content]);

  const handleToggleCompare = useCallback((versionId: string) => {
    setCompareSelection((prev) => {
      const next = new Set(prev);
      if (next.has(versionId)) {
        next.delete(versionId);
      } else if (next.size < 2) {
        next.add(versionId);
      } else {
        // Replace the oldest selection
        const arr = [...next];
        next.delete(arr[0]);
        next.add(versionId);
      }
      return next;
    });
  }, []);

  const compareVersions = useMemo(() => {
    if (compareSelection.size !== 2) return null;
    const [leftId, rightId] = [...compareSelection];
    const left = versions.find((v) => v.id === leftId);
    const right = versions.find((v) => v.id === rightId);
    if (!left || !right) return null;
    // Put the older version on the left
    return new Date(left.created_at) <= new Date(right.created_at)
      ? { left, right }
      : { left: right, right: left };
  }, [compareSelection, versions]);

  if (isLoading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !prompt) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center text-sm text-destructive">
          {error ? `Failed to load prompt: ${(error as Error).message}` : "Prompt not found"}
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl p-6">
      {/* Header */}
      <div className="mb-6">
        <Link
          to="/prompts"
          className="mb-3 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-3" />
          Back to Prompts
        </Link>

        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <div className="flex size-8 items-center justify-center rounded-lg bg-muted">
                <FileText className="size-4 text-muted-foreground" />
              </div>
              <h1 className="text-lg font-semibold tracking-tight">{prompt.name}</h1>
              <Badge variant="outline" className="font-mono text-[10px]">
                v{prompt.version}
              </Badge>
            </div>
            {/* Editable description */}
            <input
              type="text"
              value={description}
              onChange={(e) => setEditedDescription(e.target.value)}
              placeholder="Add a description..."
              className="mt-1.5 w-full max-w-lg bg-transparent text-xs text-muted-foreground outline-none placeholder:text-muted-foreground/50 focus:text-foreground"
            />
            {/* Metadata row */}
            <div className="mt-2 flex items-center gap-3">
              <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <Users className="size-2.5" />
                {prompt.team}
              </span>
              <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <Clock className="size-2.5" />
                {new Date(prompt.created_at).toLocaleDateString("en-US", {
                  month: "short",
                  day: "numeric",
                  year: "numeric",
                })}
              </span>
              <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <Layers className="size-2.5" />
                {versions.length} version{versions.length !== 1 ? "s" : ""}
              </span>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            {hasChanges && (
              <span className="mr-1 text-[10px] text-amber-600 dark:text-amber-400">
                Unsaved changes
              </span>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => duplicateMutation.mutate()}
              disabled={duplicateMutation.isPending}
            >
              <Copy className="size-3" data-icon="inline-start" />
              Duplicate
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => {
                if (window.confirm("Delete this prompt? This cannot be undone.")) {
                  deleteMutation.mutate();
                }
              }}
              disabled={deleteMutation.isPending}
            >
              <Trash2 className="size-3" data-icon="inline-start" />
              Delete
            </Button>
            <Button
              size="sm"
              onClick={() => saveMutation.mutate()}
              disabled={!hasChanges || saveMutation.isPending}
            >
              {saveSuccess ? (
                <Check className="size-3" data-icon="inline-start" />
              ) : saveMutation.isPending ? (
                <Loader2 className="size-3 animate-spin" data-icon="inline-start" />
              ) : (
                <Save className="size-3" data-icon="inline-start" />
              )}
              {saveSuccess ? "Saved" : "Save"}
            </Button>
          </div>
        </div>
      </div>

      {/* Main tabs */}
      <Tabs defaultValue="editor">
        <TabsList className="h-9 bg-transparent p-0">
          <TabsTrigger value="editor" className={TAB_TRIGGER_CLASS}>
            <Pencil className="size-3" />
            Editor
          </TabsTrigger>
          <TabsTrigger value="versions" className={TAB_TRIGGER_CLASS}>
            <History className="size-3" />
            Versions
            {versions.length > 0 && (
              <Badge variant="outline" className="ml-1 font-mono text-[9px] px-1 py-0">
                {versions.length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="compare" className={TAB_TRIGGER_CLASS}>
            <GitCompare className="size-3" />
            Compare
          </TabsTrigger>
          <TabsTrigger value="test" className={TAB_TRIGGER_CLASS}>
            <FlaskConical className="size-3" />
            Test
          </TabsTrigger>
        </TabsList>

        {/* Editor Tab */}
        <TabsContent value="editor">
          <div className="mt-4">
            {/* Edit/Preview sub-tabs */}
            <div className="mb-2 flex items-center gap-1 border-b border-border">
              <button
                onClick={() => setActiveEditorTab("edit")}
                className={cn(
                  "flex items-center gap-1.5 border-b-2 px-3 py-1.5 text-xs font-medium transition-colors",
                  activeEditorTab === "edit"
                    ? "border-foreground text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                )}
              >
                <Pencil className="size-3" />
                Edit
              </button>
              <button
                onClick={() => setActiveEditorTab("preview")}
                className={cn(
                  "flex items-center gap-1.5 border-b-2 px-3 py-1.5 text-xs font-medium transition-colors",
                  activeEditorTab === "preview"
                    ? "border-foreground text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                )}
              >
                <Eye className="size-3" />
                Preview
              </button>
            </div>

            {/* Split panel */}
            <div className="flex gap-0 rounded-lg border border-border overflow-hidden" style={{ height: "calc(100vh - 340px)" }}>
              {/* Editor panel */}
              <div
                className={cn(
                  "relative flex flex-1 flex-col border-r border-border",
                  activeEditorTab === "preview" ? "hidden md:flex" : ""
                )}
              >
                {/* Toolbar */}
                <EditorToolbar
                  textareaRef={textareaRef}
                  content={content}
                  onChange={setEditedContent}
                />
                {/* Line-numbered textarea area */}
                <div className="relative flex-1">
                  <textarea
                    ref={textareaRef}
                    value={content}
                    onChange={(e) => setEditedContent(e.target.value)}
                    onKeyDown={handleKeyDown}
                    className="size-full resize-none bg-background p-4 pl-12 font-mono text-xs leading-relaxed text-foreground outline-none placeholder:text-muted-foreground/50"
                    placeholder="Write your prompt content here..."
                    spellCheck={false}
                  />
                  {/* CSS-based line numbers */}
                  <div
                    className="pointer-events-none absolute inset-y-0 left-0 w-10 overflow-hidden border-r border-border/50 bg-muted/20 pt-4"
                    aria-hidden="true"
                  >
                    {content.split("\n").map((_, i) => (
                      <div
                        key={i}
                        className="pr-2 text-right font-mono text-[10px] leading-relaxed text-muted-foreground/40"
                      >
                        {i + 1}
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* Divider */}
              <div className={cn("hidden md:block w-0")} />

              {/* Preview panel */}
              <div
                className={cn(
                  "flex-1 overflow-auto bg-muted/20 p-4",
                  activeEditorTab === "edit" ? "hidden md:block" : ""
                )}
              >
                {content ? (
                  <div
                    className="prose-sm max-w-none text-foreground"
                    dangerouslySetInnerHTML={{ __html: previewHtml }}
                  />
                ) : (
                  <p className="text-xs text-muted-foreground italic">
                    Nothing to preview yet. Start writing in the editor.
                  </p>
                )}
              </div>
            </div>
          </div>
        </TabsContent>

        {/* Versions Tab */}
        <TabsContent value="versions">
          <div className="mt-4">
            {versionHistory.length > 0 ? (
              <VersionHistoryPanel
                promptId={id!}
                versions={versionHistory}
                currentId={id!}
                onRestore={(versionContent) => restoreMutation.mutate(versionContent)}
              />
            ) : viewingVersion ? (
              <div className="space-y-4">
                <VersionContentViewer
                  version={viewingVersion}
                  onClose={() => setViewingVersion(null)}
                />
              </div>
            ) : versions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <History className="mb-3 size-8 text-muted-foreground/30" />
                <p className="text-sm text-muted-foreground">No version history available.</p>
              </div>
            ) : (
              <VersionTimeline
                versions={versions}
                currentId={id!}
                onSelect={(v) => setViewingVersion(v)}
                onRestore={(v) => {
                  if (window.confirm(`Restore version v${v.version}? This will update the current content.`)) {
                    restoreMutation.mutate(v.content);
                  }
                }}
                selectedForCompare={compareSelection}
                onToggleCompare={handleToggleCompare}
              />
            )}
          </div>
        </TabsContent>

        {/* Test Tab */}
        <TabsContent value="test">
          <PromptTestPanel content={content} />
        </TabsContent>

        {/* Compare Tab */}
        <TabsContent value="compare">
          <div className="mt-4">
            {versions.length < 2 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <GitCompare className="mb-3 size-8 text-muted-foreground/30" />
                <p className="text-sm text-muted-foreground">
                  Need at least two versions to compare.
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {/* Version selector */}
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-2">
                    <label className="text-xs font-medium">Left:</label>
                    <select
                      value={[...compareSelection][0] ?? ""}
                      onChange={(e) => {
                        const val = e.target.value;
                        setCompareSelection((prev) => {
                          const arr = [...prev];
                          const next = new Set<string>();
                          if (val) next.add(val);
                          if (arr[1]) next.add(arr[1]);
                          return next;
                        });
                      }}
                      className="h-7 rounded-md border border-input bg-background px-2 text-xs outline-none"
                    >
                      <option value="">Select version...</option>
                      {versions.map((v) => (
                        <option key={v.id} value={v.id}>
                          v{v.version} - {new Date(v.created_at).toLocaleDateString()}
                        </option>
                      ))}
                    </select>
                  </div>
                  <GitCompare className="size-3.5 text-muted-foreground" />
                  <div className="flex items-center gap-2">
                    <label className="text-xs font-medium">Right:</label>
                    <select
                      value={[...compareSelection][1] ?? ""}
                      onChange={(e) => {
                        const val = e.target.value;
                        setCompareSelection((prev) => {
                          const arr = [...prev];
                          const next = new Set<string>();
                          if (arr[0]) next.add(arr[0]);
                          if (val) next.add(val);
                          return next;
                        });
                      }}
                      className="h-7 rounded-md border border-input bg-background px-2 text-xs outline-none"
                    >
                      <option value="">Select version...</option>
                      {versions.map((v) => (
                        <option key={v.id} value={v.id}>
                          v{v.version} - {new Date(v.created_at).toLocaleDateString()}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* Diff display */}
                {compareVersions ? (
                  <DiffViewer
                    leftVersion={compareVersions.left}
                    rightVersion={compareVersions.right}
                  />
                ) : (
                  <div className="rounded-lg border border-dashed border-border p-8 text-center">
                    <p className="text-xs text-muted-foreground">
                      Select two versions above to see their differences.
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
