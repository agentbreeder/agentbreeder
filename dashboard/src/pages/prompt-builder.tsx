import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Save,
  Loader2,
  Eye,
  Code,
  Bold,
  Italic,
  Hash,
  History,
  RotateCcw,
  GitCompare,
  FileCode,
  Braces,
} from "lucide-react";
import { api, type Prompt, type PromptVersion } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  useState,
  useCallback,
  useMemo,
  useRef,
  useEffect,
} from "react";
import { useToast } from "@/hooks/use-toast";
import {
  extractVariables,
  promptToYaml,
  yamlToPrompt,
  type PromptVariable,
  type PromptYamlData,
} from "@/lib/prompt-yaml";

// ---------------------------------------------------------------------------
// Markdown renderer (reused from prompt-detail)
// ---------------------------------------------------------------------------

function renderMarkdown(md: string): string {
  let html = md
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _l, code) => {
    return `<pre class="rounded-md bg-muted/70 px-3 py-2 text-xs leading-relaxed overflow-x-auto"><code>${(code as string).trim()}</code></pre>`;
  });

  html = html.replace(
    /`([^`]+)`/g,
    '<code class="rounded bg-muted px-1 py-0.5 text-xs font-mono">$1</code>'
  );

  html = html.replace(
    /^### (.+)$/gm,
    '<h3 class="text-sm font-semibold mt-4 mb-1">$1</h3>'
  );
  html = html.replace(
    /^## (.+)$/gm,
    '<h2 class="text-base font-semibold mt-4 mb-1">$1</h2>'
  );
  html = html.replace(
    /^# (.+)$/gm,
    '<h1 class="text-lg font-bold mt-4 mb-2">$1</h1>'
  );

  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

  html = html.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    '<a href="$2" class="text-blue-600 dark:text-blue-400 underline">$1</a>'
  );

  html = html.replace(
    /^- (.+)$/gm,
    '<li class="ml-4 list-disc text-sm">$1</li>'
  );
  html = html.replace(
    /^\d+\. (.+)$/gm,
    '<li class="ml-4 list-decimal text-sm">$1</li>'
  );

  html = html.replace(/^---$/gm, '<hr class="my-3 border-border" />');

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

/** Replace {{var}} in preview with defaults or highlighted placeholders. */
function renderPreviewWithVariables(
  html: string,
  variables: Map<string, PromptVariable>
): string {
  return html.replace(/\{\{(\w+)\}\}/g, (_match, name: string) => {
    const v = variables.get(name);
    if (v?.default) {
      return `<span class="rounded bg-emerald-500/15 px-1 text-emerald-700 dark:text-emerald-400">${v.default}</span>`;
    }
    return `<span class="rounded bg-amber-500/20 px-1 text-amber-700 dark:text-amber-400">{{${name}}}</span>`;
  });
}

// ---------------------------------------------------------------------------
// Simple diff viewer (inline)
// ---------------------------------------------------------------------------

interface DiffLine {
  type: "same" | "added" | "removed";
  text: string;
  lineNum: number;
}

function computeSimpleDiff(oldText: string, newText: string): DiffLine[] {
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");
  const result: DiffLine[] = [];

  const maxLen = Math.max(oldLines.length, newLines.length);
  for (let i = 0; i < maxLen; i++) {
    const oldLine = i < oldLines.length ? oldLines[i] : undefined;
    const newLine = i < newLines.length ? newLines[i] : undefined;

    if (oldLine === newLine) {
      result.push({ type: "same", text: oldLine ?? "", lineNum: i + 1 });
    } else {
      if (oldLine !== undefined) {
        result.push({ type: "removed", text: oldLine, lineNum: i + 1 });
      }
      if (newLine !== undefined) {
        result.push({ type: "added", text: newLine, lineNum: i + 1 });
      }
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// Version History Panel
// ---------------------------------------------------------------------------

function VersionHistoryPanel({
  promptId,
  currentContent,
  onRestore,
}: {
  promptId: string;
  currentContent: string;
  onRestore: (content: string) => void;
}) {
  const [compareA, setCompareA] = useState<string | null>(null);
  const [compareB, setCompareB] = useState<string | null>(null);
  const [showDiff, setShowDiff] = useState(false);

  const { data: versionsData } = useQuery({
    queryKey: ["prompt-versions", promptId],
    queryFn: () => api.prompts.versionHistory(promptId),
    enabled: !!promptId,
  });

  const versions = versionsData?.data ?? [];

  const diffData = useMemo(() => {
    if (!showDiff || !compareA || !compareB) return null;
    const vA = versions.find((v) => v.id === compareA);
    const vB = versions.find((v) => v.id === compareB);
    if (!vA || !vB) return null;
    return {
      lines: computeSimpleDiff(vA.content, vB.content),
      labelA: `v${vA.version_number}`,
      labelB: `v${vB.version_number}`,
    };
  }, [showDiff, compareA, compareB, versions]);

  const handleVersionClick = (versionId: string) => {
    if (!compareA) {
      setCompareA(versionId);
    } else if (!compareB && versionId !== compareA) {
      setCompareB(versionId);
    } else {
      setCompareA(versionId);
      setCompareB(null);
      setShowDiff(false);
    }
  };

  if (versions.length === 0) {
    return (
      <div className="px-3 py-6 text-center text-xs text-muted-foreground">
        No version history yet. Save your first version to start tracking
        changes.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {compareA && compareB && (
        <div className="flex items-center gap-2 px-3">
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => {
              setShowDiff(!showDiff);
            }}
          >
            <GitCompare className="mr-1 size-3" />
            {showDiff ? "Hide Diff" : "Compare"}
          </Button>
          <button
            className="text-[10px] text-muted-foreground hover:text-foreground"
            onClick={() => {
              setCompareA(null);
              setCompareB(null);
              setShowDiff(false);
            }}
          >
            Clear selection
          </button>
        </div>
      )}

      {showDiff && diffData && (
        <div className="mx-3 max-h-60 overflow-auto rounded-md border border-border bg-muted/30">
          <div className="px-3 py-1.5 text-[10px] font-medium text-muted-foreground border-b border-border">
            {diffData.labelA} vs {diffData.labelB}
          </div>
          <pre className="px-3 py-2 text-xs font-mono leading-relaxed">
            {diffData.lines.map((line, i) => (
              <div
                key={i}
                className={cn(
                  line.type === "added" &&
                    "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
                  line.type === "removed" &&
                    "bg-red-500/10 text-red-700 dark:text-red-400"
                )}
              >
                <span className="inline-block w-6 text-right text-muted-foreground mr-2 select-none">
                  {line.type === "added"
                    ? "+"
                    : line.type === "removed"
                      ? "-"
                      : " "}
                </span>
                {line.text}
              </div>
            ))}
          </pre>
        </div>
      )}

      <div className="flex flex-col">
        {versions.map((ver, idx) => {
          const isSelected = ver.id === compareA || ver.id === compareB;
          return (
            <div
              key={ver.id}
              className={cn(
                "flex items-start gap-2 px-3 py-2 text-xs transition-colors cursor-pointer hover:bg-muted/30",
                isSelected && "bg-muted/50",
                idx > 0 && "border-t border-border/50"
              )}
              onClick={() => handleVersionClick(ver.id)}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <Badge
                    variant="outline"
                    className={cn(
                      "font-mono text-[10px]",
                      idx === 0 &&
                        "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                    )}
                  >
                    v{ver.version_number}
                  </Badge>
                  {idx === 0 && (
                    <span className="text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                      latest
                    </span>
                  )}
                  {isSelected && (
                    <span className="text-[10px] font-medium text-blue-600 dark:text-blue-400">
                      selected
                    </span>
                  )}
                </div>
                {ver.change_summary && (
                  <p className="mt-0.5 text-muted-foreground line-clamp-1">
                    {ver.change_summary}
                  </p>
                )}
                <p className="mt-0.5 text-[10px] text-muted-foreground">
                  {ver.created_by} &middot;{" "}
                  {new Date(ver.created_at).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0 shrink-0"
                title="Restore this version"
                onClick={(e) => {
                  e.stopPropagation();
                  onRestore(ver.content);
                }}
              >
                <RotateCcw className="size-3" />
              </Button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Variables Panel
// ---------------------------------------------------------------------------

function VariablesPanel({
  content,
  variables,
  onUpdateVariable,
}: {
  content: string;
  variables: Map<string, PromptVariable>;
  onUpdateVariable: (name: string, field: "description" | "default", value: string) => void;
}) {
  const detectedNames = useMemo(() => extractVariables(content), [content]);

  if (detectedNames.length === 0) {
    return (
      <div className="px-3 py-6 text-center text-xs text-muted-foreground">
        No template variables detected. Use <code className="rounded bg-muted px-1 font-mono text-[10px]">{"{{variable_name}}"}</code> syntax in your prompt.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 px-3">
      {detectedNames.map((name) => {
        const v = variables.get(name) ?? {
          name,
          description: "",
          default: "",
        };
        return (
          <div key={name} className="rounded-md border border-border p-2.5">
            <div className="flex items-center gap-1.5 mb-2">
              <Braces className="size-3 text-muted-foreground" />
              <span className="font-mono text-xs font-medium">{name}</span>
            </div>
            <div className="space-y-1.5">
              <div>
                <label className="text-[10px] font-medium text-muted-foreground">
                  Description
                </label>
                <Input
                  value={v.description}
                  onChange={(e) =>
                    onUpdateVariable(name, "description", e.target.value)
                  }
                  placeholder="What this variable represents..."
                  className="h-7 text-xs"
                />
              </div>
              <div>
                <label className="text-[10px] font-medium text-muted-foreground">
                  Default value
                </label>
                <Input
                  value={v.default}
                  onChange={(e) =>
                    onUpdateVariable(name, "default", e.target.value)
                  }
                  placeholder="Default value..."
                  className="h-7 text-xs"
                />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Prompt Builder Page
// ---------------------------------------------------------------------------

export default function PromptBuilderPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const editorRef = useRef<HTMLTextAreaElement>(null);

  // State
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [description, setDescription] = useState("");
  const [team, setTeam] = useState("");
  const [content, setContent] = useState("");
  const [variables, setVariables] = useState<Map<string, PromptVariable>>(
    new Map()
  );
  const [isDirty, setIsDirty] = useState(false);
  const [showPreview, setShowPreview] = useState(true);
  const [showYaml, setShowYaml] = useState(false);
  const [rightPanel, setRightPanel] = useState<
    "preview" | "variables" | "history"
  >("preview");
  const [changeSummary, setChangeSummary] = useState("");

  // Load existing prompt
  const { data: promptData, isLoading } = useQuery({
    queryKey: ["prompt", id],
    queryFn: () => api.prompts.get(id!),
    enabled: !!id,
  });

  // Initialize form from loaded prompt
  useEffect(() => {
    if (promptData?.data) {
      const p = promptData.data;
      setName(p.name);
      setVersion(p.version);
      setDescription(p.description);
      setTeam(p.team);
      setContent(p.content);
      setIsDirty(false);
    }
  }, [promptData]);

  // Save (update existing)
  const updateMutation = useMutation({
    mutationFn: () =>
      api.prompts.update(id!, { content, description }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompt", id] });
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      setIsDirty(false);
      toast({ title: "Prompt saved", variant: "success" });
    },
    onError: (err: Error) => {
      toast({
        title: "Failed to save",
        description: err.message,
        variant: "error",
      });
    },
  });

  // Create new prompt
  const createMutation = useMutation({
    mutationFn: () =>
      api.prompts.create({ name, version, content, description, team }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      setIsDirty(false);
      toast({ title: "Prompt created", variant: "success" });
      navigate(`/prompts/builder/${data.data.id}`, { replace: true });
    },
    onError: (err: Error) => {
      toast({
        title: "Failed to create prompt",
        description: err.message,
        variant: "error",
      });
    },
  });

  // Save as new version (update content via API)
  const updateContentMutation = useMutation({
    mutationFn: () =>
      api.prompts.updateContent(id!, {
        content,
        change_summary: changeSummary || undefined,
        author: "builder",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompt", id] });
      queryClient.invalidateQueries({ queryKey: ["prompt-versions", id] });
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      setIsDirty(false);
      setChangeSummary("");
      toast({
        title: "Saved as new version",
        variant: "success",
      });
    },
    onError: (err: Error) => {
      toast({
        title: "Failed to save version",
        description: err.message,
        variant: "error",
      });
    },
  });

  // Handle save
  const handleSave = useCallback(() => {
    if (id) {
      updateMutation.mutate();
    } else {
      if (!name.trim() || !team.trim() || !content.trim()) {
        toast({
          title: "Missing required fields",
          description: "Name, team, and content are required.",
          variant: "error",
        });
        return;
      }
      createMutation.mutate();
    }
  }, [id, name, team, content, updateMutation, createMutation, toast]);

  const handleSaveNewVersion = useCallback(() => {
    if (!id) return;
    updateContentMutation.mutate();
  }, [id, updateContentMutation]);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        handleSave();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "b") {
        e.preventDefault();
        wrapSelection("**", "**", "bold text");
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "i") {
        e.preventDefault();
        wrapSelection("*", "*", "italic text");
        return;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [handleSave]);

  // Wrap selection with syntax
  const wrapSelection = useCallback(
    (before: string, after: string, placeholder: string) => {
      const textarea = editorRef.current;
      if (!textarea) return;
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const selected = content.substring(start, end);
      const text = selected || placeholder;
      const newContent =
        content.substring(0, start) + before + text + after + content.substring(end);
      setContent(newContent);
      setIsDirty(true);
      requestAnimationFrame(() => {
        textarea.focus();
        const cursorStart = start + before.length;
        const cursorEnd = cursorStart + text.length;
        textarea.selectionStart = cursorStart;
        textarea.selectionEnd = cursorEnd;
      });
    },
    [content]
  );

  // Update variable metadata
  const handleUpdateVariable = useCallback(
    (name: string, field: "description" | "default", value: string) => {
      setVariables((prev) => {
        const next = new Map(prev);
        const existing = next.get(name) ?? {
          name,
          description: "",
          default: "",
        };
        next.set(name, { ...existing, [field]: value });
        return next;
      });
    },
    []
  );

  // Handle content change
  const handleContentChange = useCallback(
    (newContent: string) => {
      setContent(newContent);
      setIsDirty(true);
    },
    []
  );

  // Restore version
  const handleRestoreVersion = useCallback((restoredContent: string) => {
    setContent(restoredContent);
    setIsDirty(true);
    toast({ title: "Version restored", description: "Content has been restored. Save to keep changes.", variant: "info" });
  }, [toast]);

  // Stats
  const charCount = content.length;
  const wordCount = content.trim() ? content.trim().split(/\s+/).length : 0;
  const estimatedTokens = Math.ceil(charCount / 4);
  const lineCount = content.split("\n").length;

  // YAML representation
  const yamlContent = useMemo(() => {
    const detected = extractVariables(content);
    const yamlVars: PromptVariable[] = detected.map((n) => {
      const v = variables.get(n);
      return {
        name: n,
        description: v?.description ?? "",
        default: v?.default ?? "",
      };
    });
    const data: PromptYamlData = {
      name: name || "untitled",
      version,
      description,
      tags: [],
      variables: yamlVars,
      content,
    };
    return promptToYaml(data);
  }, [name, version, description, content, variables]);

  // Preview HTML
  const previewHtml = useMemo(() => {
    const raw = renderMarkdown(content);
    return renderPreviewWithVariables(raw, variables);
  }, [content, variables]);

  // Line numbers for editor
  const lineNumbers = useMemo(() => {
    return Array.from({ length: lineCount }, (_, i) => i + 1);
  }, [lineCount]);

  const isSaving =
    updateMutation.isPending ||
    createMutation.isPending ||
    updateContentMutation.isPending;

  if (isLoading && id) {
    return (
      <div className="flex h-[calc(100vh-4rem)] items-center justify-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col">
      {/* Top Bar */}
      <div className="flex items-center gap-3 border-b border-border px-4 py-2">
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-7 p-0"
          onClick={() => navigate(id ? `/prompts/${id}` : "/prompts")}
        >
          <ArrowLeft className="size-4" />
        </Button>

        {id ? (
          <div className="flex items-center gap-2">
            <h1 className="text-sm font-semibold">{name}</h1>
            <Badge variant="outline" className="font-mono text-[10px]">
              v{version}
            </Badge>
            {isDirty && (
              <Badge
                variant="outline"
                className="text-[10px] border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400"
              >
                unsaved
              </Badge>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <Input
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setIsDirty(true);
              }}
              placeholder="Prompt name..."
              className="h-7 w-48 text-xs"
            />
            <Input
              value={version}
              onChange={(e) => {
                setVersion(e.target.value);
                setIsDirty(true);
              }}
              placeholder="1.0.0"
              className="h-7 w-20 text-xs font-mono"
            />
            <Input
              value={team}
              onChange={(e) => {
                setTeam(e.target.value);
                setIsDirty(true);
              }}
              placeholder="Team..."
              className="h-7 w-32 text-xs"
            />
          </div>
        )}

        <div className="ml-auto flex items-center gap-2">
          {/* YAML toggle */}
          <Button
            variant={showYaml ? "default" : "outline"}
            size="sm"
            className="h-7 text-xs"
            onClick={() => setShowYaml(!showYaml)}
          >
            <FileCode className="mr-1 size-3" />
            YAML
          </Button>

          {/* Save as new version (only for existing prompts) */}
          {id && (
            <div className="flex items-center gap-1">
              <Input
                value={changeSummary}
                onChange={(e) => setChangeSummary(e.target.value)}
                placeholder="Change summary..."
                className="h-7 w-40 text-xs"
              />
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs"
                onClick={handleSaveNewVersion}
                disabled={!isDirty || isSaving}
              >
                {updateContentMutation.isPending ? (
                  <Loader2 className="mr-1 size-3 animate-spin" />
                ) : (
                  <History className="mr-1 size-3" />
                )}
                Save Version
              </Button>
            </div>
          )}

          {/* Save button */}
          <Button
            size="sm"
            className="h-7 text-xs"
            onClick={handleSave}
            disabled={isSaving}
          >
            {updateMutation.isPending || createMutation.isPending ? (
              <Loader2 className="mr-1 size-3 animate-spin" />
            ) : (
              <Save className="mr-1 size-3" />
            )}
            Save
          </Button>

          {/* Submit for Review (disabled, future) */}
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            disabled
            title="Coming soon"
          >
            Submit for Review
          </Button>
        </div>
      </div>

      {/* Description field */}
      {!id && (
        <div className="border-b border-border px-4 py-1.5">
          <Input
            value={description}
            onChange={(e) => {
              setDescription(e.target.value);
              setIsDirty(true);
            }}
            placeholder="Description (optional)..."
            className="h-7 border-none bg-transparent text-xs shadow-none focus-visible:ring-0 px-0"
          />
        </div>
      )}

      {/* Main Content Area */}
      {showYaml ? (
        <div className="flex-1 overflow-auto bg-muted/20 p-4">
          <pre className="rounded-lg border border-border bg-background p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap">
            {yamlContent}
          </pre>
        </div>
      ) : (
        <div className="flex flex-1 overflow-hidden">
          {/* Left Panel: Editor */}
          <div className="flex flex-1 flex-col border-r border-border" style={{ flex: "0 0 60%" }}>
            {/* Editor toolbar */}
            <div className="flex items-center gap-0.5 border-b border-border bg-muted/30 px-2 py-1">
              <button
                type="button"
                title="Bold (Cmd+B)"
                onClick={() => wrapSelection("**", "**", "bold text")}
                className="flex size-7 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Bold className="size-3.5" />
              </button>
              <button
                type="button"
                title="Italic (Cmd+I)"
                onClick={() => wrapSelection("*", "*", "italic text")}
                className="flex size-7 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Italic className="size-3.5" />
              </button>
              <button
                type="button"
                title="Inline code"
                onClick={() => wrapSelection("`", "`", "code")}
                className="flex size-7 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Code className="size-3.5" />
              </button>
              <button
                type="button"
                title="Heading"
                onClick={() => wrapSelection("## ", "", "Heading")}
                className="flex size-7 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Hash className="size-3.5" />
              </button>
              <button
                type="button"
                title="Insert variable"
                onClick={() => wrapSelection("{{", "}}", "variable_name")}
                className="flex size-7 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Braces className="size-3.5" />
              </button>

              <div className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground">
                <span>Cmd+S save</span>
                <span className="text-border">|</span>
                <span>Cmd+B bold</span>
                <span className="text-border">|</span>
                <span>Cmd+I italic</span>
              </div>
            </div>

            {/* Editor with line numbers */}
            <div className="relative flex flex-1 overflow-hidden">
              {/* Line numbers */}
              <div className="flex-shrink-0 select-none overflow-hidden border-r border-border bg-muted/20 px-2 py-3 text-right font-mono text-[11px] leading-[1.625rem] text-muted-foreground">
                {lineNumbers.map((n) => (
                  <div key={n}>{n}</div>
                ))}
              </div>

              {/* Textarea */}
              <textarea
                ref={editorRef}
                value={content}
                onChange={(e) => handleContentChange(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Tab") {
                    e.preventDefault();
                    const target = e.currentTarget;
                    const start = target.selectionStart;
                    const end = target.selectionEnd;
                    const newContent =
                      content.substring(0, start) +
                      "  " +
                      content.substring(end);
                    setContent(newContent);
                    setIsDirty(true);
                    requestAnimationFrame(() => {
                      target.selectionStart = target.selectionEnd = start + 2;
                    });
                  }
                }}
                className="flex-1 resize-none bg-background p-3 font-mono text-xs leading-[1.625rem] outline-none placeholder:text-muted-foreground"
                placeholder="Write your prompt here...&#10;&#10;Use {{variable_name}} for template variables."
                spellCheck={false}
              />
            </div>
          </div>

          {/* Right Panel */}
          <div className="flex flex-col overflow-hidden" style={{ flex: "0 0 40%" }}>
            {/* Panel tabs */}
            <div className="flex items-center border-b border-border bg-muted/30">
              <button
                className={cn(
                  "flex items-center gap-1.5 px-3 py-2 text-xs font-medium transition-colors",
                  rightPanel === "preview"
                    ? "border-b-2 border-foreground text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
                onClick={() => setRightPanel("preview")}
              >
                <Eye className="size-3" />
                Preview
              </button>
              <button
                className={cn(
                  "flex items-center gap-1.5 px-3 py-2 text-xs font-medium transition-colors",
                  rightPanel === "variables"
                    ? "border-b-2 border-foreground text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
                onClick={() => setRightPanel("variables")}
              >
                <Braces className="size-3" />
                Variables
                {extractVariables(content).length > 0 && (
                  <Badge
                    variant="outline"
                    className="ml-1 h-4 px-1 text-[10px]"
                  >
                    {extractVariables(content).length}
                  </Badge>
                )}
              </button>
              {id && (
                <button
                  className={cn(
                    "flex items-center gap-1.5 px-3 py-2 text-xs font-medium transition-colors",
                    rightPanel === "history"
                      ? "border-b-2 border-foreground text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                  onClick={() => setRightPanel("history")}
                >
                  <History className="size-3" />
                  History
                </button>
              )}
            </div>

            {/* Panel content */}
            <div className="flex-1 overflow-auto">
              {rightPanel === "preview" && (
                <div className="p-4">
                  {content.trim() ? (
                    <div
                      className="prose prose-sm dark:prose-invert max-w-none"
                      dangerouslySetInnerHTML={{ __html: previewHtml }}
                    />
                  ) : (
                    <p className="text-xs text-muted-foreground text-center py-8">
                      Start typing to see a preview...
                    </p>
                  )}
                </div>
              )}

              {rightPanel === "variables" && (
                <div className="py-3">
                  <VariablesPanel
                    content={content}
                    variables={variables}
                    onUpdateVariable={handleUpdateVariable}
                  />
                </div>
              )}

              {rightPanel === "history" && id && (
                <div className="py-2">
                  <VersionHistoryPanel
                    promptId={id}
                    currentContent={content}
                    onRestore={handleRestoreVersion}
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Bottom Bar */}
      <div className="flex items-center justify-between border-t border-border bg-muted/30 px-4 py-1.5 text-[10px] text-muted-foreground">
        <div className="flex items-center gap-4">
          <span>{charCount.toLocaleString()} characters</span>
          <span>{wordCount.toLocaleString()} words</span>
          <span>{lineCount} lines</span>
          <span>~{estimatedTokens.toLocaleString()} tokens</span>
        </div>
        <div className="flex items-center gap-3">
          {extractVariables(content).length > 0 && (
            <span>
              {extractVariables(content).length} variable
              {extractVariables(content).length !== 1 ? "s" : ""}
            </span>
          )}
          <span>Markdown</span>
        </div>
      </div>
    </div>
  );
}
