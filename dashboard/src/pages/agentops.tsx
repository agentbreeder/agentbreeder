import { useState, useEffect } from "react";
import { authFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

const API = "/api/v1/agentops";

interface Agent {
  id: string;
  name: string;
  team: string;
  status: "healthy" | "degraded" | "down";
  health_score: number;
  last_deploy: string;
  model: string;
  framework: string;
}

interface FleetOverview {
  agents: Agent[];
  summary: {
    total: number;
    healthy: number;
    degraded: number;
    down: number;
    avg_health_score: number;
  };
}

interface HeatmapCell {
  agent_id: string;
  name: string;
  team: string;
  health_score: number;
  status: string;
}

function healthColor(score: number): string {
  if (score >= 90) return "bg-emerald-500";
  if (score >= 70) return "bg-yellow-500";
  if (score >= 40) return "bg-orange-500";
  return "bg-red-500";
}

function statusColor(status: string): string {
  if (status === "healthy") return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400";
  if (status === "degraded") return "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400";
  return "bg-red-500/15 text-red-700 dark:text-red-400";
}

export default function AgentOpsPage() {
  const [fleet, setFleet] = useState<FleetOverview | null>(null);
  const [heatmap, setHeatmap] = useState<HeatmapCell[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchAll() {
      try {
        const [fleetRes, heatmapRes] = await Promise.all([
          authFetch(`${API}/fleet`).then((r) => r.json()),
          authFetch(`${API}/fleet/heatmap`).then((r) => r.json()),
        ]);

        setFleet(fleetRes.data);
        setHeatmap(heatmapRes.data?.grid ?? []);
      } catch (err) {
        console.error("AgentOps fetch error:", err);
      } finally {
        setLoading(false);
      }
    }
    fetchAll();
  }, []);

  const summary = fleet?.summary;

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">AgentOps — Fleet Control</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Health status across all registered agents
        </p>
      </div>

      {/* Summary Cards */}
      {summary && (
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="rounded-lg border border-border bg-card p-5">
            <div className="text-xs text-muted-foreground">Total Agents</div>
            <div className="mt-1 text-2xl font-semibold">{summary.total}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {summary.healthy} healthy · {summary.degraded} degraded · {summary.down} down
            </div>
          </div>
          <div className="rounded-lg border border-border bg-card p-5">
            <div className="text-xs text-muted-foreground">Fleet Health Score</div>
            <div className="mt-1 text-2xl font-semibold">{summary.avg_health_score}</div>
            <div className="mt-1 text-xs text-muted-foreground">Average across all agents</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-5">
            <div className="text-xs text-muted-foreground">Agents Healthy</div>
            <div className="mt-1 text-2xl font-semibold text-emerald-600 dark:text-emerald-400">
              {summary.total > 0 ? Math.round((summary.healthy / summary.total) * 100) : 0}%
            </div>
            <div className="mt-1 text-xs text-muted-foreground">{summary.healthy} of {summary.total}</div>
          </div>
        </div>
      )}

      {/* Heatmap */}
      <div className="rounded-lg border border-border bg-card p-5">
        <h2 className="mb-4 text-sm font-medium">Fleet Health Heatmap</h2>
        {loading ? (
          <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">
            Loading...
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {heatmap.map((cell) => (
              <div
                key={cell.agent_id}
                title={`${cell.name} (${cell.team}) — ${cell.health_score}/100 — ${cell.status}`}
                className="group relative flex size-14 flex-col items-center justify-center rounded-lg text-white cursor-default"
                style={{ backgroundColor: "transparent" }}
              >
                <div
                  className={cn(
                    "absolute inset-0 rounded-lg opacity-80 transition-opacity group-hover:opacity-100",
                    healthColor(cell.health_score)
                  )}
                />
                <span className="relative z-10 text-lg font-bold leading-none text-white drop-shadow">
                  {cell.health_score}
                </span>
                <span className="relative z-10 mt-0.5 max-w-full truncate px-1 text-[9px] text-white/90 leading-tight text-center">
                  {cell.name.split("-")[0]}
                </span>
              </div>
            ))}
            {heatmap.length === 0 && (
              <div className="py-6 text-sm text-muted-foreground">No agents found</div>
            )}
          </div>
        )}
        <div className="mt-3 flex items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="inline-block size-2.5 rounded bg-emerald-500" /> 90–100 Healthy
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block size-2.5 rounded bg-yellow-500" /> 70–89 Degraded
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block size-2.5 rounded bg-orange-500" /> 40–69 Warning
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block size-2.5 rounded bg-red-500" /> 0–39 Down
          </span>
        </div>
      </div>

      {/* Agent Fleet Table */}
      <div className="rounded-lg border border-border bg-card p-5">
        <h2 className="mb-4 text-sm font-medium">All Agents</h2>
        {loading ? (
          <div className="py-8 text-center text-sm text-muted-foreground">Loading...</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="pb-2 font-medium">Agent</th>
                  <th className="pb-2 font-medium">Team</th>
                  <th className="pb-2 font-medium">Status</th>
                  <th className="pb-2 text-right font-medium">Health</th>
                </tr>
              </thead>
              <tbody>
                {(fleet?.agents ?? []).map((agent) => (
                  <tr key={agent.id} className="border-b border-border/50 last:border-0">
                    <td className="py-2.5">
                      <div className="font-medium">{agent.name}</div>
                      <div className="text-[10px] text-muted-foreground">{agent.model}</div>
                    </td>
                    <td className="py-2.5 text-muted-foreground">{agent.team}</td>
                    <td className="py-2.5">
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-[10px] font-semibold capitalize",
                          statusColor(agent.status)
                        )}
                      >
                        {agent.status}
                      </span>
                    </td>
                    <td className="py-2.5 text-right font-mono text-xs">{agent.health_score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
