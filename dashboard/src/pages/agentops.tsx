import { useState, useEffect } from "react";
import { Activity, AlertTriangle, DollarSign, Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import { ComingSoonBanner } from "@/components/coming-soon-badge";

const API = "/api/v1/agentops";

interface Agent {
  id: string;
  name: string;
  team: string;
  status: "healthy" | "degraded" | "down";
  health_score: number;
  invocations_24h: number;
  error_rate_pct: number;
  avg_latency_ms: number;
  cost_24h_usd: number;
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

interface Event {
  id: string;
  timestamp: string;
  type: string;
  agent_name: string;
  message: string;
  severity: "info" | "warning" | "critical";
}

interface TeamMetric {
  team: string;
  agent_count: number;
  total_cost_24h: number;
  avg_health_score: number;
  incidents_open: number;
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

function severityColor(severity: string): string {
  if (severity === "critical") return "bg-red-500/15 text-red-700 dark:text-red-400";
  if (severity === "warning") return "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400";
  return "bg-blue-500/15 text-blue-700 dark:text-blue-400";
}

function TopNCard({
  title,
  agents,
  valueKey,
  valueFormat,
  icon: Icon,
  color,
}: {
  title: string;
  agents: Agent[];
  valueKey: keyof Agent;
  valueFormat: (v: number) => string;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-medium">{title}</h3>
        <div className={cn("rounded-md p-1.5", color)}>
          <Icon className="size-3.5" />
        </div>
      </div>
      <div className="space-y-2">
        {agents.slice(0, 5).map((a, i) => (
          <div key={a.id} className="flex items-center gap-2 text-sm">
            <span className="flex size-5 items-center justify-center rounded-full bg-muted text-[10px] font-semibold text-muted-foreground">
              {i + 1}
            </span>
            <span className="flex-1 truncate font-medium">{a.name}</span>
            <span className="font-mono text-xs text-muted-foreground">
              {valueFormat(a[valueKey] as number)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function AgentOpsPage() {
  const [fleet, setFleet] = useState<FleetOverview | null>(null);
  const [heatmap, setHeatmap] = useState<HeatmapCell[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [teams, setTeams] = useState<TeamMetric[]>([]);
  const [topByCost, setTopByCost] = useState<Agent[]>([]);
  const [topByErrors, setTopByErrors] = useState<Agent[]>([]);
  const [topByLatency, setTopByLatency] = useState<Agent[]>([]);
  const [topByInvocations, setTopByInvocations] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchAll() {
      try {
        const [fleetRes, heatmapRes, eventsRes, teamsRes, costRes, errorsRes, latencyRes, invocRes] =
          await Promise.all([
            fetch(`${API}/fleet`).then((r) => r.json()),
            fetch(`${API}/fleet/heatmap`).then((r) => r.json()),
            fetch(`${API}/events?limit=20`).then((r) => r.json()),
            fetch(`${API}/teams`).then((r) => r.json()),
            fetch(`${API}/top-agents?metric=cost&limit=5`).then((r) => r.json()),
            fetch(`${API}/top-agents?metric=errors&limit=5`).then((r) => r.json()),
            fetch(`${API}/top-agents?metric=latency&limit=5`).then((r) => r.json()),
            fetch(`${API}/top-agents?metric=invocations&limit=5`).then((r) => r.json()),
          ]);

        setFleet(fleetRes.data);
        setHeatmap(heatmapRes.data?.grid ?? []);
        setEvents(eventsRes.data ?? []);
        setTeams(teamsRes.data ?? []);
        setTopByCost(costRes.data ?? []);
        setTopByErrors(errorsRes.data ?? []);
        setTopByLatency(latencyRes.data ?? []);
        setTopByInvocations(invocRes.data ?? []);
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
      <ComingSoonBanner
        feature="Real fleet telemetry"
        issue="#206"
        description="Fleet health, top-agents, events, and team comparisons are currently served from an in-memory seed store. Persistence to real deploy + cost + trace data is in progress."
      />
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">AgentOps — Fleet Control</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Unified health, cost, and operations visibility across all deployed agents
        </p>
      </div>

      {/* Summary Cards */}
      {summary && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
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
          <div className="rounded-lg border border-border bg-card p-5">
            <div className="text-xs text-muted-foreground">Active Incidents</div>
            <div className="mt-1 text-2xl font-semibold text-red-600 dark:text-red-400">
              {teams.reduce((sum, t) => sum + t.incidents_open, 0)}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">Across all teams</div>
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

      {/* Top-N Widgets */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <TopNCard
          title="Most Expensive"
          agents={topByCost}
          valueKey="cost_24h_usd"
          valueFormat={(v) => `$${v.toFixed(2)}`}
          icon={DollarSign}
          color="bg-amber-500/10 text-amber-600"
        />
        <TopNCard
          title="Most Errors"
          agents={topByErrors}
          valueKey="error_rate_pct"
          valueFormat={(v) => `${v.toFixed(1)}%`}
          icon={AlertTriangle}
          color="bg-red-500/10 text-red-600"
        />
        <TopNCard
          title="Highest Latency"
          agents={topByLatency}
          valueKey="avg_latency_ms"
          valueFormat={(v) => `${v}ms`}
          icon={Activity}
          color="bg-orange-500/10 text-orange-600"
        />
        <TopNCard
          title="Most Invocations"
          agents={topByInvocations}
          valueKey="invocations_24h"
          valueFormat={(v) => v.toLocaleString()}
          icon={Zap}
          color="bg-blue-500/10 text-blue-600"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Event Stream */}
        <div className="rounded-lg border border-border bg-card p-5">
          <h2 className="mb-4 text-sm font-medium">Recent Events</h2>
          {loading ? (
            <div className="py-8 text-center text-sm text-muted-foreground">Loading...</div>
          ) : events.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">No events</div>
          ) : (
            <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
              {events.map((evt) => (
                <div key={evt.id} className="flex items-start gap-3 rounded-md border border-border/50 p-3">
                  <span
                    className={cn(
                      "mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase",
                      severityColor(evt.severity)
                    )}
                  >
                    {evt.severity}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-medium">{evt.agent_name}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground">{evt.message}</div>
                    <div className="mt-1 text-[10px] text-muted-foreground/60">
                      {new Date(evt.timestamp).toLocaleString()}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Team Comparison */}
        <div className="rounded-lg border border-border bg-card p-5">
          <h2 className="mb-4 text-sm font-medium">Team Comparison</h2>
          {loading ? (
            <div className="py-8 text-center text-sm text-muted-foreground">Loading...</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs text-muted-foreground">
                    <th className="pb-2 font-medium">Team</th>
                    <th className="pb-2 text-right font-medium">Agents</th>
                    <th className="pb-2 text-right font-medium">Cost 24h</th>
                    <th className="pb-2 text-right font-medium">Health</th>
                    <th className="pb-2 text-right font-medium">Incidents</th>
                  </tr>
                </thead>
                <tbody>
                  {teams.map((t) => (
                    <tr key={t.team} className="border-b border-border/50 last:border-0">
                      <td className="py-2.5 font-medium capitalize">{t.team}</td>
                      <td className="py-2.5 text-right font-mono text-xs">{t.agent_count}</td>
                      <td className="py-2.5 text-right font-mono text-xs">${t.total_cost_24h.toFixed(2)}</td>
                      <td className="py-2.5 text-right">
                        <span
                          className={cn(
                            "inline-block rounded px-1.5 py-0.5 font-mono text-xs font-medium",
                            t.avg_health_score >= 90
                              ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                              : t.avg_health_score >= 70
                              ? "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400"
                              : "bg-red-500/10 text-red-700 dark:text-red-400"
                          )}
                        >
                          {t.avg_health_score}
                        </span>
                      </td>
                      <td className="py-2.5 text-right">
                        {t.incidents_open > 0 ? (
                          <span className="inline-flex items-center gap-1 font-mono text-xs text-red-600 dark:text-red-400">
                            <AlertTriangle className="size-3" />
                            {t.incidents_open}
                          </span>
                        ) : (
                          <span className="font-mono text-xs text-muted-foreground">0</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
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
                  <th className="pb-2 text-right font-medium">Invocations</th>
                  <th className="pb-2 text-right font-medium">Error Rate</th>
                  <th className="pb-2 text-right font-medium">Latency</th>
                  <th className="pb-2 text-right font-medium">Cost 24h</th>
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
                    <td className="py-2.5 text-right font-mono text-xs">{agent.invocations_24h.toLocaleString()}</td>
                    <td className="py-2.5 text-right font-mono text-xs">{agent.error_rate_pct}%</td>
                    <td className="py-2.5 text-right font-mono text-xs">{agent.avg_latency_ms}ms</td>
                    <td className="py-2.5 text-right font-mono text-xs">${agent.cost_24h_usd.toFixed(2)}</td>
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
