import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Bot, Wrench, Circle, ArrowRight, Activity } from "lucide-react";
import { api, type Agent } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_COLORS: Record<string, string> = {
  running: "text-emerald-500",
  deploying: "text-amber-500 animate-pulse",
  stopped: "text-muted-foreground",
  failed: "text-destructive",
};

function StatCard({
  icon: Icon,
  label,
  value,
  to,
}: {
  icon: typeof Bot;
  label: string;
  value: number | string;
  to: string;
}) {
  return (
    <Link
      to={to}
      className="group flex items-center gap-4 rounded-lg border border-border p-4 transition-colors hover:bg-muted/30"
    >
      <div className="flex size-10 items-center justify-center rounded-lg bg-muted">
        <Icon className="size-4.5 text-muted-foreground" />
      </div>
      <div>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
        <div className="text-xs text-muted-foreground">{label}</div>
      </div>
      <ArrowRight className="ml-auto size-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
    </Link>
  );
}

function RecentAgent({ agent }: { agent: Agent }) {
  return (
    <Link
      to={`/agents/${agent.id}`}
      className="group flex items-center gap-3 rounded-md px-3 py-2 transition-colors hover:bg-muted/30"
    >
      <Circle className={cn("size-2 fill-current", STATUS_COLORS[agent.status])} />
      <span className="flex-1 truncate text-sm">{agent.name}</span>
      <span className="text-xs text-muted-foreground">{agent.framework}</span>
    </Link>
  );
}

export default function HomePage() {
  const agentsQuery = useQuery({
    queryKey: ["agents", {}],
    queryFn: () => api.agents.list({ per_page: 5 }),
    staleTime: 10_000,
  });

  const toolsQuery = useQuery({
    queryKey: ["tools", {}],
    queryFn: () => api.tools.list(),
    staleTime: 10_000,
  });

  const agents = agentsQuery.data?.data ?? [];
  const totalAgents = agentsQuery.data?.meta.total ?? 0;
  const totalTools = toolsQuery.data?.meta.total ?? 0;
  const running = agents.filter((a) => a.status === "running").length;

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-8">
        <h1 className="text-lg font-semibold tracking-tight">Overview</h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Agent Garden registry at a glance
        </p>
      </div>

      {/* Stats */}
      <div className="mb-8 grid gap-3 sm:grid-cols-3">
        <StatCard icon={Bot} label="Agents" value={totalAgents} to="/agents" />
        <StatCard icon={Wrench} label="Tools" value={totalTools} to="/tools" />
        <StatCard icon={Activity} label="Running" value={running} to="/agents?status=running" />
      </div>

      {/* Recent agents */}
      <div className="rounded-lg border border-border">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Recent Agents
          </h2>
          <Link
            to="/agents"
            className="text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            View all
          </Link>
        </div>
        {agentsQuery.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-8 animate-pulse rounded bg-muted" />
            ))}
          </div>
        ) : agents.length === 0 ? (
          <div className="px-4 py-8 text-center text-xs text-muted-foreground">
            No agents yet. Deploy one with <code className="rounded bg-muted px-1">garden deploy</code>
          </div>
        ) : (
          <div className="divide-y divide-border/50">
            {agents.map((agent) => (
              <RecentAgent key={agent.id} agent={agent} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
