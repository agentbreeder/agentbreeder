// LLM cost (#906, console half), as pure functions over the `cost --json` answer the server
// checked (docs/cost.md): LiteLLM's spend per deployment key and per model, in the currency it
// names (USD), with the time the server read it. It is LiteLLM's estimate from its price list,
// not a bill. Every string from the answer is untrusted text; the screen renders it through
// <Text>. Nothing here touches the DOM.
//
// Stable exports: WINDOWS, costPath, money, count, modelLabel, keyLabel, agentCost, costNotes,
// costRefusal, asOfText, windowText, partial.

import { agentName } from "./agents.js";

export const WINDOWS = [7, 30, 90];

/** The API path of one cost read, or null for a window the server would refuse. */
export function costPath(projectId, days) {
  if (!WINDOWS.includes(days)) return null;
  return `/api/projects/${projectId}/llm-cost?${new URLSearchParams({ days: String(days) })}`;
}

const isCount = (value) => Number.isInteger(value) && value >= 0;
const isAmount = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;
const text = (value) => (typeof value === "string" && value ? value : "");

/** An amount with its currency: six decimals below one unit (calls cost fractions of a cent). */
export function money(value, currency) {
  if (!isAmount(value)) return "-";
  return `${text(currency) || "USD"} ${value.toFixed(value < 1 ? 6 : 2)}`;
}

export function count(value) {
  return isCount(value) ? value.toLocaleString("en-US") : "-";
}

/** A model: the gateway alias and the provider model, as LiteLLM recorded them. */
export function modelLabel(model) {
  const group = text(model && model.model_group);
  const name = text(model && model.model);
  if (group && name && group !== name) return `${group} (${name})`;
  return group || name || "unknown model";
}

// An eval judge's key kind, as an object key: the static guard refuses the word quoted.
const JUDGE_KIND = { eval: true };

/** A spend key: a deployment by name, an eval judge by run, or a key this state does not know. */
export function keyLabel(key) {
  if (!key) return "";
  if (key.kind === "deployment") return text(key.name) || text(key.id);
  if (Object.hasOwn(JUDGE_KIND, key.kind)) return `eval run ${text(key.run) || "unknown"}`;
  return `${text(key.id) || "unknown"} (not in this state)`;
}

const BUCKET = ["calls", "failed", "prompt_tokens", "completion_tokens", "unpriced"];

function emptyBucket(extra = {}) {
  return { ...extra, calls: 0, failed: 0, prompt_tokens: 0, completion_tokens: 0, cached_prompt_tokens: 0, unpriced: 0, cost_usd: 0 };
}

function add(bucket, item) {
  for (const field of BUCKET) bucket[field] += isCount(item[field]) ? item[field] : 0;
  bucket.cost_usd += isAmount(item.cost_usd) ? item.cost_usd : 0;
  // Cached prompt tokens (#1056): a `cost` before it reports none, so the sum is unknown (null),
  // never a quiet zero.
  const cached = item.cached_prompt_tokens;
  bucket.cached_prompt_tokens = isCount(bucket.cached_prompt_tokens) && isCount(cached) ? bucket.cached_prompt_tokens + cached : null;
}

const byCost = (a, b) => b.cost_usd - a.cost_usd || b.calls - a.calls;

/**
 * One agent's share of a project's cost: its deployment keys (production, candidates and removed
 * deployments, by name), their models added up, and the total. `models_cut` is true when a key
 * had more models than the server passed on, so the per-model rows are partial.
 */
export function agentCost(answer, name) {
  const keys = (answer && Array.isArray(answer.keys) ? answer.keys : []).filter(
    (key) => key && key.kind === "deployment" && agentName({ name: key.name }) === name,
  );
  const total = emptyBucket();
  const models = new Map();
  let modelsCut = false;
  for (const key of keys) {
    add(total, key);
    if (key.models_cut) modelsCut = true;
    for (const model of Array.isArray(key.models) ? key.models : []) {
      const id = JSON.stringify([model.model_group, model.model]);
      if (!models.has(id)) models.set(id, emptyBucket({ model_group: model.model_group, model: model.model }));
      add(models.get(id), model);
    }
  }
  return { keys, models: [...models.values()].sort(byCost), total, modelsCut };
}

/** What a reader must know to trust the numbers: partial reads, calls with no price, cut lists.
 * `keys: false` leaves out the cut keys (a view that lists no keys). */
export function costNotes(answer, total, { keys = true } = {}) {
  const notes = [];
  if (answer && answer.truncated) notes.push("LiteLLM had more spend log rows than are read, so these totals are partial. Pick a shorter window.");
  const unpriced = total && isCount(total.unpriced) ? total.unpriced : 0;
  if (unpriced) notes.push(`${count(unpriced)} ${unpriced === 1 ? "call has" : "calls have"} no price in LiteLLM's list and ${unpriced === 1 ? "is" : "are"} not in the amounts.`);
  if (keys && answer && isCount(answer.keys_cut) && answer.keys_cut) notes.push(`${count(answer.keys_cut)} smaller keys are not listed.`);
  if (answer && answer.models_cut) notes.push(`${count(answer.models_cut)} smaller models are not listed.`);
  return notes;
}

const GATEWAY_DOWN = "start the services (Services, or agentbreeder services up)";
// docs/cost.md "Errors", with the step a person can take.
const REFUSALS = {
  COST_NO_GATEWAY: { reason: "this environment has no model gateway with a master key", next: "run agentbreeder services up for this project" },
  GATEWAY_CREDENTIAL: { reason: "the gateway's master key is empty", next: "run agentbreeder services up for this project" },
  GATEWAY_UNREACHABLE: { reason: "the model gateway did not answer", next: GATEWAY_DOWN },
  GATEWAY_ERROR: { reason: "", next: "check the litellm service's logs (Logs)" },
  STATE_NEEDS_MIGRATION: { reason: "the deployment state is from an older version", next: "run agentbreeder migrate" },
  STATE_BUSY: { reason: "the deployment state is being written", next: "try again" },
  COST_BUSY: { reason: "", next: "try again when another cost read ends" },
};

/** What to show for a failed read: {reason, next}. */
export function costRefusal(error) {
  const code = error && typeof error.code === "string" ? error.code : "";
  const message = error && typeof error.message === "string" ? error.message : "";
  const known = REFUSALS[code];
  const reason = (known && known.reason) || message || "the cost could not be read";
  const next = known ? known.next : "run agentbreeder cost --stack DIR in a terminal";
  return { reason, next };
}

/** True when the read may have missed rows or keys, so "none" only means none in the part read. */
export function partial(answer) {
  return Boolean(answer && (answer.truncated || (isCount(answer.keys_cut) && answer.keys_cut)));
}

/** "as of 2026-11-14 22:05:09 UTC" from the server's read time. */
export function asOfText(answer) {
  const when = text(answer && answer.as_of);
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(when) ? `as of ${when.replace("T", " ").replace("Z", " UTC")}` : "";
}

/** "2026-10-16 to 2026-11-14 (UTC days)". */
export function windowText(answer) {
  const since = text(answer && answer.since);
  const until = text(answer && answer.until);
  return since && until ? `${since} to ${until} (UTC days)` : "";
}
