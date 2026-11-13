// Pure wording for the console's screens: the walkthrough fixes (#811) live here so they are
// tested without a browser. Every function takes server data and returns plain strings or
// small objects; nothing here touches the DOM.

import { visible } from "./text.js";

/** `audit verify --json` in words (#811 item 3). */
export function verifySentence(report) {
  if (!report || typeof report !== "object") return { ok: null, text: "Not verified yet." };
  const rows = Number(report.rows) || 0;
  const head = typeof report.head === "string" ? report.head.slice(0, 12) : "";
  const noun = rows === 1 ? "row" : "rows";
  if (report.ok) {
    return { ok: true, text: `Chain intact: ${rows} ${noun} checked${head ? `, head ${head}…` : "."}` };
  }
  const where = report.first_invalid_seq != null ? ` at row ${report.first_invalid_seq}` : "";
  const why = report.problem ? `: ${report.problem}` : "";
  return { ok: false, text: `Chain broken${where}${why}. ${rows} ${noun} before it check out.` };
}

/** Incomplete attempts in words, or null when there are none. */
export function incompleteSentence(report) {
  const attempts = (report && Array.isArray(report.incomplete_attempts) && report.incomplete_attempts) || [];
  if (!attempts.length) return null;
  const ids = attempts.map((id) => String(id).slice(0, 8)).join(", ");
  return `${attempts.length} ${attempts.length === 1 ? "attempt has" : "attempts have"} no final row: ${ids}.`;
}

/** An audit row's time (`at`, ISO 8601 UTC) as local date and time; the raw value if unparsable. */
export function auditTime(at, locale) {
  if (!at) return "";
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return String(at);
  return date.toLocaleString(locale, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Reason codes of an audit row, joined. */
export function reasonCodes(row) {
  const reasons = (row && Array.isArray(row.reasons) && row.reasons) || [];
  return reasons.map((reason) => (reason && reason.code) || "").filter(Boolean).join(", ");
}

/** A release candidate: temporary, removed when its release ends (#811 item 2). */
export function isCandidate(row) {
  return Boolean(row && typeof row.name === "string" && row.name.endsWith("-candidate"));
}

/** A service's host ports in words; a service with none is "internal only" (#811 item 9). */
export function portsText(info) {
  const ports = Object.entries((info && info.host_ports) || {});
  if (!ports.length) return "internal only";
  return ports.map(([name, port]) => `${name} → 127.0.0.1:${port}`).join(", ");
}

/** A service's state with its health. */
export function serviceState(info) {
  if (!info) return "";
  return `${info.state}${info.health ? ` (${info.health})` : ""}`;
}

/** Measured costs are only Northwind's: other projects get no figure (#811 walkthrough). */
export function isNorthwind(project) {
  return Boolean(project && project.name === "northwind" && /\/agentbreeder-projects\/northwind$/.test(project.path || ""));
}

const JOB_LABELS = {
  "services-up": "Start services",
  "services-down": "Stop services",
  build: "Build",
  release: "Release",
  rollback: "Roll back",
  remove: "Remove",
  reconcile: "Re-verify",
  "cluster-up": "Connect cluster",
  "cluster-down": "Delete cluster",
  promote: "Deploy to cluster",
};

/** A job kind as a label. Unknown kinds show as themselves, with controls escaped (S-35). */
export function jobLabel(kind) {
  return JOB_LABELS[kind] || visible(kind || "job");
}

/**
 * A finished job's one-line result: {tone: "ok"|"warn"|"error", text}. Running jobs give null.
 * Exit 5 is a failed eval gate for release and promote.
 */
export function jobOutcome(job) {
  if (!job || job.state === "running") return null;
  const label = jobLabel(job.kind);
  if (job.state === "succeeded") return { tone: "ok", text: `${label} finished.` };
  if (job.state === "waiting_approval") {
    return { tone: "warn", text: `${label} is waiting for a second person's approval. The log shows the command; it cannot be approved here, because this session is one person.` };
  }
  if (job.state === "interrupted") {
    return { tone: "warn", text: `${label} was interrupted. Check Deployments for a leftover candidate and remove it.` };
  }
  if (job.kind === "release") {
    return { tone: "error", text: job.exit_code === 5 ? "The eval gate failed. Production is unchanged." : "The release failed. See its log." };
  }
  if (job.kind === "promote") {
    return { tone: "error", text: job.exit_code === 5 ? "Cluster evals failed. The log says what was restored." : "Deploy to cluster failed. See its log." };
  }
  return { tone: "error", text: `${label} failed${job.exit_code != null ? ` (exit ${job.exit_code})` : ""}. See its log.` };
}

/** The install line for a missing cluster tool, with the restart `up` needs (#811 item 5). */
export function toolGuidance(name, tool) {
  const install = tool && tool.install ? ` Install it: ${tool.install}.` : "";
  return `${name} is not on the console's PATH.${install} Then stop and restart agentbreeder up: the console reads PATH when it starts.`;
}
