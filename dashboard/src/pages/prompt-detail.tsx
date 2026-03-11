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
} from "lucide-react";
import { api, type Prompt } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useState, useCallback, useMemo } from "react";

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

function VersionSidebar({
  versions,
  currentId,
  onSelect,
}: {
  versions: Prompt[];
  currentId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="space-y-1">
      <h4 className="mb-2 text-xs font-medium text-muted-foreground uppercase tracking-wider">
        Versions
      </h4>
      {versions.map((v, i) => (
        <button
          key={v.id}
          onClick={() => onSelect(v.id)}
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors",
            v.id === currentId
              ? "bg-muted font-medium text-foreground"
              : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
          )}
        >
          <Badge variant="outline" className="font-mono text-[10px]">
            v{v.version}
          </Badge>
          {i === 0 && (
            <span className="text-[10px] text-emerald-600 dark:text-emerald-400">latest</span>
          )}
          <span className="ml-auto text-[10px] text-muted-foreground">
            {new Date(v.created_at).toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
            })}
          </span>
        </button>
      ))}
    </div>
  );
}

export default function PromptDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [editedContent, setEditedContent] = useState<string | null>(null);
  const [editedDescription, setEditedDescription] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"edit" | "preview">("edit");
  const [saveSuccess, setSaveSuccess] = useState(false);

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

  const prompt = promptData?.data;
  const versions = versionsData?.data ?? [];

  const content = editedContent ?? prompt?.content ?? "";
  const description = editedDescription ?? prompt?.description ?? "";
  const hasChanges = editedContent !== null || editedDescription !== null;

  const saveMutation = useMutation({
    mutationFn: () =>
      api.prompts.update(id!, {
        ...(editedContent !== null ? { content: editedContent } : {}),
        ...(editedDescription !== null ? { description: editedDescription } : {}),
      }),
    onSuccess: () => {
      setEditedContent(null);
      setEditedDescription(null);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2000);
      queryClient.invalidateQueries({ queryKey: ["prompt", id] });
      queryClient.invalidateQueries({ queryKey: ["prompt-versions", id] });
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.prompts.delete(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      navigate("/prompts");
    },
  });

  const duplicateMutation = useMutation({
    mutationFn: () => api.prompts.duplicate(id!),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      navigate(`/prompts/${data.data.id}`);
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
        // Restore cursor position after React re-render
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

  const handleVersionSelect = (versionId: string) => {
    // Reset edits and navigate to the selected version
    setEditedContent(null);
    setEditedDescription(null);
    navigate(`/prompts/${versionId}`);
  };

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
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
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

      {/* Main content: editor + preview + version sidebar */}
      <div className="flex gap-4">
        {/* Editor + Preview */}
        <div className="min-w-0 flex-1">
          {/* Tab bar */}
          <div className="mb-2 flex items-center gap-1 border-b border-border">
            <button
              onClick={() => setActiveTab("edit")}
              className={cn(
                "flex items-center gap-1.5 border-b-2 px-3 py-1.5 text-xs font-medium transition-colors",
                activeTab === "edit"
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              <Pencil className="size-3" />
              Edit
            </button>
            <button
              onClick={() => setActiveTab("preview")}
              className={cn(
                "flex items-center gap-1.5 border-b-2 px-3 py-1.5 text-xs font-medium transition-colors",
                activeTab === "preview"
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              <Eye className="size-3" />
              Preview
            </button>
            {hasChanges && (
              <span className="ml-2 text-[10px] text-amber-600 dark:text-amber-400">
                Unsaved changes
              </span>
            )}
          </div>

          {/* Split panel */}
          <div className="flex gap-0 rounded-lg border border-border overflow-hidden" style={{ height: "calc(100vh - 280px)" }}>
            {/* Editor panel */}
            <div
              className={cn(
                "relative flex-1 border-r border-border",
                activeTab === "preview" ? "hidden md:block" : ""
              )}
            >
              <textarea
                value={content}
                onChange={(e) => setEditedContent(e.target.value)}
                onKeyDown={handleKeyDown}
                className="size-full resize-none bg-background p-4 font-mono text-xs leading-relaxed text-foreground outline-none placeholder:text-muted-foreground/50"
                placeholder="Write your prompt content here..."
                spellCheck={false}
              />
            </div>

            {/* Divider -- visible when both panels shown */}
            <div className={cn(
              "hidden md:block w-0",
            )} />

            {/* Preview panel */}
            <div
              className={cn(
                "flex-1 overflow-auto bg-muted/20 p-4",
                activeTab === "edit" ? "hidden md:block" : ""
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

        {/* Version sidebar */}
        {versions.length > 0 && (
          <div className="w-52 shrink-0">
            <div className="sticky top-6 rounded-lg border border-border p-3">
              <VersionSidebar
                versions={versions}
                currentId={id!}
                onSelect={handleVersionSelect}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
