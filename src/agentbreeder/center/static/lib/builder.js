// Build a new agent (#833, M17), as pure functions: the form to `new agent --from-spec`'s JSON
// spec (docs/commands/new.md), why it cannot be previewed or created yet, and what the preview's
// combination means. The browser never builds YAML: the preview shows the files the server's dry
// run returns. Nothing here touches the DOM.

import { caseProblems, parseCases, pyJson } from "./cases.js";
import { starterNameProblem } from "./guide.js";
import { PROVIDERS, VERIFIED } from "./onboard.js";

export const SPEC_VERSION = 2; // `runtime` is a version 2 field (`agentspec.V2_FIELDS`)
export const MAX_PROMPT = 32 * 1024; // bytes, `agentspec.MAX_PROMPT`
export const MAX_CASES = 100; // `agentspec.MAX_CASES`
export const MAX_TURNS = 10;
export const MAX_TEXT = 4000; // a turn or a reference, `agentspec.MAX_TEXT`
export const MAX_SPEC = 256 * 1024; // bytes of JSON, `agentspec.MAX_SPEC_BYTES`
export const MAX_TOOLS = 50; // `agentspec.Spec.tools`
export const MAX_SERVERS = 10; // `agentspec.Spec.mcp`
export const MAX_SERVER_TOOLS = 100; // `agentspec.Mcp.tools`
const CASE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/; // `agentspec.CASE_ID`
const MODEL_ID = /^[A-Za-z0-9._:/-]{1,128}$/; // `newagent.MODEL_ID_PATTERN`
// The checks a spec case takes (`agentspec.Expect`); a new agent has no knowledge to cite.
export const SPEC_CHECKS = ["must_match", "must_not_match", "tools_include", "tools_exclude"];
// The parts every spec has; `combination.parts` lists more when tools or MCP servers are picked.
export const BASE_PARTS = ["model", "prompt", "evals"];
// The framework a new form starts on (owner decision C1, #1085): the runtime's registry name,
// fixed, with no "last used" state. Its display name comes from the runtime, never from here.
export const DEFAULT_RUNTIME = "adk";

/** The first case of a new form: a working example to edit. */
export const FIRST_CASE = {
  id: "hello",
  turns: ["Hello! What can you help me with?"],
  expect: {},
  reference: "A short, friendly reply that says what the agent helps with.",
};

export const initialForm = () => ({
  name: "",
  model: "gemini",
  modelId: "",
  prompt: "",
  maxCalls: "2",
  threshold: "1",
  retries: "1",
  runtime: null, // the picked `runtime/` ref; set from `parts.runtimes` once it is read
  tools: {}, // tool name -> the picked `tool/` ref
  mcp: {}, // server name -> {ref, tools: [granted tool names]}
});

// -- the framework (#1085 S1, console spec §4.1) -------------------------------------------------
// `parts.runtimes` is the engine's resolved view of every `runtime/` version (`registry runtimes
// --json`, allow-listed by the server): a usable entry is `{ref, name, version, root_kind,
// display_name, framework, protocol, profile, capabilities, limits, reserved_tool_names, verified,
// notes?, conformance?: {passed}}`; one the CLI cannot use is `{ref, usable: false, code, reason}`.
// No framework's name, note or limit is written in this file.

const newestFirst = (a, b) => {
  const left = a.version.split(".").map(Number);
  const right = b.version.split(".").map(Number);
  for (let index = 0; index < 3; index += 1) if (left[index] !== right[index]) return right[index] - left[index];
  return 0;
};

/**
 * The frameworks offered, one per runtime name: `[{name, versions: [entry]}]`, newest version
 * first. Frameworks with a version run end to end come first, then by name.
 */
export function frameworks(runtimes) {
  const groups = new Map();
  for (const entry of runtimes || []) {
    if (!entry.usable) continue;
    if (!groups.has(entry.name)) groups.set(entry.name, { name: entry.name, versions: [] });
    groups.get(entry.name).versions.push(entry);
  }
  const listed = [...groups.values()];
  for (const group of listed) group.versions.sort(newestFirst);
  const run = (group) => group.versions.some((entry) => entry.verified);
  return listed.sort((a, b) => Number(run(b)) - Number(run(a)) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

/** The runtime versions the CLI cannot use, listed as text and never as a disabled control. */
export const unusableRuntimes = (runtimes) => (runtimes || []).filter((entry) => !entry.usable);

export const unusableText = (entry) => `Cannot be used: ${entry.ref}: ${entry.reason} (${entry.code})`;

/** The usable runtime version with this ref, or null. */
export const runtimeOf = (runtimes, ref) => (runtimes || []).find((entry) => entry.usable && entry.ref === ref) || null;

/** "LangGraph 1.0.0": the framework's own name (C8) and the version. */
export const runtimeLabel = (entry) => `${entry.display_name} ${entry.version}`;

/** The ref a new form starts on: the newest version of the default framework, or null. */
export function defaultRuntime(runtimes) {
  const group = frameworks(runtimes).find((item) => item.name === DEFAULT_RUNTIME);
  return group ? group.versions[0].ref : null;
}

/**
 * The status of a runtime version, in the console's words (four values, §4.1): run end to end,
 * else what its newest conformance report says, else not run.
 */
export function runtimeStatus(entry) {
  if (entry.verified) return "Run end to end (Northwind)";
  if (entry.conformance) return entry.conformance.passed ? "Conformance passed; not run end to end" : "Conformance failed; not run end to end";
  return "Not run end to end";
}

/** What a framework or version change announces, once: "LangGraph 1.0.0: Not run end to end". */
export const runtimeAnnouncement = (entry) => `${runtimeLabel(entry)}: ${runtimeStatus(entry)}`;

const limitOf = (runtime, part) => {
  const value = runtime && runtime.limits ? runtime.limits[part] : undefined;
  return Number.isInteger(value) ? value : Infinity;
};

export const hasCapability = (runtime, name) => Boolean(runtime && (runtime.capabilities || []).includes(name));

/** The function name of tool version `entry` that `runtime` reserves, or null. */
export function reservedName(entry, runtime) {
  const reserved = new Set((runtime && runtime.reserved_tool_names) || []);
  return ((entry && entry.functions) || []).find((name) => reserved.has(name)) || null;
}

/** Why tool version `entry` cannot be picked on `runtime`, as a sentence, or null. */
export function toolBlocked(entry, runtime) {
  const name = reservedName(entry, runtime);
  return name ? `${runtimeLabel(runtime)} reserves the tool name ${name}.` : null;
}

/** Whether `runtime` takes no more tools than the form has picked. */
export const toolsFull = (form, runtime) => Object.keys(form.tools || {}).length >= limitOf(runtime, "tools");

/**
 * The picks that do not work with `runtime` (the chosen version), each with its reason:
 * `[{kind: "tool" | "mcp", name, ref, reason}]`. A framework or version switch never drops a
 * pick silently (§4.2): it stays on the form with this reason and a Remove control, and
 * switching back clears it, because this is computed from the form and never stored.
 *  - an MCP server on a runtime without the `mcp` capability, or past `limits.mcp_servers`;
 *  - a tool past `limits.tools` (in name order, as the spec lists them, not counting a tool
 *    with a reserved name);
 *  - a tool with a function name the runtime reserves.
 */
export function invalidPicks(form, runtime, parts) {
  if (!runtime) return [];
  const label = runtimeLabel(runtime);
  const found = [];
  const entries = new Map(((parts && parts.tools) || []).map((entry) => [entry.ref, entry]));
  const most = limitOf(runtime, "tools");
  let taken = 0; // a tool with a reserved name takes none of the limit: it has its own reason
  Object.keys(form.tools || {}).sort().forEach((name) => {
    const ref = form.tools[name];
    const blocked = toolBlocked(entries.get(ref), runtime);
    if (blocked) found.push({ kind: "tool", name, ref, reason: blocked });
    else if ((taken += 1) > most) found.push({ kind: "tool", name, ref, reason: `${label} takes at most ${most} tool${most === 1 ? "" : "s"}.` });
  });
  const servers = limitOf(runtime, "mcp_servers");
  Object.keys(form.mcp || {}).sort().forEach((name, index) => {
    const ref = form.mcp[name].ref;
    if (!hasCapability(runtime, "mcp")) found.push({ kind: "mcp", name, ref, reason: `${label} has no MCP client.` });
    else if (index >= servers) found.push({ kind: "mcp", name, ref, reason: `${label} takes at most ${servers} MCP server${servers === 1 ? "" : "s"}.` });
  });
  return found;
}

/** "2 picks do not work with LangGraph 1.0.0." (one message for all of them), or null. */
export function invalidText(invalid, runtime) {
  if (!invalid.length || !runtime) return null;
  return `${invalid.length} pick${invalid.length === 1 ? " does" : "s do"} not work with ${runtimeLabel(runtime)}.`;
}

/** Preview's disabled reason while a pick does not fit the chosen framework, or null. */
export function invalidProblem(invalid, runtime) {
  const text = invalidText(invalid, runtime);
  return text ? `${text} Remove ${invalid.length === 1 ? "it" : "them"} or choose another framework.` : null;
}

/** The form on the newest version of framework `name` (each framework starts there). */
export function pickFramework(form, name, runtimes) {
  const group = frameworks(runtimes).find((item) => item.name === name);
  return group ? { ...form, runtime: group.versions[0].ref } : form;
}

/** The form without pick `{kind, name}` (an invalid pick's Remove control). */
export function removePick(form, pick) {
  const key = pick.kind === "mcp" ? "mcp" : "tools";
  const next = { ...form[key] };
  delete next[pick.name];
  return { ...form, [key]: next };
}

// -- the pickers -------------------------------------------------------------------------------
// `parts` is `GET /api/builder/parts`: `{tools: [entry], mcp: [entry + {tools, unusable}]}`, each
// entry `{ref, name, version, root_kind, runs_code, description}`, newest version of a name first.

/** The versions offered, grouped by name in the server's order: `[{name, versions: [entry]}]`. */
export function pickGroups(entries) {
  const groups = [];
  for (const entry of entries || []) {
    const last = groups[groups.length - 1];
    if (last && last.name === entry.name) last.versions.push(entry);
    else groups.push({ name: entry.name, versions: [entry] });
  }
  return groups;
}

/** The form with tool `name` set to `ref` ("" removes it). */
export function pickTool(form, name, ref) {
  const tools = { ...form.tools };
  if (ref) tools[name] = ref;
  else delete tools[name];
  return { ...form, tools };
}

/**
 * The form with MCP server `name` set to version `ref` ("" removes it). A newly picked server
 * grants none of its tools; a version change keeps the grants the new version still declares.
 */
export function pickServer(form, name, ref, entries) {
  const mcp = { ...form.mcp };
  if (!ref) {
    delete mcp[name];
    return { ...form, mcp };
  }
  const entry = (entries || []).find((item) => item.ref === ref);
  const declared = new Set(((entry && entry.tools) || []).map((tool) => tool.name));
  const kept = ((mcp[name] && mcp[name].tools) || []).filter((tool) => declared.has(tool));
  mcp[name] = { ref, tools: kept };
  return { ...form, mcp };
}

/** The form with `tool` of MCP server `name` granted or not, in the version's declared order. */
export function grantTool(form, name, tool, granted, entries) {
  const current = form.mcp[name];
  if (!current) return form;
  const entry = (entries || []).find((item) => item.ref === current.ref);
  const order = ((entry && entry.tools) || []).map((item) => item.name);
  const wanted = new Set(current.tools);
  if (granted) wanted.add(tool);
  else wanted.delete(tool);
  const tools = order.filter((item) => wanted.has(item));
  return { ...form, mcp: { ...form.mcp, [name]: { ...current, tools } } };
}

/**
 * The form without picks `parts` no longer offers (a version gone, or one that cannot be picked
 * now) and without grants of tools the picked version no longer declares; a runtime that is gone
 * or cannot be used now, and a form with none yet, gets the default framework's newest version.
 * The same form object when nothing changed. This is for registry changes only: a pick that does
 * not fit the chosen framework stays, with a reason (`invalidPicks`).
 */
export function keepOffered(form, parts) {
  const tools = new Set((parts.tools || []).map((entry) => entry.ref));
  const servers = new Map((parts.mcp || []).filter((entry) => !entry.unusable).map((entry) => [entry.ref, entry]));
  const runtime = runtimeOf(parts.runtimes, form.runtime) ? form.runtime : defaultRuntime(parts.runtimes);
  let changed = runtime !== (form.runtime ?? null);
  const nextTools = {};
  for (const [name, ref] of Object.entries(form.tools || {})) {
    if (tools.has(ref)) nextTools[name] = ref;
    else changed = true;
  }
  const nextServers = {};
  for (const [name, server] of Object.entries(form.mcp || {})) {
    const entry = servers.get(server.ref);
    if (!entry) {
      changed = true;
      continue;
    }
    const declared = new Set(entry.tools.map((tool) => tool.name));
    const granted = server.tools.filter((tool) => declared.has(tool));
    if (granted.length !== server.tools.length) changed = true;
    nextServers[name] = granted.length === server.tools.length ? server : { ...server, tools: granted };
  }
  return changed ? { ...form, runtime, tools: nextTools, mcp: nextServers } : form;
}

/** Why the picks cannot be previewed, as a sentence, or null. */
export function picksProblem(form) {
  const tools = Object.keys(form.tools || {});
  const servers = Object.entries(form.mcp || {});
  if (tools.length > MAX_TOOLS) return `Pick at most ${MAX_TOOLS} tools.`;
  if (servers.length > MAX_SERVERS) return `Pick at most ${MAX_SERVERS} MCP servers.`;
  const bare = servers.find(([, server]) => !server.tools.length);
  if (bare) return `Grant ${bare[0]} at least one of its tools, or set it to Not used.`;
  const many = servers.find(([, server]) => server.tools.length > MAX_SERVER_TOOLS);
  if (many) return `Grant ${many[0]} at most ${MAX_SERVER_TOOLS} tools.`;
  return null;
}

export const initialCases = () => `${pyJson(FIRST_CASE)}\n`;

const bytes = (text) => new TextEncoder().encode(text).length;

const whole = (text, low, high) => {
  const trimmed = String(text).trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return value >= low && value <= high ? value : null;
};

const fraction = (text) => {
  const trimmed = String(text).trim();
  if (!/^(0|1|0?\.\d+|1\.0*)$/.test(trimmed)) return null;
  return Number(trimmed);
};

/** The cases of the editor's text, each as a spec case, with why one cannot be (per case). */
export function specCases(casesText) {
  const parsed = parseCases(casesText);
  const cases = parsed.entries.filter((entry) => entry.data).map((entry) => entry.data);
  const problems = cases.map((data) => {
    const found = [];
    const id = typeof data.id === "string" ? data.id : String(data.id ?? "");
    if (id.trim() && !CASE_ID.test(id)) found.push("an id is letters, digits and . _ - (starting with a letter or digit, at most 64)");
    if (Array.isArray(data.turns) && data.turns.some((turn) => typeof turn !== "string")) found.push("a turn is not text");
    if (Array.isArray(data.turns) && data.turns.length > MAX_TURNS) found.push(`more than ${MAX_TURNS} turns`);
    const expect = data.expect && typeof data.expect === "object" ? data.expect : {};
    if (Object.keys(expect).some((key) => !SPEC_CHECKS.includes(key))) found.push("cited sources need knowledge, which a new agent does not have yet");
    const texts = [...(Array.isArray(data.turns) ? data.turns : []), data.reference];
    if (texts.some((text) => typeof text === "string" && text.length > MAX_TEXT)) found.push(`a turn or the reference is over ${MAX_TEXT} characters`);
    return found;
  });
  const spec = cases.map((data) => {
    const item = { id: typeof data.id === "string" ? data.id : String(data.id ?? "") };
    item.turns = Array.isArray(data.turns) ? data.turns : [];
    item.expect = data.expect && typeof data.expect === "object" ? data.expect : {};
    item.reference = typeof data.reference === "string" ? data.reference : "";
    return item;
  });
  return { cases: spec, problems, entries: parsed.entries };
}

/**
 * Why the form cannot be previewed yet, as a sentence, or null. `invalid` is `invalidProblem`'s
 * sentence for picks that do not fit the chosen framework.
 */
export function formProblem(form, casesText, invalid = null) {
  const nameProblem = starterNameProblem(form.name.trim());
  if (nameProblem) return nameProblem === "Type a name first." ? "Type the agent's name first." : nameProblem;
  if (!form.runtime) return "Choose a framework first.";
  if (invalid) return invalid;
  if (!PROVIDERS.includes(form.model)) return "Pick a model provider first.";
  if (form.modelId.trim() && !MODEL_ID.test(form.modelId.trim())) return "A model id is letters, digits and . _ : / -, at most 128 characters.";
  if (!form.prompt.trim()) return "Write the agent's prompt first.";
  if (bytes(form.prompt) > MAX_PROMPT) return `The prompt is over ${MAX_PROMPT / 1024} KiB.`;
  if (whole(form.maxCalls, 1, 20) === null) return "Model calls per turn is a whole number from 1 to 20.";
  if (fraction(form.threshold) === null) return "The pass threshold is a number from 0 to 1.";
  if (whole(form.retries, 0, 3) === null) return "Retries is a whole number from 0 to 3.";
  const picks = picksProblem(form);
  if (picks) return picks;
  const { cases, problems, entries } = specCases(casesText);
  if (!cases.length) return "Add at least one eval case.";
  if (cases.length > MAX_CASES) return `A new agent starts with at most ${MAX_CASES} eval cases.`;
  const loading = caseProblems(entries);
  if (loading.count) return `Fix the eval cases first (${loading.count} to fix).`;
  const index = problems.findIndex((found) => found.length);
  if (index >= 0) return `Eval case ${index + 1}: ${problems[index][0]}.`;
  if (bytes(JSON.stringify(buildSpec(form, casesText))) > MAX_SPEC) return `The agent is over ${MAX_SPEC / 1024} KiB; shorten the prompt or the cases.`;
  return null;
}

/** The spec for `new agent --from-spec` (call only when `formProblem` is null). */
export function buildSpec(form, casesText) {
  const modelId = form.modelId.trim();
  return {
    schema_version: SPEC_VERSION,
    name: form.name.trim(),
    runtime: form.runtime,
    model: form.model,
    model_id: modelId || null,
    max_model_calls: whole(form.maxCalls, 1, 20),
    prompt: { text: form.prompt.replace(/\r\n?/g, "\n") },
    evals: { cases: specCases(casesText).cases, threshold: fraction(form.threshold), retries: whole(form.retries, 0, 3) },
    tools: Object.keys(form.tools || {}).sort().map((name) => form.tools[name]),
    knowledge: [],
    mcp: Object.keys(form.mcp || {}).sort().map((name) => ({ ref: form.mcp[name].ref, tools: [...form.mcp[name].tools] })),
  };
}

export const previewBody = (spec) => ({ spec });

/** Create's body: the spec that was previewed (not the form as it is now) and its digest. */
export function createBody(spec, preview, codeConfirmed) {
  return { spec, digest: preview.plan_sha256, code_confirmed: codeConfirmed === true };
}

const PART_WORDS = { tools: "tools", mcp: "MCP servers", knowledge: "knowledge" };

/** "LangGraph 1.0.0 with MCP servers", for the parts beyond the model, prompt and evals; or null. */
function withParts(preview, label) {
  const extra = preview.combination.parts.filter((part) => !BASE_PARTS.includes(part));
  return extra.length ? `${label || "a new agent"} with ${extra.map((part) => PART_WORDS[part] || part).join(" and ")}` : null;
}

/**
 * What has not been run end to end, as a sentence, or null when the combination is verified:
 * the provider (unless it is one of `verified`), and the framework (`label`, `runtimeLabel` of
 * the previewed runtime) with the parts beyond the model, prompt and evals. A combination is
 * keyed by the runtime (`agentspec.VERIFIED`), so the same parts can be run on one framework or
 * version and not on another.
 */
export function unverifiedText(preview, verified = VERIFIED, label = null) {
  if (!preview || preview.combination.verified) return null;
  const what = [];
  if (!verified.includes(preview.model)) what.push(`the ${preview.model} provider`);
  const parts = withParts(preview, label);
  if (parts) what.push(parts);
  const named = what.length ? what.join(", and ") : label ? `a new agent on ${label}` : "this combination";
  return `Not run end to end yet: ${named}. The console creates only combinations that have been; \`agentbreeder new agent --from-spec\` writes it anyway.`;
}

/** Why Create is off, or null. `label` is the previewed runtime's (`runtimeLabel`). */
export function createProblem(preview, stale, codeConfirmed, label = null, verified = VERIFIED) {
  if (!preview) return "Preview first.";
  if (stale) return "The form changed: preview again.";
  if (!preview.combination.verified) {
    const parts = label ? withParts(preview, label) : null;
    if (parts) return `${parts} has not been run end to end.`;
    // The provider and the parts are run ones, so it is this framework version that is not.
    if (label && verified.includes(preview.model)) return `A new agent on ${label} has not been run end to end.`;
    return "This combination has not been run end to end.";
  }
  if (preview.runs_code && !codeConfirmed) return "Confirm that this code runs in agents first.";
  return null;
}
