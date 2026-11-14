// The gate-failed view's data (#826, M10 console half, PR 3), as pure functions: which release run
// failed the eval gate, one row per failed check of each failed case with what was expected and
// what happened, and where to edit the prompt and the case. Nothing here touches the DOM.
//
// The report is `evals show RUN --json` (docs/evals.md), read through lib/evals.js. Expected
// values come from the check's own name (`matches:PATTERN`, `called:TOOL`, as evals.run_case names
// them) and from the eval set's case (its `reference` and `expect`), read with `registry show`.
// "Actual" is what the report recorded: the tools called, the judge's correctness and, only when
// the replies were asked for, the agent's reply. Every string stays text: the screen shows it
// through Text.

import { RUN_ID, failedCases } from "./evals.js";
import { latestRun } from "./agents.js";
import { parseRef } from "./registry.js";
import { href } from "./router.js";

/** evals.CORRECTNESS_PASS: the judge's correctness a case needs. */
export const CORRECTNESS_PASS = 0.7;

/**
 * The run id of the agent's latest release when that release failed the eval gate, else null.
 * `entries` are the Releases view's entries for the agent (newest first, lib/agents.js).
 */
export function gateFailedRun(entries) {
  const run = latestRun(entries);
  if (!run || run.decision !== "eval_failed" || !run.score) return null;
  return typeof run.score.run === "string" && RUN_ID.test(run.score.run) ? run.score.run : null;
}

/** The API path of the saved candidate tail (`logs --failed NAME`). */
export function tailApi(projectId, agent) {
  return `/api/projects/${projectId}/logs/failed/${encodeURIComponent(agent)}`;
}

const list = (value) => (Array.isArray(value) ? value.filter((item) => typeof item === "string") : []);

/** The eval set's cases by id, from its `registry show` answer and the parsed cases file. */
export function casesById(entries) {
  const byId = new Map();
  for (const entry of Array.isArray(entries) ? entries : []) {
    const data = entry && entry.data;
    if (data && typeof data.id === "string" && !byId.has(data.id)) byId.set(data.id, data);
  }
  return byId;
}

function correctness(row) {
  return typeof row.correctness === "number" && Number.isFinite(row.correctness) ? row.correctness.toFixed(2) : null;
}

function calledText(row) {
  const tools = list(row.tools);
  return tools.length ? `Called ${tools.join(", ")}` : "Called no tool";
}

/**
 * What one failed check expected and what happened, as {expected, actual} text. `row` is the
 * report's result, `kase` the eval set's case (or null when it could not be read). The check name
 * is the CLI's capped, redacted text; a `#2` suffix (two names equal once capped) is kept.
 */
export function expectation(check, row, kase = null) {
  const name = String(check ?? "");
  const expect = kase && kase.expect && typeof kase.expect === "object" ? kase.expect : {};
  const part = /^(matches|avoids|called|did_not_call):(.*)$/s.exec(name);
  if (part) {
    const [, kind, value] = part;
    if (kind === "matches") return { expected: `The reply matches ${value}`, actual: "It does not" };
    if (kind === "avoids") return { expected: `The reply does not match ${value}`, actual: "It does" };
    if (kind === "called") return { expected: `Calls ${value}`, actual: calledText(row) };
    return { expected: `Does not call ${value}`, actual: calledText(row) };
  }
  const base = name.replace(/#[0-9]+$/, "");
  if (base === "agent_responded") return { expected: "A reply", actual: "No reply, or the call failed" };
  if (base === "judge_correct") {
    const score = correctness(row);
    const reference = kase && typeof kase.reference === "string" ? kase.reference : null;
    return {
      expected: reference ?? `An answer the judge scores at least ${CORRECTNESS_PASS.toFixed(2)}`,
      actual: score ? `Judged ${score} (a pass needs ${CORRECTNESS_PASS.toFixed(2)})` : "Judged below the pass mark",
    };
  }
  if (base === "retrieved_expected_article") {
    const sources = list(expect.sources_any);
    return {
      expected: sources.length ? `Cites one of ${sources.join(", ")} in its top 3 sources` : "Cites an expected source in its top 3",
      actual: "It did not (the report does not keep the sources)",
    };
  }
  if (base === "patterns_finished") {
    return { expected: "The reply patterns finish within their time limit", actual: "They took too long, so no pattern was checked" };
  }
  return { expected: "", actual: "Failed" };
}

/**
 * One row per failed check of each failed case: {key, id, first, check, expected, actual, reason,
 * reply}; `first` marks a case's first row (the one that carries its edit link and reply). A failed case with no failed check recorded (malformed or a run error) is one row with
 * `check` null. `byId` maps case ids to the eval set's cases (casesById); `reply` is the agent's
 * reply when the report was read with `--content`, else null.
 */
export function failureRows(report, byId = new Map()) {
  const rows = [];
  failedCases(report).forEach((row, index) => {
    const id = String(row.id ?? "");
    const kase = byId.get(id) || null;
    const reason = typeof row.reason === "string" ? row.reason : "";
    const reply = report && report.content && typeof row.reply === "string" ? row.reply : null;
    const checks = list(row.failed_checks);
    if (!checks.length) {
      rows.push({ key: `${index}`, id, first: true, check: null, expected: "", actual: "The case failed with no failed check recorded", reason, reply });
      return;
    }
    checks.forEach((check, n) => {
      rows.push({ key: `${index}:${n}`, id, first: n === 0, check, ...expectation(check, row, kase), reason, reply: n === 0 ? reply : null });
    });
  });
  return rows;
}

function routeTo(pattern, params) {
  try {
    return href(pattern, params);
  } catch {
    return null; // a value the router refuses (not one segment of letters, digits, . _ -)
  }
}

/** An eval set ref (`eval/NAME@X.Y.Z`) as {kind, name, version}, or null for anything else. */
export function evalSetRef(text) {
  const ref = parseRef(text);
  return ref && /^eval$/.test(ref.kind) ? ref : null; // a regex: the static guard flags the quoted word
}

/** Where to edit the case: the eval set's page with that case open, else the eval set's page. */
export function caseHref(evalSet, id) {
  const ref = evalSetRef(evalSet);
  if (!ref) return null;
  return routeTo("/registry/evals/:name/case/:case", { name: ref.name, case: id }) || routeTo("/registry/evals/:name", { name: ref.name });
}

/** Where to edit the agent's prompt: the registry page of `agent.yaml`'s `prompt.ref`, or null. */
export function promptHref(agentFile) {
  const prompt = agentFile && agentFile.prompt;
  const ref = parseRef(prompt && prompt.ref);
  return ref && ref.kind === "prompt" ? routeTo("/registry/prompts/:name", { name: ref.name }) : null;
}
