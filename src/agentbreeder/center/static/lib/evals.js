// The eval report viewer's data, as pure functions (#932): where a report is read and linked,
// and what each case of `evals show --json` says (contract: docs/evals.md). Nothing here touches
// the DOM.
//
// Stable exports (lane B's M10 gate-failed view imports them; change only additively):
//   RUN_ID                         the run id rule, `YYYYMMDD-HHMMSS-<6 hex>`
//   isAgentName(value)             whether a value is an agent name (eval_reports.AGENT)
//   reportApi(projectId, run, {agent, content})   GET path of one report (`evals show RUN`);
//                                  it THROWS on a bad run id: check RUN_ID first during render
//   listApi(projectId, {agent})    GET path of the saved reports (`evals list`)
//   reportHref(run, agent)         the viewer's hash route, or null for a bad run or agent
//   caseState(row)                 "failed" | "flaky" | "passed"
//   orderedCases(report)           the results, failed first, then flaky, then passed
//   failedCases(report)            only the failed results
//   scoreText(report)              "1 of 2 cases passed (50%)"
//   allPassed(report)              true only when the saved header says every case passed
//   percent(rate)                  "50%", "97.5%", or "" when not a rate

import { href } from "./router.js";

export const RUN_ID = /^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$/;
// eval_reports.AGENT (agentfile.NAME_PATTERN).
const AGENT = /^[a-z0-9](?:[a-z0-9-]{0,51}[a-z0-9])?$/;
const ORDER = { failed: 0, flaky: 1, passed: 2 };

/** Whether `value` is an agent name (the CLI's rule). */
export function isAgentName(value) {
  return typeof value === "string" && AGENT.test(value);
}

function agentName(agent) {
  return isAgentName(agent) ? agent : null;
}

function query(params) {
  const parts = Object.entries(params)
    .filter(([, value]) => value)
    .map(([key, value]) => `${key}=${encodeURIComponent(value)}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** The API path of one run's report; a bad run id throws (it would never reach the CLI). */
export function reportApi(projectId, run, { agent = null, content = false } = {}) {
  if (!RUN_ID.test(String(run))) throw new Error("bad eval run id");
  return `/api/projects/${projectId}/evals/${run}${query({ agent: agentName(agent), content: content ? "1" : "" })}`;
}

/** The API path of the project's saved reports, newest first (at most 100). */
export function listApi(projectId, { agent = null } = {}) {
  return `/api/projects/${projectId}/evals${query({ agent: agentName(agent) })}`;
}

/** The viewer's route for a run (and its agent, when known), or null when either is not valid. */
export function reportHref(run, agent = null) {
  if (typeof run !== "string" || !RUN_ID.test(run)) return null;
  if (agent === null || agent === undefined) return href("/audit/evals/:run", { run });
  const name = agentName(agent);
  return name ? href("/audit/evals/:run/:agent", { run, agent: name }) : null;
}

/** A case's outcome: failed, flaky (passed after a retry) or passed. */
export function caseState(row) {
  if (!row || row.passed !== true) return "failed";
  return Number.isInteger(row.attempts) && row.attempts > 1 ? "flaky" : "passed";
}

function results(report) {
  return report && Array.isArray(report.results) ? report.results.filter((row) => row && typeof row === "object") : [];
}

/** The results, failed first, then flaky, then passed; each group keeps the report's order. */
export function orderedCases(report) {
  return results(report)
    .map((row, index) => ({ row, index }))
    .sort((a, b) => ORDER[caseState(a.row)] - ORDER[caseState(b.row)] || a.index - b.index)
    .map(({ row }) => row);
}

/** Only the failed results, in the report's order. */
export function failedCases(report) {
  return results(report).filter((row) => caseState(row) === "failed");
}

/** A rate in [0, 1] as a percent with at most one decimal; "" for anything else. */
export function percent(rate) {
  if (typeof rate !== "number" || !Number.isFinite(rate) || rate < 0 || rate > 1) return "";
  return `${Math.round(rate * 1000) / 10}%`;
}

/** The header's score in words, from the summary `release` saved (every case in the run). */
export function scoreText(report) {
  if (!report || !Number.isInteger(report.cases) || !Number.isInteger(report.passed)) return "Score not recorded";
  const rate = percent(report.pass_rate);
  const noun = report.cases === 1 ? "case" : "cases";
  return `${report.passed} of ${report.cases} ${noun} passed${rate ? ` (${rate})` : ""}`;
}

/**
 * Whether the header `release` saved says every case passed. The header counts every case in the
 * run, including any skipped as malformed, so the tone follows it and not the readable results.
 */
export function allPassed(report) {
  return Boolean(report) && Number.isInteger(report.cases) && Number.isInteger(report.passed) && report.cases > 0 && report.passed === report.cases && failedCases(report).length === 0;
}
