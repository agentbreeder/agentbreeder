import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Workflow, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { api, type Orchestration } from "@/lib/api";
import { useToast } from "@/hooks/use-toast";

const STRATEGY_COLORS: Record<string, string> = {
  router: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  sequential: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  parallel: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
  hierarchical: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  supervisor: "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-400",
  fan_out_fan_in: "bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-400",
};

function OrchestrationCard({ orch, onDelete }: { orch: Orchestration; onDelete: (id: string) => void }) {
  const agentCount = Object.keys(orch.agents_config ?? {}).length;
  return (
    <Link
      to={`/orchestrations/builder?id=${orch.id}`}
      className="group rounded-lg border border-border bg-card p-5 transition-all hover:border-foreground/20 hover:shadow-md"
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <Workflow className="size-5 text-muted-foreground" />
          <h3 className="font-semibold text-foreground group-hover:text-primary">{orch.name}</h3>
        </div>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${STRATEGY_COLORS[orch.strategy] ?? "bg-muted text-muted-foreground"}`}
        >
          {orch.strategy}
        </span>
      </div>

      <p className="mt-2 text-sm text-muted-foreground line-clamp-2">
        {orch.description || "No description"}
      </p>

      <div className="mt-4 flex items-center gap-3 text-xs text-muted-foreground">
        <span>v{orch.version}</span>
        <span>{agentCount} agent{agentCount === 1 ? "" : "s"}</span>
        <Badge variant={orch.status === "deployed" ? "default" : "outline"}>{orch.status}</Badge>
      </div>

      <div className="mt-3 flex items-center justify-between">
        <div className="flex flex-wrap gap-1">
          {(orch.tags ?? []).slice(0, 4).map((tag) => (
            <span key={tag} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {tag}
            </span>
          ))}
        </div>
        <button
          className="rounded p-1 text-muted-foreground opacity-0 hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            if (confirm(`Delete "${orch.name}"?`)) onDelete(orch.id);
          }}
          title="Delete"
        >
          <Trash2 className="size-4" />
        </button>
      </div>
    </Link>
  );
}

export default function OrchestrationsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const { data: response, isLoading, error } = useQuery({
    queryKey: ["orchestrations"],
    queryFn: () => api.orchestrations.list(),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.orchestrations.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orchestrations"] });
      toast({ title: "Orchestration deleted", variant: "success" });
    },
    onError: (err: Error) => {
      toast({ title: `Delete failed: ${err.message}`, variant: "error" });
    },
  });

  const orchestrations = response?.data ?? [];

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Orchestrations</h1>
          <p className="text-sm text-muted-foreground">
            Multi-agent workflows defined in this workspace
          </p>
        </div>
        <button
          onClick={() => navigate("/orchestrations/builder")}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          <Plus className="size-4" /> New Orchestration
        </button>
      </div>

      {error ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          Failed to load orchestrations: {error.message}
        </div>
      ) : isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-44 animate-pulse rounded-lg border border-border bg-muted" />
          ))}
        </div>
      ) : orchestrations.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-16">
          <Workflow className="size-10 text-muted-foreground/40" />
          <p className="mt-3 text-sm text-muted-foreground">No orchestrations yet</p>
          <button
            onClick={() => navigate("/orchestrations/builder")}
            className="mt-3 text-sm text-primary hover:underline"
          >
            Build your first orchestration
          </button>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {orchestrations.map((o) => (
            <OrchestrationCard key={o.id} orch={o} onDelete={(id) => deleteMutation.mutate(id)} />
          ))}
        </div>
      )}
    </div>
  );
}
