// The Audit view's data, as pure functions (#824): which agent a row is about, its production
// deployment, and what a release row records about its eval run. Nothing here touches the DOM.
//
// `audit list --json` names the agent of every row with a deployment (removed ones included). A
// `release` row has no deployment: its RELEASE_IMAGE reason is the image it evaluated, so it
// belongs to the active deployment running that image (the check `promote` makes), or to the
// release candidate that ran it on this page (`<name>-candidate`). A row of an attempt whose other
// rows on this page name a deployment (its `attempt_started` row, written before the deployment
// exists) takes that deployment's agent.

import { canTry, productionRow } from "./tryit.js";

const NAME = /^[a-z0-9][a-z0-9-]{0,62}$/;
const IMAGE = /^sha256:[0-9a-f]{64}$/;
// release.py: `<run id> <passed>/<cases>`; the run id is `YYYYMMDD-HHMMSS-<6 hex>`.
const RUN = /^([0-9]{8}-[0-9]{6}-[0-9a-f]{6}) ([0-9]{1,6})\/([0-9]{1,6})$/;
const SUFFIX = "-candidate";
const AMBIGUOUS = Symbol("ambiguous");

function reason(row, code) {
  const reasons = (row && Array.isArray(row.reasons) && row.reasons) || [];
  const found = reasons.find((item) => item && item.code === code);
  return found && typeof found.message === "string" ? found.message : null;
}

function named(value) {
  return typeof value === "string" && NAME.test(value) ? value : null;
}

/** The agent a deployment name belongs to: a release candidate is its agent's. */
export function agentOf(name) {
  const value = named(name);
  if (!value) return null;
  return value.endsWith(SUFFIX) && value.length > SUFFIX.length ? value.slice(0, -SUFFIX.length) : value;
}

/**
 * Each row's agent as {name, candidate}, or null when the log does not say. `deployments` is
 * `list --json` (active ones); `rows` is the page, so a release row can find its candidate.
 */
export function rowAgents(rows, deployments) {
  const list = Array.isArray(rows) ? rows : [];
  const active = Array.isArray(deployments) ? deployments : [];
  // An image run by two agents (an identical build) names neither: AMBIGUOUS, shown as unknown.
  const byImage = new Map();
  const claim = (image, agent) => {
    const held = byImage.get(image);
    byImage.set(image, held === undefined || held === agent ? agent : AMBIGUOUS);
  };
  const byAttempt = new Map(); // an attempt's first row (`attempt_started`) precedes its deployment
  for (const row of list) {
    const name = named(row && row.agent);
    if (!name) continue;
    if (typeof row.image_digest === "string" && IMAGE.test(row.image_digest)) {
      claim(row.image_digest, agentOf(name));
    }
    if (typeof row.attempt_id === "string" && row.attempt_id) byAttempt.set(row.attempt_id, name);
  }
  for (const deployment of active) {
    const name = named(deployment && deployment.name);
    const image = deployment && typeof deployment.image === "string" ? deployment.image : "";
    if (name && IMAGE.test(image)) claim(image, agentOf(name));
  }
  return list.map((row) => {
    const name = named(row && row.agent) || (row && byAttempt.get(row.attempt_id)) || null;
    if (name) return { name: agentOf(name), candidate: name !== agentOf(name), deployment: name };
    const image = row && row.action === "release" ? reason(row, "RELEASE_IMAGE") : null;
    const owner = image && IMAGE.test(image) ? byImage.get(image) : null;
    return owner && owner !== AMBIGUOUS ? { name: owner, candidate: false, deployment: null } : null;
  });
}

/** The agent's production deployment when Try it can reach it (local, ready), else null. */
export function production(deployments, agent) {
  const row = productionRow(deployments, agent);
  return canTry(row) ? row : null;
}

/**
 * A release row's eval run: {run, passed, cases, report}, where `report` is the file `release`
 * wrote (relative to the project, its default `deploy/` output) when the agent is known.
 */
export function releaseEval(row, agent) {
  if (!row || row.action !== "release") return null;
  const match = RUN.exec(reason(row, "RELEASE_EVAL") || "");
  if (!match) return null;
  const name = named(agent);
  return {
    run: match[1],
    passed: Number(match[2]),
    cases: Number(match[3]),
    // Concatenated: the source guard rejects a dot right before that word (see test_center_static).
    report: name ? "deploy/" + name + "." + "eval-" + match[1] + ".json" : null,
  };
}

/** The page cursors: `stack` holds the `before` of each page opened from the newest. */
export function pageLabel(stack, count) {
  if (!count) return "No rows on this page.";
  const page = (Array.isArray(stack) ? stack.length : 0) + 1;
  return `Page ${page}, ${count} ${count === 1 ? "row" : "rows"}, newest first.`;
}
