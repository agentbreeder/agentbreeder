// The Traces tab of an agent (#907): pure helpers. The list is `traces ID --last N --json` through
// GET /api/projects/:id/deployments/:deployment/traces (center/traces.py); one trace's span tree is
// the Try it trace route (lib/tryit.js `tracePath`).

import { latencyText, traceTokensText } from "./tryit.js";

const DEPLOYMENT_ID = /^[0-9a-f]{32}$/;

/** How many traces a read asks for (`--last`); the server allows 1 to 50. */
export const LAST_CHOICES = [10, 20, 50];
export const DEFAULT_LAST = 20;

/** The read path of a deployment's traces, or null for an id outside the rule or a bad count. */
export function tracesPath(projectId, deploymentId, last = DEFAULT_LAST) {
  if (typeof deploymentId !== "string" || !DEPLOYMENT_ID.test(deploymentId)) return null;
  if (!LAST_CHOICES.includes(last)) return null;
  return `/api/projects/${projectId}/deployments/${deploymentId}/traces?last=${last}`;
}

/** The deployment chosen first: production when there is one, else the first of `rows`. */
export function defaultDeployment(rows, production) {
  if (production && rows.some((row) => row.id === production.id)) return production.id;
  return rows.length ? rows[0].id : "";
}

function count(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}

/** A trace's start, in UTC to the second ("2026-11-14 03:10:33 UTC"), or "start unknown". */
export function startText(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(value)) return "start unknown";
  return `${value.slice(0, 10)} ${value.slice(11, 19)} UTC`;
}

/** One trace in a line after its start: status, latency, models, tokens and spans. */
export function traceLine(trace) {
  const parts = [trace.status === "error" ? "Error" : trace.status === "ok" ? "OK" : "Status unknown"];
  if (Number.isFinite(trace.latency_ms)) parts.push(latencyText(trace.latency_ms));
  const models = Array.isArray(trace.models) ? trace.models.filter((m) => typeof m === "string") : [];
  if (models.length) parts.push(`model${models.length === 1 ? "" : "s"} ${models.join(", ")}`);
  const tokens = traceTokensText(trace); // with the cached part (runtime/adk@1.4.0)
  if (tokens) parts.push(`${tokens} tokens`);
  const spans = count(trace.span_count) ?? 0;
  parts.push(`${spans} span${spans === 1 ? "" : "s"}${trace.incomplete ? " so far" : ""}${trace.spans_truncated ? " (Phoenix had more)" : ""}`);
  if (trace.incomplete) parts.push("still arriving");
  return parts.join(" · ");
}

/** The trace's tool calls ("lookup_order (error, 11 ms), refund"), or null when it made none. */
export function toolCallsText(trace) {
  const calls = Array.isArray(trace.tool_calls) ? trace.tool_calls.filter((call) => call && typeof call.name === "string") : [];
  if (!calls.length) return null;
  return calls
    .map((call) => {
      const facts = [];
      if (call.status === "error") facts.push("error");
      if (Number.isFinite(call.latency_ms)) facts.push(latencyText(call.latency_ms));
      return facts.length ? `${call.name} (${facts.join(", ")})` : call.name;
    })
    .join(", ");
}

/** How the list's traces were attributed, in words, or null when none were matched by time. */
export function windowText(answer) {
  const traces = Array.isArray(answer && answer.traces) ? answer.traces : [];
  if (!traces.some((trace) => trace.attribution === "window")) return null;
  const others = Array.isArray(answer.overlapping) ? answer.overlapping.length : 0;
  return others
    ? `Matched by time: this runtime does not record its deployment on its traces, and ${others} other deployment${others === 1 ? "" : "s"} of this agent ran at the same time, so some of these traces may be theirs.`
    : "Matched by time: this runtime does not record its deployment on its traces.";
}

/** How many listed traces Phoenix has only part of (no root span yet, #1010), in words, or null. */
export function arrivingText(answer) {
  const traces = Array.isArray(answer && answer.traces) ? answer.traces : [];
  const arriving = traces.filter((trace) => trace && trace.incomplete === true).length;
  if (!arriving) return null;
  return `${arriving} ${arriving === 1 ? "trace is" : "traces are"} still arriving: Phoenix does not have ${arriving === 1 ? "its" : "their"} root span yet, so ${arriving === 1 ? "its" : "their"} spans and tokens are partial and which deployment ran ${arriving === 1 ? "it" : "them"} is not known yet. Read again in a few seconds.`;
}

/** A failed list read, in words. */
export function tracesProblemText(problem) {
  const code = (problem && problem.code) || "TRACES_FAILED";
  const message = (problem && problem.message) || "The traces could not be read";
  if (code === "TRACES_NO_PHOENIX") return `This stack does not run Phoenix (service/phoenix), so there are no traces. [${code}]`;
  if (code === "TRACE_BUSY") return `Other trace reads are still running; read again when one ends. [${code}]`;
  if (code === "TRACES_UNREACHABLE") return `Phoenix did not answer; check that the stack's services are up. [${code}]`;
  return `${message} [${code}]`;
}
