import { useState, useEffect } from "react";
import { CheckCircle, XCircle, AlertCircle, Download, FileText } from "lucide-react";
import { cn } from "@/lib/utils";

const API = "/api/v1/agentops";

interface Control {
  id: string;
  name: string;
  category: string;
  status: "pass" | "fail" | "partial" | "skipped";
  last_checked: string;
  evidence?: Record<string, unknown>;
  details?: string;
}

interface ComplianceStatus {
  overall_status: "compliant" | "partial" | "non_compliant";
  controls_total: number;
  controls_passed: number;
  controls_failed: number;
  controls_partial: number;
  controls_skipped?: number;
  last_checked: string;
  scan_id?: string;
  controls: Control[];
}

interface Report {
  report_id: string;
  generated_at: string;
  format: string;
  controls_passed: number;
  controls_failed: number;
  controls_total: number;
  evidence: { control_id: string; control_name: string; category: string; status: string; details: string }[];
}

function ControlStatusIcon({ status }: { status: string }) {
  if (status === "pass") return <CheckCircle className="size-4 text-emerald-500" />;
  if (status === "fail") return <XCircle className="size-4 text-red-500" />;
  return <AlertCircle className="size-4 text-yellow-500" />;
}

function ControlStatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pass: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
    fail: "bg-red-500/15 text-red-700 dark:text-red-400",
    partial: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
  };
  return (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-[10px] font-semibold capitalize",
        colors[status] ?? "bg-muted text-muted-foreground"
      )}
    >
      {status}
    </span>
  );
}

export default function CompliancePage() {
  const [status, setStatus] = useState<ComplianceStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [reportLoading, setReportLoading] = useState(false);
  const [lastReport, setLastReport] = useState<Report | null>(null);
  const [showReportModal, setShowReportModal] = useState(false);
  const [reportFormat, setReportFormat] = useState<"json" | "csv" | "pdf">("json");

  useEffect(() => {
    fetch(`${API}/compliance/status`)
      .then((r) => r.json())
      .then((json) => setStatus(json.data))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  async function generateReport() {
    setReportLoading(true);
    try {
      const res = await fetch(`${API}/compliance/report?format=${reportFormat}`);
      const json = await res.json();
      setLastReport(json.data);
      setShowReportModal(false);
    } catch (err) {
      console.error("Report generation failed:", err);
    } finally {
      setReportLoading(false);
    }
  }

  // Group controls by category
  const grouped: Record<string, Control[]> = {};
  for (const c of status?.controls ?? []) {
    if (!grouped[c.category]) grouped[c.category] = [];
    grouped[c.category].push(c);
  }

  const overallColors: Record<string, string> = {
    compliant: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30",
    partial: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30",
    non_compliant: "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
  };

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Compliance</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            SOC2 controls status and audit evidence export
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowReportModal(true)}
            className="flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-2 text-xs font-medium hover:bg-accent"
          >
            <FileText className="size-3.5" />
            Schedule Report
          </button>
          <button
            onClick={() => { setReportFormat("json"); generateReport(); }}
            disabled={reportLoading}
            className="flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-2 text-xs font-medium hover:bg-accent disabled:opacity-50"
          >
            <Download className="size-3.5" />
            Export JSON
          </button>
          <button
            onClick={() => { setReportFormat("csv"); generateReport(); }}
            disabled={reportLoading}
            className="flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-2 text-xs font-medium hover:bg-accent disabled:opacity-50"
          >
            <Download className="size-3.5" />
            Export CSV
          </button>
        </div>
      </div>

      {/* Overall Status */}
      {!loading && status && (
        <div className="grid gap-4 sm:grid-cols-4">
          <div
            className={cn(
              "rounded-lg border p-5",
              overallColors[status.overall_status] ?? "border-border bg-card"
            )}
          >
            <div className="text-xs font-medium uppercase tracking-wider opacity-70">
              Overall Status
            </div>
            <div className="mt-2 text-xl font-semibold capitalize">
              {status.overall_status.replace("_", " ")}
            </div>
            <div className="mt-1 text-xs opacity-70">
              Last checked {new Date(status.last_checked).toLocaleString()}
            </div>
          </div>
          <div className="rounded-lg border border-border bg-card p-5">
            <div className="text-xs text-muted-foreground">Controls Passed</div>
            <div className="mt-2 text-2xl font-semibold text-emerald-600 dark:text-emerald-400">
              {status.controls_passed}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">of {status.controls_total} total</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-5">
            <div className="text-xs text-muted-foreground">Partial Controls</div>
            <div className="mt-2 text-2xl font-semibold text-yellow-600 dark:text-yellow-400">
              {status.controls_partial}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">needs attention</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-5">
            <div className="text-xs text-muted-foreground">Failed Controls</div>
            <div className="mt-2 text-2xl font-semibold text-red-600 dark:text-red-400">
              {status.controls_failed}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">requires remediation</div>
          </div>
        </div>
      )}

      {/* Controls by Category */}
      {loading ? (
        <div className="rounded-lg border border-border bg-card py-16 text-center text-sm text-muted-foreground">
          Loading compliance status...
        </div>
      ) : (
        <div className="space-y-4">
          {Object.entries(grouped).map(([category, controls]) => {
            const passed = controls.filter((c) => c.status === "pass").length;
            const failed = controls.filter((c) => c.status === "fail").length;
            return (
              <div key={category} className="rounded-lg border border-border bg-card">
                <div className="flex items-center justify-between border-b border-border px-5 py-3">
                  <div className="flex items-center gap-3">
                    <h3 className="text-sm font-medium">{category}</h3>
                    <span className="text-xs text-muted-foreground">
                      {passed}/{controls.length} passed
                    </span>
                  </div>
                  {failed > 0 && (
                    <span className="rounded bg-red-500/10 px-2 py-0.5 text-[10px] font-semibold text-red-600 dark:text-red-400">
                      {failed} failed
                    </span>
                  )}
                </div>
                <div className="divide-y divide-border/50">
                  {controls.map((c) => (
                    <div key={c.id} className="flex items-center gap-3 px-5 py-3">
                      <ControlStatusIcon status={c.status} />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm">{c.name}</div>
                        <div className="text-[10px] text-muted-foreground">
                          Last checked {new Date(c.last_checked).toLocaleString()}
                        </div>
                      </div>
                      <ControlStatusBadge status={c.status} />
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Last Report Summary */}
      {lastReport && (
        <div className="rounded-lg border border-border bg-card p-5">
          <h3 className="mb-3 text-sm font-medium">Last Generated Report</h3>
          <div className="grid gap-4 text-sm sm:grid-cols-3">
            <div>
              <div className="text-xs text-muted-foreground">Report ID</div>
              <div className="mt-0.5 font-mono text-xs">{lastReport.report_id}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Generated</div>
              <div className="mt-0.5 text-xs">{new Date(lastReport.generated_at).toLocaleString()}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Results</div>
              <div className="mt-0.5 text-xs">
                <span className="text-emerald-600 dark:text-emerald-400">{lastReport.controls_passed} passed</span>
                {" · "}
                <span className="text-red-600 dark:text-red-400">{lastReport.controls_failed} failed</span>
                {" · "}
                {lastReport.controls_total} total
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Schedule Report Modal */}
      {showReportModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-xl border border-border bg-card p-6 shadow-2xl">
            <h3 className="mb-4 text-base font-semibold">Generate Compliance Report</h3>
            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Format</label>
                <select
                  value={reportFormat}
                  onChange={(e) => setReportFormat(e.target.value as "json" | "csv" | "pdf")}
                  className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-foreground"
                >
                  <option value="json">JSON</option>
                  <option value="csv">CSV</option>
                  <option value="pdf">PDF</option>
                </select>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button
                  onClick={() => setShowReportModal(false)}
                  className="rounded-md border border-border px-4 py-2 text-xs font-medium hover:bg-accent"
                >
                  Cancel
                </button>
                <button
                  onClick={generateReport}
                  disabled={reportLoading}
                  className="rounded-md bg-foreground px-4 py-2 text-xs font-medium text-background hover:bg-foreground/90 disabled:opacity-50"
                >
                  {reportLoading ? "Generating..." : "Generate"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
