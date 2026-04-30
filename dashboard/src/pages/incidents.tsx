import { useState, useEffect } from "react";
import { ChevronDown, ChevronRight, RefreshCw, RotateCcw, TrendingDown, Power } from "lucide-react";
import { cn } from "@/lib/utils";

const API = "/api/v1/agentops";

interface TimelineEntry {
  timestamp: string;
  actor: string;
  message: string;
}

interface Incident {
  id: string;
  agent_name: string;
  title: string;
  severity: "critical" | "high" | "medium" | "low";
  status: "open" | "investigating" | "mitigated" | "resolved";
  description: string;
  created_at: string;
  updated_at: string;
  timeline: TimelineEntry[];
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
  high: "bg-orange-500/15 text-orange-700 dark:text-orange-400 border-orange-500/30",
  medium: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30",
  low: "bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30",
};

const STATUS_COLORS: Record<string, string> = {
  open: "bg-red-500/15 text-red-700 dark:text-red-400",
  investigating: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  mitigated: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
  resolved: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
};

const ACTIONS = [
  { id: "restart", label: "Restart", icon: RefreshCw, color: "text-blue-600 dark:text-blue-400 hover:bg-blue-500/10" },
  { id: "rollback", label: "Rollback", icon: RotateCcw, color: "text-orange-600 dark:text-orange-400 hover:bg-orange-500/10" },
  { id: "scale", label: "Scale Up", icon: TrendingDown, color: "text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/10" },
  { id: "disable", label: "Disable", icon: Power, color: "text-red-600 dark:text-red-400 hover:bg-red-500/10" },
];

function SeverityBadge({ severity }: { severity: string }) {
  return (
    <span
      className={cn(
        "rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase",
        SEVERITY_COLORS[severity] ?? "bg-muted text-muted-foreground border-border"
      )}
    >
      {severity}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-[10px] font-semibold capitalize",
        STATUS_COLORS[status] ?? "bg-muted text-muted-foreground"
      )}
    >
      {status}
    </span>
  );
}

export default function IncidentsPage() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [filterSeverity, setFilterSeverity] = useState<string>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  async function fetchIncidents() {
    try {
      const params = new URLSearchParams();
      if (filterStatus !== "all") params.set("status", filterStatus);
      if (filterSeverity !== "all") params.set("severity", filterSeverity);
      const res = await fetch(`${API}/incidents?${params}`);
      const json = await res.json();
      setIncidents(json.data ?? []);
    } catch (err) {
      console.error("Failed to fetch incidents:", err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    fetchIncidents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterStatus, filterSeverity]);

  async function executeAction(incidentId: string, action: string) {
    setActionLoading(`${incidentId}-${action}`);
    try {
      await fetch(`${API}/incidents/${incidentId}/actions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      await fetchIncidents();
    } catch (err) {
      console.error("Action failed:", err);
    } finally {
      setActionLoading(null);
    }
  }

  async function updateStatus(incidentId: string, status: string) {
    try {
      await fetch(`${API}/incidents/${incidentId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      await fetchIncidents();
    } catch (err) {
      console.error("Update failed:", err);
    }
  }

  const STATUS_OPTIONS = ["all", "open", "investigating", "mitigated", "resolved"];
  const SEVERITY_OPTIONS = ["all", "critical", "high", "medium", "low"];

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Incidents</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage agent incidents — track, investigate, and resolve operational issues
        </p>
      </div>

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label className="text-xs text-muted-foreground">Status:</label>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="rounded-md border border-border bg-card px-2 py-1 text-xs outline-none focus:border-foreground"
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s === "all" ? "All Statuses" : s.charAt(0).toUpperCase() + s.slice(1)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-muted-foreground">Severity:</label>
          <select
            value={filterSeverity}
            onChange={(e) => setFilterSeverity(e.target.value)}
            className="rounded-md border border-border bg-card px-2 py-1 text-xs outline-none focus:border-foreground"
          >
            {SEVERITY_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s === "all" ? "All Severities" : s.charAt(0).toUpperCase() + s.slice(1)}
              </option>
            ))}
          </select>
        </div>
        <div className="ml-auto text-xs text-muted-foreground">
          {incidents.length} incident{incidents.length !== 1 ? "s" : ""}
        </div>
      </div>

      {/* Incident List */}
      {loading ? (
        <div className="rounded-lg border border-border bg-card py-16 text-center text-sm text-muted-foreground">
          Loading incidents...
        </div>
      ) : incidents.length === 0 ? (
        <div className="rounded-lg border border-border bg-card py-16 text-center text-sm text-muted-foreground">
          No incidents found
        </div>
      ) : (
        <div className="space-y-3">
          {incidents.map((inc) => {
            const isExpanded = expandedId === inc.id;
            return (
              <div
                key={inc.id}
                className={cn(
                  "rounded-lg border bg-card transition-colors",
                  inc.severity === "critical" ? "border-red-500/40" : "border-border"
                )}
              >
                {/* Row */}
                <button
                  className="flex w-full items-start gap-3 p-4 text-left"
                  onClick={() => setExpandedId(isExpanded ? null : inc.id)}
                >
                  <span className="mt-0.5 text-muted-foreground">
                    {isExpanded ? (
                      <ChevronDown className="size-4" />
                    ) : (
                      <ChevronRight className="size-4" />
                    )}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <SeverityBadge severity={inc.severity} />
                      <StatusBadge status={inc.status} />
                      <span className="text-sm font-medium">{inc.title}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                      <span className="font-medium text-foreground/70">{inc.agent_name}</span>
                      <span>Created {new Date(inc.created_at).toLocaleString()}</span>
                      <span>Updated {new Date(inc.updated_at).toLocaleString()}</span>
                    </div>
                  </div>
                </button>

                {/* Expanded Detail */}
                {isExpanded && (
                  <div className="border-t border-border px-4 pb-5 pt-4">
                    <p className="text-sm text-muted-foreground">{inc.description}</p>

                    {/* Action Buttons */}
                    <div className="mt-4 flex flex-wrap gap-2">
                      {ACTIONS.map((action) => {
                        const Icon = action.icon;
                        const isLoading = actionLoading === `${inc.id}-${action.id}`;
                        return (
                          <button
                            key={action.id}
                            onClick={() => executeAction(inc.id, action.id)}
                            disabled={isLoading}
                            className={cn(
                              "flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50",
                              action.color
                            )}
                          >
                            <Icon className={cn("size-3.5", isLoading && "animate-spin")} />
                            {action.label}
                          </button>
                        );
                      })}

                      {/* Status transitions */}
                      {inc.status !== "resolved" && (
                        <div className="ml-auto flex items-center gap-2">
                          <span className="text-xs text-muted-foreground">Move to:</span>
                          {inc.status === "open" && (
                            <button
                              onClick={() => updateStatus(inc.id, "investigating")}
                              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-accent"
                            >
                              Investigating
                            </button>
                          )}
                          {inc.status === "investigating" && (
                            <button
                              onClick={() => updateStatus(inc.id, "mitigated")}
                              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-accent"
                            >
                              Mitigated
                            </button>
                          )}
                          {(inc.status === "mitigated" || inc.status === "investigating") && (
                            <button
                              onClick={() => updateStatus(inc.id, "resolved")}
                              className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-500/20 dark:text-emerald-400"
                            >
                              Resolved
                            </button>
                          )}
                        </div>
                      )}
                    </div>

                    {/* Timeline */}
                    <div className="mt-5">
                      <h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        Timeline
                      </h4>
                      <div className="space-y-3">
                        {inc.timeline.map((entry, i) => (
                          <div key={i} className="flex items-start gap-3">
                            <div className="mt-1.5 size-1.5 shrink-0 rounded-full bg-foreground/40" />
                            <div>
                              <div className="flex items-center gap-2 text-xs">
                                <span className="font-medium">{entry.actor}</span>
                                <span className="text-muted-foreground">
                                  {new Date(entry.timestamp).toLocaleString()}
                                </span>
                              </div>
                              <div className="mt-0.5 text-xs text-muted-foreground">
                                {entry.message}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
