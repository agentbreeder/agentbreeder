import { useState, useCallback, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Database,
  Plus,
  Trash2,
  Search,
  Upload,
  FileText,
  Loader2,
  ArrowLeft,
  X,
  AlertCircle,
  CheckCircle2,
  File as FileIcon,
} from "lucide-react";
import { api, type RAGSearchHit, type IngestJob } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { GraphTab } from "@/components/GraphTab";

// --- Types ---

type View = "list" | "detail";

const EMBEDDING_MODELS = [
  { value: "openai/text-embedding-3-small", label: "OpenAI text-embedding-3-small" },
  { value: "ollama/nomic-embed-text", label: "Ollama nomic-embed-text" },
] as const;

const CHUNK_STRATEGIES = [
  { value: "fixed_size", label: "Fixed Size" },
  { value: "recursive", label: "Recursive Text Splitter" },
] as const;

const ACCEPTED_TYPES = ".pdf,.txt,.md,.csv,.json";

// --- Helpers ---

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

// --- Create Index Dialog ---

function CreateIndexDialog({
  open,
  onClose,
  onCreate,
  isPending,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (data: {
    name: string;
    description: string;
    embedding_model: string;
    chunk_strategy: string;
    chunk_size: number;
    chunk_overlap: number;
  }) => void;
  isPending: boolean;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState("openai/text-embedding-3-small");
  const [chunkStrategy, setChunkStrategy] = useState("fixed_size");
  const [chunkSize, setChunkSize] = useState(512);
  const [chunkOverlap, setChunkOverlap] = useState(64);

  if (!open) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    onCreate({
      name: name.trim(),
      description,
      embedding_model: embeddingModel,
      chunk_strategy: chunkStrategy,
      chunk_size: chunkSize,
      chunk_overlap: chunkOverlap,
    });
  };

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="fixed inset-x-0 top-[10%] z-50 mx-auto w-full max-w-lg">
        <form
          onSubmit={handleSubmit}
          className="overflow-hidden rounded-xl border border-border bg-card shadow-2xl"
        >
          <div className="flex items-center justify-between border-b border-border px-5 py-3">
            <h2 className="text-sm font-semibold">Create Vector Index</h2>
            <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">
              <X className="size-4" />
            </button>
          </div>

          <div className="space-y-4 p-5">
            <div>
              <label className="mb-1 block text-xs font-medium">Name</label>
              <Input
                placeholder="product-docs-index"
                value={name}
                onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9-_]/g, "-"))}
                className="h-8 text-xs font-mono"
                autoFocus
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium">Description</label>
              <textarea
                placeholder="What documents will this index contain?"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
                className="w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-xs outline-none focus:border-ring focus:ring-2 focus:ring-ring/50"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium">Embedding Model</label>
                <select
                  value={embeddingModel}
                  onChange={(e) => setEmbeddingModel(e.target.value)}
                  className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
                >
                  {EMBEDDING_MODELS.map((m) => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium">Chunk Strategy</label>
                <select
                  value={chunkStrategy}
                  onChange={(e) => setChunkStrategy(e.target.value)}
                  className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none"
                >
                  {CHUNK_STRATEGIES.map((s) => (
                    <option key={s.value} value={s.value}>{s.label}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium">Chunk Size</label>
                <Input
                  type="number"
                  value={chunkSize}
                  onChange={(e) => setChunkSize(Number(e.target.value))}
                  min={64}
                  max={8192}
                  className="h-8 text-xs"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium">Chunk Overlap</label>
                <Input
                  type="number"
                  value={chunkOverlap}
                  onChange={(e) => setChunkOverlap(Number(e.target.value))}
                  min={0}
                  max={1024}
                  className="h-8 text-xs"
                />
              </div>
            </div>
          </div>

          <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium transition-colors hover:bg-accent"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!name.trim() || isPending}
              className="flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background transition-colors hover:bg-foreground/90 disabled:opacity-50"
            >
              {isPending ? <Loader2 className="size-3 animate-spin" /> : <Plus className="size-3" />}
              Create Index
            </button>
          </div>
        </form>
      </div>
    </>
  );
}

// --- File Upload / Ingest Panel ---

function IngestPanel({
  indexId,
  onComplete,
}: {
  indexId: string;
  onComplete: () => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [lastJob, setLastJob] = useState<IngestJob | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const ingestMutation = useMutation({
    mutationFn: async () => {
      const resp = await api.rag.ingest(indexId, files);
      return resp.data;
    },
    onSuccess: (job) => {
      setLastJob(job);
      setFiles([]);
      onComplete();
    },
  });

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => setIsDragging(false), []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const dropped = Array.from(e.dataTransfer.files);
    setFiles((prev) => [...prev, ...dropped]);
  }, []);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      setFiles((prev) => [...prev, ...Array.from(e.target.files!)]);
    }
  }, []);

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  return (
    <div className="space-y-3">
      <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
        Ingest Documents
      </h3>

      {/* Drop zone */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed py-8 text-center transition-colors",
          isDragging
            ? "border-foreground/40 bg-accent/50"
            : "border-border hover:border-foreground/20 hover:bg-accent/30"
        )}
      >
        <Upload className="mb-2 size-5 text-muted-foreground/60" />
        <p className="text-xs text-muted-foreground">
          Drag & drop files or click to browse
        </p>
        <p className="mt-1 text-[10px] text-muted-foreground/60">
          PDF, TXT, MD, CSV, JSON
        </p>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPTED_TYPES}
          onChange={handleFileSelect}
          className="hidden"
        />
      </div>

      {/* File list */}
      {files.length > 0 && (
        <div className="space-y-1.5">
          {files.map((file, i) => (
            <div
              key={`${file.name}-${i}`}
              className="flex items-center gap-2 rounded-md border border-border px-3 py-1.5"
            >
              <FileIcon className="size-3 text-muted-foreground" />
              <span className="flex-1 truncate text-xs">{file.name}</span>
              <span className="text-[10px] text-muted-foreground">
                {(file.size / 1024).toFixed(1)} KB
              </span>
              <button
                onClick={() => removeFile(i)}
                className="text-muted-foreground hover:text-destructive"
              >
                <X className="size-3" />
              </button>
            </div>
          ))}

          <button
            onClick={() => ingestMutation.mutate()}
            disabled={ingestMutation.isPending}
            className="flex w-full items-center justify-center gap-1.5 rounded-md bg-foreground px-3 py-2 text-xs font-medium text-background transition-colors hover:bg-foreground/90 disabled:opacity-50"
          >
            {ingestMutation.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <Upload className="size-3" />
            )}
            {ingestMutation.isPending
              ? "Ingesting..."
              : `Ingest ${files.length} file${files.length > 1 ? "s" : ""}`}
          </button>
        </div>
      )}

      {/* Last job status */}
      {lastJob && (
        <div
          className={cn(
            "flex items-center gap-2 rounded-md px-3 py-2 text-xs",
            lastJob.status === "completed"
              ? "bg-emerald-500/10 text-emerald-600"
              : lastJob.status === "failed"
                ? "bg-destructive/10 text-destructive"
                : "bg-accent text-foreground"
          )}
        >
          {lastJob.status === "completed" ? (
            <CheckCircle2 className="size-3" />
          ) : lastJob.status === "failed" ? (
            <AlertCircle className="size-3" />
          ) : (
            <Loader2 className="size-3 animate-spin" />
          )}
          <span>
            {lastJob.status === "completed"
              ? `Ingested ${lastJob.total_chunks} chunks from ${lastJob.total_files} file(s)`
              : lastJob.status === "failed"
                ? `Failed: ${lastJob.error}`
                : `${lastJob.status} — ${lastJob.progress_pct}%`}
          </span>
        </div>
      )}

      {ingestMutation.error && (
        <div className="flex items-center gap-1.5 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <AlertCircle className="size-3 shrink-0" />
          {(ingestMutation.error as Error).message}
        </div>
      )}
    </div>
  );
}

// --- Search Panel ---

function SearchPanel({ indexId }: { indexId: string }) {
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(10);
  const [vectorWeight, setVectorWeight] = useState(0.7);
  const [results, setResults] = useState<RAGSearchHit[]>([]);
  const [searched, setSearched] = useState(false);

  const searchMutation = useMutation({
    mutationFn: async () => {
      const resp = await api.rag.search({
        index_id: indexId,
        query,
        top_k: topK,
        vector_weight: vectorWeight,
        text_weight: 1 - vectorWeight,
      });
      return resp.data;
    },
    onSuccess: (data) => {
      setResults(data.results);
      setSearched(true);
    },
  });

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) searchMutation.mutate();
  };

  return (
    <div className="space-y-4">
      <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
        Search Index
      </h3>

      <form onSubmit={handleSearch} className="space-y-3">
        <div className="flex gap-2">
          <Input
            placeholder="Enter search query..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="h-8 flex-1 text-xs"
          />
          <button
            type="submit"
            disabled={!query.trim() || searchMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background transition-colors hover:bg-foreground/90 disabled:opacity-50"
          >
            {searchMutation.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <Search className="size-3" />
            )}
            Search
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 flex items-center justify-between text-[10px] text-muted-foreground">
              <span>Top K</span>
              <span className="font-mono">{topK}</span>
            </label>
            <input
              type="range"
              min={1}
              max={50}
              value={topK}
              onChange={(e) => setTopK(Number(e.target.value))}
              className="w-full accent-foreground"
            />
          </div>
          <div>
            <label className="mb-1 flex items-center justify-between text-[10px] text-muted-foreground">
              <span>Vector Weight</span>
              <span className="font-mono">{vectorWeight.toFixed(1)}</span>
            </label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.1}
              value={vectorWeight}
              onChange={(e) => setVectorWeight(Number(e.target.value))}
              className="w-full accent-foreground"
            />
          </div>
        </div>
      </form>

      {/* Results */}
      {searched && results.length === 0 && (
        <div className="flex flex-col items-center py-6 text-center">
          <Search className="mb-2 size-5 text-muted-foreground/40" />
          <p className="text-xs text-muted-foreground">No results found</p>
        </div>
      )}

      {results.length > 0 && (
        <div className="space-y-2">
          <p className="text-[10px] text-muted-foreground">
            {results.length} result{results.length !== 1 ? "s" : ""}
          </p>
          {results.map((hit, i) => (
            <div
              key={hit.chunk_id}
              className="space-y-1.5 rounded-md border border-border p-3"
            >
              <div className="flex items-center gap-2">
                <span className="flex size-5 items-center justify-center rounded bg-muted text-[10px] font-medium">
                  {i + 1}
                </span>
                <Badge variant="outline" className="text-[9px]">
                  {(hit.score * 100).toFixed(1)}%
                </Badge>
                <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground">
                  <FileText className="size-3" />
                  {hit.source}
                </span>
              </div>
              <p className="text-xs leading-relaxed text-foreground/80">{hit.text}</p>
            </div>
          ))}
        </div>
      )}

      {searchMutation.error && (
        <div className="flex items-center gap-1.5 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <AlertCircle className="size-3 shrink-0" />
          {(searchMutation.error as Error).message}
        </div>
      )}
    </div>
  );
}

// --- Index Detail View ---

function IndexDetailView({
  indexId,
  onBack,
  activeTab,
  setActiveTab,
}: {
  indexId: string;
  onBack: () => void;
  activeTab: "overview" | "graph";
  setActiveTab: (tab: "overview" | "graph") => void;
}) {
  const queryClient = useQueryClient();
  const { data: indexResp, isLoading } = useQuery({
    queryKey: ["rag-index", indexId],
    queryFn: () => api.rag.getIndex(indexId),
  });

  const index = indexResp?.data;

  const isGraphIndex =
    index?.index_type === "graph" || index?.index_type === "hybrid";

  const handleIngestComplete = () => {
    queryClient.invalidateQueries({ queryKey: ["rag-index", indexId] });
  };

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!index) {
    return (
      <div className="p-6 text-center text-sm text-muted-foreground">
        Index not found.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-border px-6 py-3">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-3" />
          Indexes
        </button>
        <span className="text-xs text-muted-foreground">/</span>
        <h1 className="text-sm font-semibold">{index.name}</h1>
        <Badge variant="outline" className="ml-2 text-[9px]">
          {index.dimensions}d
        </Badge>
      </div>

      {/* Tab bar — only visible for graph/hybrid indexes */}
      {isGraphIndex && (
        <div className="flex gap-1 border-b border-border px-6">
          <button
            onClick={() => setActiveTab("overview")}
            className={`px-4 py-2 text-sm font-medium transition-colors ${activeTab === "overview" ? "text-foreground border-b-2 border-foreground" : "text-muted-foreground hover:text-foreground"}`}
          >
            Overview
          </button>
          <button
            onClick={() => setActiveTab("graph")}
            className={`px-4 py-2 text-sm font-medium transition-colors ${activeTab === "graph" ? "text-foreground border-b-2 border-foreground" : "text-muted-foreground hover:text-foreground"}`}
          >
            Knowledge Graph
          </button>
        </div>
      )}

      {/* Content: 3-column overview */}
      {(!isGraphIndex || activeTab === "overview") && (
        <div className="grid flex-1 grid-cols-[280px_1fr_320px] overflow-hidden">
          {/* Left: Stats */}
          <div className="overflow-y-auto border-r border-border p-4">
            <h2 className="mb-4 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Index Details
            </h2>
            <div className="space-y-3">
              <div className="rounded-md border border-border p-3">
                <p className="text-[10px] text-muted-foreground">Documents</p>
                <p className="text-lg font-semibold">{formatNumber(index.doc_count)}</p>
              </div>
              <div className="rounded-md border border-border p-3">
                <p className="text-[10px] text-muted-foreground">Chunks</p>
                <p className="text-lg font-semibold">{formatNumber(index.chunk_count)}</p>
              </div>
              <div className="rounded-md border border-border p-3">
                <p className="text-[10px] text-muted-foreground">Dimensions</p>
                <p className="text-lg font-semibold">{index.dimensions}</p>
              </div>

              <div className="space-y-2 pt-2">
                <div>
                  <p className="text-[10px] text-muted-foreground">Embedding Model</p>
                  <p className="text-xs font-mono">{index.embedding_model}</p>
                </div>
                <div>
                  <p className="text-[10px] text-muted-foreground">Chunk Strategy</p>
                  <p className="text-xs">{index.chunk_strategy}</p>
                </div>
                <div>
                  <p className="text-[10px] text-muted-foreground">Chunk Size / Overlap</p>
                  <p className="text-xs font-mono">
                    {index.chunk_size} / {index.chunk_overlap}
                  </p>
                </div>
                {index.description && (
                  <div>
                    <p className="text-[10px] text-muted-foreground">Description</p>
                    <p className="text-xs">{index.description}</p>
                  </div>
                )}
                <div>
                  <p className="text-[10px] text-muted-foreground">Created</p>
                  <p className="text-xs">{formatDate(index.created_at)}</p>
                </div>
              </div>
            </div>
          </div>

          {/* Center: Search */}
          <div className="overflow-y-auto p-4">
            <SearchPanel indexId={indexId} />
          </div>

          {/* Right: Ingest */}
          <div className="overflow-y-auto border-l border-border p-4">
            <IngestPanel indexId={indexId} onComplete={handleIngestComplete} />
          </div>
        </div>
      )}

      {/* Graph tab content */}
      {isGraphIndex && activeTab === "graph" && (
        <div className="flex-1 overflow-hidden">
          <GraphTab indexId={index.id} />
        </div>
      )}
    </div>
  );
}

// --- Main Page ---

export default function RAGBuilderPage() {
  const [view, setView] = useState<View>("list");
  const [selectedIndexId, setSelectedIndexId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<"overview" | "graph">("overview");

  const queryClient = useQueryClient();

  const { data: indexesResp, isLoading } = useQuery({
    queryKey: ["rag-indexes"],
    queryFn: () => api.rag.listIndexes(),
  });

  const createMutation = useMutation({
    mutationFn: async (data: {
      name: string;
      description: string;
      embedding_model: string;
      chunk_strategy: string;
      chunk_size: number;
      chunk_overlap: number;
    }) => {
      const resp = await api.rag.createIndex(data);
      return resp.data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["rag-indexes"] });
      setCreateOpen(false);
      setSelectedIndexId(data.id);
      setActiveTab("overview");
      setView("detail");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.rag.deleteIndex(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rag-indexes"] });
    },
  });

  const indexes = indexesResp?.data ?? [];

  if (view === "detail" && selectedIndexId) {
    return (
      <IndexDetailView
        indexId={selectedIndexId}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onBack={() => {
          setView("list");
          setSelectedIndexId(null);
          setActiveTab("overview");
          queryClient.invalidateQueries({ queryKey: ["rag-indexes"] });
        }}
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-6 py-3">
        <div className="flex items-center gap-3">
          <Database className="size-4 text-muted-foreground" />
          <h1 className="text-sm font-semibold">RAG Builder</h1>
          <Badge variant="outline" className="text-[10px]">
            {indexes.length} index{indexes.length !== 1 ? "es" : ""}
          </Badge>
        </div>
        <button
          onClick={() => setCreateOpen(true)}
          className="flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background transition-colors hover:bg-foreground/90"
        >
          <Plus className="size-3" />
          New Index
        </button>
      </div>

      <CreateIndexDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={(data) => createMutation.mutate(data)}
        isPending={createMutation.isPending}
      />

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : indexes.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Database className="mb-3 size-8 text-muted-foreground/30" />
            <h2 className="text-sm font-medium">No vector indexes yet</h2>
            <p className="mt-1 max-w-sm text-xs text-muted-foreground">
              Create a vector index to start ingesting documents and building RAG pipelines.
              Upload PDFs, text files, CSVs, or JSON to auto-chunk and embed.
            </p>
            <button
              onClick={() => setCreateOpen(true)}
              className="mt-4 flex items-center gap-1.5 rounded-md bg-foreground px-4 py-2 text-xs font-medium text-background transition-colors hover:bg-foreground/90"
            >
              <Plus className="size-3" />
              Create First Index
            </button>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {indexes.map((idx) => (
              <div
                key={idx.id}
                className="group cursor-pointer rounded-lg border border-border bg-card p-4 transition-all hover:border-foreground/20 hover:shadow-sm"
                onClick={() => {
                  setSelectedIndexId(idx.id);
                  setActiveTab("overview");
                  setView("detail");
                }}
              >
                <div className="mb-3 flex items-start justify-between">
                  <div className="flex items-center gap-2">
                    <Database className="size-4 text-muted-foreground" />
                    <h3 className="text-sm font-medium">{idx.name}</h3>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`Delete index "${idx.name}"?`)) {
                        deleteMutation.mutate(idx.id);
                      }
                    }}
                    className="rounded p-1 text-muted-foreground opacity-0 transition-all hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
                  >
                    <Trash2 className="size-3" />
                  </button>
                </div>

                {idx.description && (
                  <p className="mb-3 text-xs text-muted-foreground line-clamp-2">
                    {idx.description}
                  </p>
                )}

                <div className="flex flex-wrap gap-1.5">
                  <Badge variant="outline" className="text-[9px]">
                    {formatNumber(idx.doc_count)} docs
                  </Badge>
                  <Badge variant="outline" className="text-[9px]">
                    {formatNumber(idx.chunk_count)} chunks
                  </Badge>
                  <Badge variant="outline" className="text-[9px]">
                    {idx.dimensions}d
                  </Badge>
                </div>

                <div className="mt-3 flex items-center justify-between text-[10px] text-muted-foreground">
                  <span>{idx.embedding_model.split("/").pop()}</span>
                  <span>{formatDate(idx.created_at)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
