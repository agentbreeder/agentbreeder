// Agents (#823, M7): the list across projects and an agent's gate strip. Pure functions: nothing
// here touches the DOM or the network.
//
// Rows come from `list --all --json` (each with `stack`, `project` and the console's own
// `console_project`); releases from the project's audit chain (`center/agents.py` `releases`).

import { isCandidate } from "./format.js";
import { href } from "./router.js";

const CANDIDATE = "-candidate";

/** The agent a row belongs to: a candidate `x-candidate` is agent `x`'s. */
export function agentName(row) {
  const name = row && typeof row.name === "string" ? row.name : "";
  return name.endsWith(CANDIDATE) ? name.slice(0, -CANDIDATE.length) : name;
}

/**
 * List rows sorted for reading: by agent, production before its candidates, then by target. Each
 * gets `agent` and `candidate`.
 */
export function listRows(deployments) {
  if (!Array.isArray(deployments)) return [];
  return deployments
    .filter((row) => row && typeof row.name === "string" && typeof row.id === "string")
    .map((row) => ({ ...row, agent: agentName(row), candidate: isCandidate(row) }))
    .sort((a, b) => a.agent.localeCompare(b.agent) || Number(a.candidate) - Number(b.candidate) || String(a.target).localeCompare(String(b.target)));
}

/** Where a row's project stands in this console: "registered", "not registered" or "default state". */
export function projectStatus(row) {
  if (row && row.console_project) return "registered";
  if (row && row.stack == null) return "default state";
  return "not registered";
}

/** The identity check of a row in words: `compliant`, `noncompliant: CODE`, … or "not checked". */
export function identityText(compliance) {
  if (typeof compliance !== "string" || !compliance) return "not checked";
  if (compliance === "unbound") return "unbound (no principal)";
  const [state, ...codes] = compliance.split(":");
  return codes.length ? `${state}: ${codes.join(", ")}` : state;
}

/** "passed", "failed" or "unknown" for a compliance value. */
export function identityTone(compliance) {
  if (compliance === "compliant") return "passed";
  if (typeof compliance === "string" && compliance.startsWith("noncompliant")) return "failed";
  return "unknown";
}

/** An image reference shortened for a table: the last path segment, the digest cut to 12. */
export function shortImage(image) {
  if (typeof image !== "string" || !image) return "";
  const last = image.split("/").pop();
  const at = last.indexOf("@sha256:");
  return at >= 0 ? `${last.slice(0, at)}@${last.slice(at + 8, at + 20)}` : last;
}

const ROLLBACKS = new Set(["rolled_back", "rollback_failed"]);

/** A release entry in words: "passed 5/5", "eval failed 3/5", "failed (CODE)", "rolled back". */
export function releaseText(release) {
  if (!release) return "not released";
  const score = release.score ? ` ${release.score.passed}/${release.score.cases}` : "";
  const code = release.problem && release.problem.code ? ` (${release.problem.code})` : "";
  switch (release.decision) {
    case "promoted":
      return `passed${score}`;
    case "eval_failed":
      return `eval failed${score}`;
    case "incomplete":
      return "no outcome recorded";
    case "rolled_back":
      return "rolled back";
    case "rollback_failed":
      return "rollback failed";
    default:
      return `failed${score}${code}`;
  }
}

/** "passed", "failed" or "neutral": the tone of a release entry's words. */
export function releaseTone(release) {
  if (!release) return "neutral";
  if (release.decision === "promoted" || release.decision === "rolled_back") return "passed";
  if (release.decision === "incomplete") return "neutral";
  return "failed";
}

/** The newest gate run (not a rollback) among release entries (newest first), or null. */
export function latestRun(releases) {
  return (Array.isArray(releases) && releases.find((entry) => entry && !ROLLBACKS.has(entry.decision))) || null;
}

/**
 * The image production should be running according to the audit log: the newest `promoted` or
 * `rolled_back` entry's (entries newest first), or null. A failed or unfinished run changes nothing.
 */
export function releasedImage(releases) {
  const entry = Array.isArray(releases) && releases.find((e) => e && (e.decision === "promoted" || e.decision === "rolled_back"));
  return (entry && entry.image) || null;
}

/**
 * What the Releases view says about agent `name`: its entries, or none when the project's
 * `agent.yaml` is another agent (release rows name no agent, so they are only the project's own
 * agent's). `{entries, own, truncated}`.
 */
export function releasesFor(data, name) {
  const entries = data && Array.isArray(data.releases) ? data.releases : [];
  const own = Boolean(data) && data.agent === name;
  return { entries: own ? entries : [], own, truncated: Boolean(data && data.truncated) };
}

/** The Last release cell: the words, their tone, and the time, for a list row. */
export function lastReleaseCell(row, view) {
  if (!row || row.candidate) return { text: "—", tone: "neutral" };
  if (!row.console_project) return { text: "unknown", tone: "neutral", title: "Releases are read for registered projects" };
  if (!view) return { text: "…", tone: "neutral" };
  if (view.error) return { text: "unavailable", tone: "neutral" };
  const { entries, own, truncated } = releasesFor(view.data, row.name);
  if (!own) return { text: "unknown", tone: "neutral", title: "Its project's agent.yaml is another agent" };
  const latest = entries[0];
  if (!latest) return { text: truncated ? "none in the newest audit rows" : "not released", tone: "neutral" };
  return { text: releaseText(latest), tone: releaseTone(latest), at: latest.at };
}

/** The candidate row of `name`, if one is running now (a release is in progress or was cut). */
export function candidateOf(rows, name) {
  if (!Array.isArray(rows)) return null;
  return rows.find((row) => row && row.name === `${name}${CANDIDATE}`) || null;
}

/**
 * The gate strip (spec §8.5): Build → Candidate → Evals n/N → Identity → Production. Each step is
 * {key, label, state: "passed" | "failed" | "running" | "differs" | "none", detail}. Build,
 * Candidate and Evals come from the newest gate run (`latestRun`); Identity and Production from
 * the production deployment. An agent deployed without `release` shows its first three steps as
 * "none"; production running another image than `released` (the newest promoted or rolled-back
 * image, `releasedImage`), for example after a plain deploy, shows "differs".
 */
export function gateStrip({ run = null, released = null, production = null, candidate = null } = {}) {
  const none = { state: "none", detail: "No release recorded" };
  const build = !run ? none : run.image ? { state: "passed", detail: run.image } : { state: "none", detail: "No image recorded" };
  let candidateStep;
  if (candidate) candidateStep = { state: "running", detail: `Running now (${candidate.state})` };
  else if (!run) candidateStep = none;
  else if (run.decision === "incomplete") candidateStep = { state: "none", detail: "No outcome recorded: the candidate failed to start, the run stopped, or it is still running" };
  else candidateStep = { state: "passed", detail: "Ran and was removed" };
  let evals;
  if (!run) evals = { label: "Evals", ...none };
  else if (run.score) {
    const { passed, cases } = run.score;
    const needs = typeof run.threshold === "number" ? `, needs ${Math.round(run.threshold * 100)}%` : "";
    evals = { label: `Evals ${passed}/${cases}`, state: run.decision === "eval_failed" ? "failed" : "passed", detail: `Run ${run.score.run}${needs}` };
  } else if (run.decision === "failed") {
    evals = { label: "Evals", state: "failed", detail: `The eval run failed${run.problem && run.problem.code ? ` (${run.problem.code})` : ""}` };
  } else evals = { label: "Evals", state: "none", detail: "No eval result recorded" };
  const tone = production ? identityTone(production.compliance) : "unknown";
  const identity = production
    ? { state: tone === "passed" ? "passed" : tone === "failed" ? "failed" : "none", detail: identityText(production.compliance) }
    : { state: "none", detail: "No production deployment" };
  let live;
  if (!production) live = { state: "none", detail: "Not deployed" };
  else if (production.state === "failed") live = { state: "failed", detail: `${production.target}, failed` };
  else if (production.state !== "ready") live = { state: "running", detail: `${production.target}, ${production.state}` };
  else if (released && production.image && production.image !== released) live = { state: "differs", detail: `${production.target}, ready, running ${production.image}, not the image the last release or rollback put there` };
  else live = { state: "passed", detail: `${production.target}, ready` };
  if (run && run.decision === "failed" && run.score) live = { ...live, detail: `${live.detail}; the last release failed after its evals passed; see Releases` };
  return [
    { key: "build", label: "Build", ...build },
    { key: "candidate", label: "Candidate", ...candidateStep },
    { key: "evals", ...evals },
    { key: "identity", label: "Identity", ...identity },
    { key: "production", label: "Production", ...live },
  ];
}

/** Today's date where the browser is (YYYY-MM-DD), for comparing with a review date. */
export function localDate(now = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** A step's state in words, for screen readers and the title (colour is never the only cue). */
export const STEP_WORDS = { passed: "passed", failed: "failed", running: "in progress", differs: "differs from the last release", none: "not run" };

/** The detail tabs: [key, label]. Overview is the agent's own address; the others add the key. */
export const TABS = [
  ["overview", "Overview"],
  ["try", "Try it"],
  ["releases", "Releases"],
  ["logs", "Logs"],
];

/** A tab's hash: `#/agents/NAME` for Overview, `#/agents/NAME/KEY` for the rest. */
export function tabHref(name, key) {
  return key === "overview" ? href("/agents/:name", { name }) : href("/agents/:name/:tab", { name, tab: key });
}
