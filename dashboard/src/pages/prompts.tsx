import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  FileText,
  Search,
  ChevronDown,
  ChevronRight,
  Users,
  Clock,
  Plus,
  MoreHorizontal,
  Trash2,
  Tag,
  X,
  Bold,
  Italic,
  Code,
  Loader2,
  Star,
  Pencil,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, type Prompt } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogTrigger,
  DialogClose,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { useState, useMemo, useRef, useCallback } from "react";
import { useToast } from "@/hooks/use-toast";
import { FavoriteButton } from "@/components/favorite-button";
import { ExportDropdown } from "@/components/export-dropdown";
import { useFavorites } from "@/hooks/use-favorites";
import { useSortable } from "@/hooks/use-sortable";
import { SortableColumnHeader } from "@/components/ui/sortable-header";
import { SkeletonCardGrid } from "@/components/ui/skeleton-table";
import { EmptyState } from "@/components/ui/empty-state";

interface PromptGroup {
  name: string;
  versions: Prompt[];
  latestVersion: string;
  team: string;
  description: string;
  created_at: string;
}

function PromptCard({
  group,
  onNavigate,
  onDelete,
}: {
  group: PromptGroup;
  onNavigate: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const latest = group.versions[0];

  return (
    <div className="overflow-hidden rounded-lg border border-border transition-all hover:border-border">
      {/* Header -- always visible */}
      <div className="flex items-start gap-3 p-4 transition-colors hover:bg-muted/20">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted"
        >
          <FileText className="size-4 text-muted-foreground" />
        </button>

        <div
          className="min-w-0 flex-1 cursor-pointer"
          onClick={() => onNavigate(latest.id)}
        >
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium">{group.name}</h3>
            <Badge variant="outline" className="text-[10px] font-mono">
              v{group.latestVersion}
            </Badge>
            {group.versions.length > 1 && (
              <Badge variant="outline" className="text-[10px]">
                {group.versions.length} versions
              </Badge>
            )}
          </div>
          {group.description && (
            <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
              {group.description}
            </p>
          )}
          <div className="mt-2 flex items-center gap-3">
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <Users className="size-2.5" />
              {group.team}
            </span>
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <Clock className="size-2.5" />
              {new Date(latest.created_at).toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
              })}
            </span>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <FavoriteButton id={latest.id} />
          <DropdownMenu>
            <DropdownMenuTrigger
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <MoreHorizontal className="size-3.5" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => onNavigate(latest.id)}>
                <FileText className="size-3.5" />
                Open
              </DropdownMenuItem>
              <DropdownMenuItem
                variant="destructive"
                onClick={() => {
                  if (window.confirm(`Delete "${group.name}" (v${latest.version})?`)) {
                    onDelete(latest.id);
                  }
                }}
              >
                <Trash2 className="size-3.5" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <button
            onClick={() => setExpanded(!expanded)}
            className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {expanded ? (
              <ChevronDown className="size-4" />
            ) : (
              <ChevronRight className="size-4" />
            )}
          </button>
        </div>
      </div>

      {/* Expanded -- shows prompt content */}
      {expanded && (
        <div className="border-t border-border">
          {group.versions.map((prompt, i) => (
            <div
              key={prompt.id}
              className={cn(
                "cursor-pointer px-4 py-3 transition-colors hover:bg-muted/30",
                i > 0 && "border-t border-border/50"
              )}
              onClick={() => onNavigate(prompt.id)}
            >
              <div className="mb-2 flex items-center gap-2">
                <Badge
                  variant="outline"
                  className={cn(
                    "font-mono text-[10px]",
                    i === 0
                      ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
                      : ""
                  )}
                >
                  v{prompt.version}
                </Badge>
                {i === 0 && (
                  <span className="text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                    latest
                  </span>
                )}
                <span className="ml-auto text-[10px] text-muted-foreground">
                  {new Date(prompt.created_at).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                  })}
                </span>
              </div>
              <pre className="max-h-40 overflow-auto rounded-md bg-muted/50 px-3 py-2 font-mono text-xs leading-relaxed text-foreground/80">
                {prompt.content}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CreatePromptDialog({ onCreated }: { onCreated: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [description, setDescription] = useState("");
  const [team, setTeam] = useState("");
  const [content, setContent] = useState("");
  const [tagInput, setTagInput] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [error, setError] = useState("");
  const contentRef = useRef<HTMLTextAreaElement>(null);
  const { toast } = useToast();

  const queryClient = useQueryClient();

  const createMutation = useMutation({
    mutationFn: () =>
      api.prompts.create({ name, version, content, description, team }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      setOpen(false);
      resetForm();
      toast({ title: "Prompt created", description: `"${name}" has been registered.`, variant: "success" });
      onCreated(data.data.id);
    },
    onError: (err: Error) => {
      setError(err.message);
      toast({ title: "Failed to create prompt", description: err.message, variant: "error" });
    },
  });

  const resetForm = () => {
    setName("");
    setVersion("1.0.0");
    setDescription("");
    setTeam("");
    setContent("");
    setTagInput("");
    setTags([]);
    setError("");
  };

  const addTag = () => {
    const tag = tagInput.trim().toLowerCase();
    if (tag && !tags.includes(tag)) {
      setTags([...tags, tag]);
    }
    setTagInput("");
  };

  const removeTag = (tag: string) => {
    setTags(tags.filter((t) => t !== tag));
  };

  const insertSyntax = useCallback(
    (before: string, after: string, placeholder: string) => {
      const textarea = contentRef.current;
      if (!textarea) return;
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const selected = content.substring(start, end);
      const text = selected || placeholder;
      const newContent =
        content.substring(0, start) + before + text + after + content.substring(end);
      setContent(newContent);
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

  const canSubmit = name.trim() && version.trim() && team.trim() && content.trim();

  return (
    <Dialog open={open} onOpenChange={(val) => { setOpen(val); if (!val) resetForm(); }}>
      <DialogTrigger
        render={<Button size="sm" />}
      >
        <Plus className="size-3" data-icon="inline-start" />
        New Prompt
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Create Prompt</DialogTitle>
          <DialogDescription>
            Register a new prompt template in the registry.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium">Name</label>
              <Input
                placeholder="e.g. support-system"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="h-8 text-xs"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">Version</label>
              <Input
                placeholder="1.0.0"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                className="h-8 text-xs"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium">Team</label>
              <Input
                placeholder="e.g. engineering"
                value={team}
                onChange={(e) => setTeam(e.target.value)}
                className="h-8 text-xs"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">Tags</label>
              <div className="flex items-center gap-1">
                <Input
                  placeholder="Add tag..."
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addTag();
                    }
                  }}
                  className="h-8 text-xs"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-8 px-2"
                  onClick={addTag}
                  disabled={!tagInput.trim()}
                >
                  <Plus className="size-3" />
                </Button>
              </div>
            </div>
          </div>
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {tags.map((tag) => (
                <Badge key={tag} variant="outline" className="gap-1 text-[10px]">
                  <Tag className="size-2.5" />
                  {tag}
                  <button
                    onClick={() => removeTag(tag)}
                    className="ml-0.5 rounded-full hover:bg-muted"
                  >
                    <X className="size-2.5" />
                  </button>
                </Badge>
              ))}
            </div>
          )}
          <div>
            <label className="mb-1 block text-xs font-medium">Description</label>
            <Input
              placeholder="Optional description..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="h-8 text-xs"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium">Content</label>
            {/* Mini toolbar */}
            <div className="flex items-center gap-0.5 rounded-t-md border border-b-0 border-input bg-muted/30 px-1.5 py-0.5">
              <button
                type="button"
                title="Bold"
                onClick={() => insertSyntax("**", "**", "bold text")}
                className="flex size-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Bold className="size-3" />
              </button>
              <button
                type="button"
                title="Italic"
                onClick={() => insertSyntax("*", "*", "italic text")}
                className="flex size-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Italic className="size-3" />
              </button>
              <button
                type="button"
                title="Code"
                onClick={() => insertSyntax("`", "`", "code")}
                className="flex size-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Code className="size-3" />
              </button>
            </div>
            <textarea
              ref={contentRef}
              placeholder="Write your prompt content in Markdown..."
              value={content}
              onChange={(e) => setContent(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Tab") {
                  e.preventDefault();
                  const target = e.currentTarget;
                  const start = target.selectionStart;
                  const end = target.selectionEnd;
                  const newContent = content.substring(0, start) + "  " + content.substring(end);
                  setContent(newContent);
                  requestAnimationFrame(() => {
                    target.selectionStart = target.selectionEnd = start + 2;
                  });
                }
              }}
              className="min-h-[160px] w-full rounded-b-md border border-input bg-background px-3 py-2 font-mono text-xs leading-relaxed outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-ring"
            />
          </div>
          {error && (
            <p className="text-xs text-destructive">{error}</p>
          )}
        </div>

        <DialogFooter>
          <DialogClose render={<Button variant="outline" size="sm" />}>
            Cancel
          </DialogClose>
          <Button
            size="sm"
            onClick={() => createMutation.mutate()}
            disabled={!canSubmit || createMutation.isPending}
          >
            {createMutation.isPending ? (
              <>
                <Loader2 className="size-3 animate-spin" data-icon="inline-start" />
                Creating...
              </>
            ) : (
              "Create"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function PromptsPage() {
  const [search, setSearch] = useState("");
  const [teamFilter, setTeamFilter] = useState("");
  const [showFavoritesOnly, setShowFavoritesOnly] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { favorites } = useFavorites();

  const { data, isLoading, error } = useQuery({
    queryKey: ["prompts", { teamFilter }],
    queryFn: () =>
      api.prompts.list({ team: teamFilter || undefined }),
    staleTime: 10_000,
  });

  const { toast } = useToast();

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.prompts.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      toast({ title: "Prompt deleted", variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: "Failed to delete prompt", description: err.message, variant: "error" });
    },
  });

  const prompts = data?.data ?? [];
  const total = data?.meta.total ?? 0;

  // Group prompts by name, with versions sorted descending
  const groups = useMemo(() => {
    const map = new Map<string, Prompt[]>();
    for (const p of prompts) {
      const existing = map.get(p.name) ?? [];
      existing.push(p);
      map.set(p.name, existing);
    }
    const result: PromptGroup[] = [];
    for (const [name, versions] of map) {
      // Sort versions descending (newest first)
      versions.sort((a, b) => b.version.localeCompare(a.version, undefined, { numeric: true }));
      result.push({
        name,
        versions,
        latestVersion: versions[0].version,
        team: versions[0].team,
        description: versions[0].description,
        created_at: versions[0].created_at,
      });
    }
    return result;
  }, [prompts]);

  let filtered = search
    ? groups.filter(
        (g) =>
          g.name.toLowerCase().includes(search.toLowerCase()) ||
          g.description.toLowerCase().includes(search.toLowerCase()) ||
          g.team.toLowerCase().includes(search.toLowerCase())
      )
    : groups;

  if (showFavoritesOnly) {
    filtered = filtered.filter((g) =>
      g.versions.some((v) => favorites.has(v.id))
    );
  }

  // Sortable
  const { sortedData, sortKey, sortDirection, toggleSort } = useSortable(
    filtered as unknown as Record<string, unknown>[],
    "name",
    "asc"
  );
  const sortedGroups = sortedData as unknown as PromptGroup[];

  const teams = [...new Set(prompts.map((p) => p.team))].filter(Boolean).sort();
  const hasFilter = !!(search || teamFilter || showFavoritesOnly);

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Prompts</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {total} prompt{total !== 1 ? "s" : ""} across {groups.length} template
            {groups.length !== 1 ? "s" : ""} in registry
          </p>
        </div>
        <div className="flex items-center gap-2">
          <ExportDropdown
            data={prompts as unknown as Record<string, unknown>[]}
            filename="prompts"
          />
          <Button
            size="sm"
            variant="outline"
            onClick={() => navigate("/prompts/builder")}
          >
            <Pencil className="size-3" />
            Prompt Builder
          </Button>
          <CreatePromptDialog onCreated={(id) => navigate(`/prompts/${id}`)} />
        </div>
      </div>

      {/* Filters */}
      <div className="mb-4 flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Filter prompts..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 pl-9 text-xs"
          />
        </div>
        <select
          value={teamFilter}
          onChange={(e) => setTeamFilter(e.target.value)}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none"
        >
          <option value="">All teams</option>
          {teams.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <button
          onClick={() => setShowFavoritesOnly(!showFavoritesOnly)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md border border-input px-2.5 py-1.5 text-xs font-medium transition-colors",
            showFavoritesOnly
              ? "border-amber-400/50 bg-amber-500/10 text-amber-600 dark:text-amber-400"
              : "text-muted-foreground hover:bg-muted"
          )}
        >
          <Star className={cn("size-3", showFavoritesOnly && "fill-amber-400")} />
          Favorites
        </button>
      </div>

      {/* Sort controls */}
      <div className="mb-3 flex items-center gap-4 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        <span>Sort by:</span>
        <SortableColumnHeader
          sortKey="name"
          currentSortKey={sortKey}
          currentDirection={sortDirection}
          onSort={toggleSort}
        >
          Name
        </SortableColumnHeader>
        <SortableColumnHeader
          sortKey="team"
          currentSortKey={sortKey}
          currentDirection={sortDirection}
          onSort={toggleSort}
        >
          Team
        </SortableColumnHeader>
        <SortableColumnHeader
          sortKey="created_at"
          currentSortKey={sortKey}
          currentDirection={sortDirection}
          onSort={toggleSort}
        >
          Created
        </SortableColumnHeader>
      </div>

      {/* Grid */}
      {isLoading ? (
        <SkeletonCardGrid cards={4} />
      ) : error ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center text-sm text-destructive">
          Failed to load prompts: {(error as Error).message}
        </div>
      ) : sortedGroups.length === 0 ? (
        <EmptyState
          icon={FileText}
          title={hasFilter ? "No prompts match your filters" : "No prompts registered"}
          description={
            hasFilter
              ? "Try adjusting your search or filters."
              : 'Register prompt templates via the API or click "New Prompt" to get started.'
          }
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {sortedGroups.map((group) => (
            <PromptCard
              key={group.name}
              group={group}
              onNavigate={(id) => navigate(`/prompts/${id}`)}
              onDelete={(id) => deleteMutation.mutate(id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
