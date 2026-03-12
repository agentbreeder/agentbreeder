import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useSearchParams, Link } from "react-router-dom";
import { FlaskConical, Search, Filter, Plus } from "lucide-react";
import { api, type EvalDataset } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { useSortable } from "@/hooks/use-sortable";
import { SortableColumnHeader } from "@/components/ui/sortable-header";
import { SkeletonTableRows } from "@/components/ui/skeleton-table";
import { EmptyState } from "@/components/ui/empty-state";
import { useToast } from "@/hooks/use-toast";

function timeSince(dateStr: string): string {
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (seconds < 60) return "now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d`;
  return `${Math.floor(days / 30)}mo`;
}

function DatasetRow({ dataset }: { dataset: EvalDataset }) {
  const age = timeSince(dataset.created_at);
  return (
    <Link
      to={`/evals/datasets/${dataset.id}`}
      className="group flex items-center gap-4 border-b border-border/50 px-6 py-3.5 transition-colors last:border-0 hover:bg-muted/30"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium group-hover:text-primary">
            {dataset.name}
          </span>
          <Badge variant="outline" className="text-[10px]">
            v{dataset.version}
          </Badge>
        </div>
        {dataset.description && (
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {dataset.description}
          </p>
        )}
        {dataset.tags && dataset.tags.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {dataset.tags.slice(0, 3).map((tag) => (
              <Badge key={tag} variant="outline" className="text-[9px] px-1.5 py-0 h-4">
                {tag}
              </Badge>
            ))}
            {dataset.tags.length > 3 && (
              <span className="text-[9px] text-muted-foreground">
                +{dataset.tags.length - 3}
              </span>
            )}
          </div>
        )}
      </div>

      <span className="w-24 text-right text-xs text-muted-foreground">
        {dataset.agent_name || "--"}
      </span>

      <span className="w-20 text-right text-xs text-muted-foreground">
        {dataset.team || "--"}
      </span>

      <span className="w-16 text-right font-mono text-xs text-muted-foreground">
        {dataset.row_count}
      </span>

      <Badge variant="outline" className="text-[10px] w-14 justify-center">
        {dataset.format || "jsonl"}
      </Badge>

      <span className="w-14 text-right font-mono text-[10px] text-muted-foreground">
        {age}
      </span>
    </Link>
  );
}

function CreateDatasetDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [team, setTeam] = useState("");
  const [tagsStr, setTagsStr] = useState("");
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const createMutation = useMutation({
    mutationFn: () =>
      api.evals.datasets.create({
        name,
        description: description || undefined,
        team: team || undefined,
        tags: tagsStr ? tagsStr.split(",").map((t) => t.trim()).filter(Boolean) : undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["eval-datasets"] });
      toast({ title: "Dataset created", variant: "success" });
      onOpenChange(false);
      setName("");
      setDescription("");
      setTeam("");
      setTagsStr("");
    },
    onError: (err: Error) => {
      toast({ title: "Failed to create dataset", description: err.message, variant: "error" });
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New Dataset</DialogTitle>
          <DialogDescription>
            Create an evaluation dataset for testing agent performance.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Name</label>
            <Input
              placeholder="e.g. support-agent-golden-set"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="mt-1 h-8 text-xs"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Description</label>
            <Input
              placeholder="Optional description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="mt-1 h-8 text-xs"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Team</label>
            <Input
              placeholder="e.g. customer-success"
              value={team}
              onChange={(e) => setTeam(e.target.value)}
              className="mt-1 h-8 text-xs"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Tags (comma-separated)</label>
            <Input
              placeholder="e.g. production, regression"
              value={tagsStr}
              onChange={(e) => setTagsStr(e.target.value)}
              className="mt-1 h-8 text-xs"
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            size="sm"
            disabled={!name.trim() || createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            {createMutation.isPending ? "Creating..." : "Create Dataset"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function EvalDatasetsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState(searchParams.get("q") ?? "");
  const [teamFilter, setTeamFilter] = useState(searchParams.get("team") ?? "");
  const [createOpen, setCreateOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["eval-datasets", { teamFilter }],
    queryFn: () =>
      api.evals.datasets.list({
        team: teamFilter || undefined,
      }),
    staleTime: 10_000,
  });

  const datasets = data?.data ?? [];
  const total = data?.meta?.total ?? datasets.length;
  const hasFilter = !!(search || teamFilter);

  // Client-side search filter
  const filtered = search
    ? datasets.filter(
        (d) =>
          d.name.toLowerCase().includes(search.toLowerCase()) ||
          d.description?.toLowerCase().includes(search.toLowerCase()) ||
          d.agent_name?.toLowerCase().includes(search.toLowerCase())
      )
    : datasets;

  const { sortedData, sortKey, sortDirection, toggleSort } = useSortable(
    filtered as unknown as Record<string, unknown>[],
    "name",
    "asc"
  );
  const sortedDatasets = sortedData as unknown as EvalDataset[];

  const handleSearch = (value: string) => {
    setSearch(value);
    const sp = new URLSearchParams(searchParams);
    if (value) sp.set("q", value);
    else sp.delete("q");
    setSearchParams(sp, { replace: true });
  };

  return (
    <div className="mx-auto max-w-5xl p-6">
      {/* Header */}
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Evaluation Datasets</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {total} dataset{total !== 1 ? "s" : ""} in registry
          </p>
        </div>
        <Button size="sm" className="h-8 gap-1.5 text-xs" onClick={() => setCreateOpen(true)}>
          <Plus className="size-3.5" />
          New Dataset
        </Button>
      </div>

      <CreateDatasetDialog open={createOpen} onOpenChange={setCreateOpen} />

      {/* Filters */}
      <div className="mb-4 flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search datasets..."
            value={search}
            onChange={(e) => handleSearch(e.target.value)}
            className="h-8 pl-9 text-xs"
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter className="size-3.5 text-muted-foreground" />
          <select
            value={teamFilter}
            onChange={(e) => setTeamFilter(e.target.value)}
            className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none"
          >
            <option value="">All teams</option>
            {[...new Set(datasets.map((d) => d.team).filter(Boolean))].sort().map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-lg border border-border">
        <div className="flex items-center gap-4 border-b border-border bg-muted/30 px-6 py-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          <span className="flex-1">
            <SortableColumnHeader
              sortKey="name"
              currentSortKey={sortKey}
              currentDirection={sortDirection}
              onSort={toggleSort}
            >
              Dataset
            </SortableColumnHeader>
          </span>
          <span className="w-24 text-right">
            <SortableColumnHeader
              sortKey="agent_name"
              currentSortKey={sortKey}
              currentDirection={sortDirection}
              onSort={toggleSort}
            >
              Agent
            </SortableColumnHeader>
          </span>
          <span className="w-20 text-right">
            <SortableColumnHeader
              sortKey="team"
              currentSortKey={sortKey}
              currentDirection={sortDirection}
              onSort={toggleSort}
            >
              Team
            </SortableColumnHeader>
          </span>
          <span className="w-16 text-right">
            <SortableColumnHeader
              sortKey="row_count"
              currentSortKey={sortKey}
              currentDirection={sortDirection}
              onSort={toggleSort}
            >
              Rows
            </SortableColumnHeader>
          </span>
          <span className="w-14 text-center">Format</span>
          <span className="w-14 text-right">
            <SortableColumnHeader
              sortKey="created_at"
              currentSortKey={sortKey}
              currentDirection={sortDirection}
              onSort={toggleSort}
            >
              Created
            </SortableColumnHeader>
          </span>
        </div>

        {isLoading ? (
          <SkeletonTableRows rows={5} columns={4} />
        ) : error ? (
          <div className="px-6 py-12 text-center text-sm text-destructive">
            Failed to load datasets: {(error as Error).message}
          </div>
        ) : sortedDatasets.length === 0 ? (
          <EmptyState
            icon={FlaskConical}
            title={hasFilter ? "No datasets match your filters" : "No datasets yet"}
            description={
              hasFilter
                ? "Try adjusting your search or filters."
                : "No datasets yet. Create one to start evaluating agents."
            }
          />
        ) : (
          sortedDatasets.map((dataset) => (
            <DatasetRow key={dataset.id} dataset={dataset} />
          ))
        )}
      </div>
    </div>
  );
}
