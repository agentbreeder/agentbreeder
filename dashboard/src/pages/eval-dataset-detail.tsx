import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import {
  ArrowLeft,
  Plus,
  Download,
  Upload,
  Clock,
  FileText,
  Rows3,
  Tag,
} from "lucide-react";
import {
  api,
  type EvalDataset,
  type EvalDatasetRow,
} from "@/lib/api";
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
} from "@/components/ui/dialog";
import { useState } from "react";
import { SkeletonTableRows } from "@/components/ui/skeleton-table";
import { EmptyState } from "@/components/ui/empty-state";
import { useToast } from "@/hooks/use-toast";

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function StatsCard({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: string;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </div>
      <div className="mt-1 text-xl font-semibold tracking-tight">{value}</div>
    </div>
  );
}

function AddRowDialog({
  datasetId,
  open,
  onOpenChange,
}: {
  datasetId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [inputJson, setInputJson] = useState('{"query": ""}');
  const [expectedOutput, setExpectedOutput] = useState("");
  const [tagsStr, setTagsStr] = useState("");
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const addMutation = useMutation({
    mutationFn: () => {
      let parsedInput: Record<string, unknown>;
      try {
        parsedInput = JSON.parse(inputJson);
      } catch {
        throw new Error("Invalid JSON input");
      }
      return api.evals.datasets.addRows(datasetId, [
        {
          input: parsedInput,
          expected_output: expectedOutput,
          tags: tagsStr ? tagsStr.split(",").map((t) => t.trim()).filter(Boolean) : undefined,
        },
      ]);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["eval-dataset-rows", datasetId] });
      queryClient.invalidateQueries({ queryKey: ["eval-dataset", datasetId] });
      toast({ title: "Row added", variant: "success" });
      onOpenChange(false);
      setInputJson('{"query": ""}');
      setExpectedOutput("");
      setTagsStr("");
    },
    onError: (err: Error) => {
      toast({ title: "Failed to add row", description: err.message, variant: "error" });
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add Test Case</DialogTitle>
          <DialogDescription>Add a new test case row to this dataset.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Input (JSON)</label>
            <textarea
              value={inputJson}
              onChange={(e) => setInputJson(e.target.value)}
              rows={4}
              className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs outline-none focus:ring-1 focus:ring-ring"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Expected Output</label>
            <textarea
              value={expectedOutput}
              onChange={(e) => setExpectedOutput(e.target.value)}
              rows={3}
              placeholder="The expected agent response"
              className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-xs outline-none focus:ring-1 focus:ring-ring"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Tags (comma-separated)</label>
            <Input
              placeholder="e.g. edge-case, returns"
              value={tagsStr}
              onChange={(e) => setTagsStr(e.target.value)}
              className="mt-1 h-8 text-xs"
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            size="sm"
            disabled={!expectedOutput.trim() || addMutation.isPending}
            onClick={() => addMutation.mutate()}
          >
            {addMutation.isPending ? "Adding..." : "Add Row"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TestCasesTab({ datasetId }: { datasetId: string }) {
  const [addRowOpen, setAddRowOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["eval-dataset-rows", datasetId],
    queryFn: () => api.evals.datasets.listRows(datasetId, { per_page: 100 }),
    staleTime: 10_000,
  });

  const rows = (data?.data ?? []) as EvalDatasetRow[];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <span className="text-xs text-muted-foreground">{rows.length} test case{rows.length !== 1 ? "s" : ""}</span>
        <Button size="sm" className="h-7 gap-1 text-xs" onClick={() => setAddRowOpen(true)}>
          <Plus className="size-3" />
          Add Row
        </Button>
      </div>

      <AddRowDialog datasetId={datasetId} open={addRowOpen} onOpenChange={setAddRowOpen} />

      <div className="overflow-hidden rounded-lg border border-border">
        <div className="flex items-center gap-4 border-b border-border bg-muted/30 px-6 py-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          <span className="flex-1">Input</span>
          <span className="w-48">Expected Output</span>
          <span className="w-24 text-right">Tags</span>
        </div>

        {isLoading ? (
          <SkeletonTableRows rows={5} columns={3} />
        ) : error ? (
          <div className="px-6 py-12 text-center text-sm text-destructive">
            Failed to load rows: {(error as Error).message}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Rows3}
            title="No test cases yet"
            description="Add rows to this dataset to start evaluating agents."
          />
        ) : (
          rows.map((row) => (
            <div
              key={row.id}
              className="flex items-start gap-4 border-b border-border/50 px-6 py-3 last:border-0"
            >
              <div className="min-w-0 flex-1">
                <pre className="truncate font-mono text-xs text-foreground">
                  {JSON.stringify(row.input).length > 80
                    ? JSON.stringify(row.input).slice(0, 80) + "..."
                    : JSON.stringify(row.input)}
                </pre>
              </div>
              <div className="w-48">
                <p className="truncate text-xs text-muted-foreground">
                  {row.expected_output.length > 60
                    ? row.expected_output.slice(0, 60) + "..."
                    : row.expected_output}
                </p>
              </div>
              <div className="w-24 text-right">
                {row.tags && row.tags.length > 0 ? (
                  <div className="flex flex-wrap justify-end gap-1">
                    {row.tags.slice(0, 2).map((tag) => (
                      <Badge key={tag} variant="outline" className="text-[9px] px-1.5 py-0 h-4">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <span className="text-[10px] text-muted-foreground">--</span>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function ImportExportTab({ datasetId }: { datasetId: string }) {
  const [importContent, setImportContent] = useState("");
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const importMutation = useMutation({
    mutationFn: () => api.evals.datasets.importJsonl(datasetId, importContent),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["eval-dataset-rows", datasetId] });
      queryClient.invalidateQueries({ queryKey: ["eval-dataset", datasetId] });
      toast({
        title: "Import complete",
        description: `Imported ${result.data?.imported ?? 0} rows`,
        variant: "success",
      });
      setImportContent("");
    },
    onError: (err: Error) => {
      toast({ title: "Import failed", description: err.message, variant: "error" });
    },
  });

  const exportMutation = useMutation({
    mutationFn: () => api.evals.datasets.exportJsonl(datasetId),
    onSuccess: (result) => {
      const content = result.data?.content ?? "";
      const blob = new Blob([content], { type: "application/jsonl" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `dataset-${datasetId}.jsonl`;
      a.click();
      URL.revokeObjectURL(url);
      toast({ title: "Export downloaded", variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: "Export failed", description: err.message, variant: "error" });
    },
  });

  return (
    <div className="space-y-6">
      {/* Import */}
      <div className="rounded-lg border border-border bg-card p-5">
        <div className="mb-3 flex items-center gap-2">
          <Upload className="size-4 text-muted-foreground" />
          <h3 className="text-sm font-medium">Import JSONL</h3>
        </div>
        <p className="mb-3 text-xs text-muted-foreground">
          Paste JSONL content below. Each line should have &quot;input&quot; (object) and &quot;expected_output&quot; (string) fields.
        </p>
        <textarea
          value={importContent}
          onChange={(e) => setImportContent(e.target.value)}
          rows={8}
          placeholder={'{"input": {"query": "How do I return an item?"}, "expected_output": "You can return items within 30 days..."}\n{"input": {"query": "What are your hours?"}, "expected_output": "We are open 9am-5pm..."}'}
          className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs outline-none focus:ring-1 focus:ring-ring"
        />
        <div className="mt-3 flex justify-end">
          <Button
            size="sm"
            disabled={!importContent.trim() || importMutation.isPending}
            onClick={() => importMutation.mutate()}
          >
            {importMutation.isPending ? "Importing..." : "Import"}
          </Button>
        </div>
      </div>

      {/* Export */}
      <div className="rounded-lg border border-border bg-card p-5">
        <div className="mb-3 flex items-center gap-2">
          <Download className="size-4 text-muted-foreground" />
          <h3 className="text-sm font-medium">Export JSONL</h3>
        </div>
        <p className="mb-3 text-xs text-muted-foreground">
          Download this dataset as a JSONL file.
        </p>
        <Button
          size="sm"
          variant="outline"
          disabled={exportMutation.isPending}
          onClick={() => exportMutation.mutate()}
        >
          {exportMutation.isPending ? "Exporting..." : "Export JSONL"}
        </Button>
      </div>
    </div>
  );
}

export default function EvalDatasetDetailPage() {
  const { id } = useParams<{ id: string }>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["eval-dataset", id],
    queryFn: () => api.evals.datasets.get(id!),
    enabled: !!id,
  });

  const dataset: EvalDataset | null = data?.data ?? null;

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <div className="animate-pulse space-y-4">
          <div className="h-6 w-48 rounded bg-muted" />
          <div className="h-4 w-96 rounded bg-muted" />
          <div className="grid grid-cols-4 gap-4">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-20 rounded-lg border border-border bg-card" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (error || !dataset) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <div className="text-sm text-destructive">
          {error ? `Failed to load dataset: ${(error as Error).message}` : "Dataset not found"}
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl p-6">
      {/* Back link */}
      <Link
        to="/evals/datasets"
        className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3" />
        Back to datasets
      </Link>

      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold tracking-tight">{dataset.name}</h1>
          <Badge variant="outline" className="text-[10px]">v{dataset.version}</Badge>
          {dataset.team && (
            <Badge variant="secondary" className="text-[10px]">{dataset.team}</Badge>
          )}
        </div>
        {dataset.description && (
          <p className="mt-1 text-sm text-muted-foreground">{dataset.description}</p>
        )}
      </div>

      {/* Stats */}
      <div className="mb-6 grid grid-cols-4 gap-4">
        <StatsCard label="Row Count" value={String(dataset.row_count)} icon={Rows3} />
        <StatsCard label="Format" value={dataset.format || "jsonl"} icon={FileText} />
        <StatsCard label="Created" value={formatDate(dataset.created_at)} icon={Clock} />
        <StatsCard
          label="Last Updated"
          value={formatDate(dataset.updated_at)}
          icon={Tag}
        />
      </div>

      {/* Tabs */}
      <Tabs defaultValue="test-cases">
        <TabsList>
          <TabsTrigger value="test-cases">Test Cases</TabsTrigger>
          <TabsTrigger value="import-export">Import / Export</TabsTrigger>
        </TabsList>
        <TabsContent value="test-cases">
          <TestCasesTab datasetId={id!} />
        </TabsContent>
        <TabsContent value="import-export">
          <ImportExportTab datasetId={id!} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
