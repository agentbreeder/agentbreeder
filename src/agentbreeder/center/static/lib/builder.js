// Build a new agent (#833, M17), as pure functions: the form to `new agent --from-spec`'s JSON
// spec (docs/commands/new.md), why it cannot be previewed or created yet, and what the preview's
// combination means. The browser never builds YAML: the preview shows the files the server's dry
// run returns. Nothing here touches the DOM.

import { caseProblems, parseCases, pyJson } from "./cases.js";
import { starterNameProblem } from "./guide.js";
import { PROVIDERS } from "./onboard.js";

export const SPEC_VERSION = 1;
export const MAX_PROMPT = 32 * 1024; // bytes, `agentspec.MAX_PROMPT`
export const MAX_CASES = 100; // `agentspec.MAX_CASES`
export const MAX_TURNS = 10;
export const MAX_TEXT = 4000; // a turn or a reference, `agentspec.MAX_TEXT`
export const MAX_SPEC = 256 * 1024; // bytes of JSON, `agentspec.MAX_SPEC_BYTES`
const CASE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/; // `agentspec.CASE_ID`
const MODEL_ID = /^[A-Za-z0-9._:/-]{1,128}$/; // `newagent.MODEL_ID_PATTERN`
// The checks a spec case takes (`agentspec.Expect`); a new agent has no knowledge to cite.
export const SPEC_CHECKS = ["must_match", "must_not_match", "tools_include", "tools_exclude"];
// The parts every spec has; `combination.parts` lists more when tools or MCP servers are picked.
export const BASE_PARTS = ["model", "prompt", "evals"];

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
});

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

/** Why the form cannot be previewed yet, as a sentence, or null. */
export function formProblem(form, casesText) {
  const nameProblem = starterNameProblem(form.name.trim());
  if (nameProblem) return nameProblem === "Type a name first." ? "Type the agent's name first." : nameProblem;
  if (!PROVIDERS.includes(form.model)) return "Pick a model provider first.";
  if (form.model === "openrouter" && !form.modelId.trim()) return "OpenRouter needs a model id, such as openrouter/<vendor>/<model>.";
  if (form.modelId.trim() && !MODEL_ID.test(form.modelId.trim())) return "A model id is letters, digits and . _ : / -, at most 128 characters.";
  if (!form.prompt.trim()) return "Write the agent's prompt first.";
  if (bytes(form.prompt) > MAX_PROMPT) return `The prompt is over ${MAX_PROMPT / 1024} KiB.`;
  if (whole(form.maxCalls, 1, 20) === null) return "Model calls per turn is a whole number from 1 to 20.";
  if (fraction(form.threshold) === null) return "The pass threshold is a number from 0 to 1.";
  if (whole(form.retries, 0, 3) === null) return "Retries is a whole number from 0 to 3.";
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
    model: form.model,
    model_id: modelId || null,
    max_model_calls: whole(form.maxCalls, 1, 20),
    prompt: { text: form.prompt.replace(/\r\n?/g, "\n") },
    evals: { cases: specCases(casesText).cases, threshold: fraction(form.threshold), retries: whole(form.retries, 0, 3) },
    tools: [],
    knowledge: [],
    mcp: [],
  };
}

export const previewBody = (spec) => ({ spec });

/** Create's body: the spec that was previewed (not the form as it is now) and its digest. */
export function createBody(spec, preview, codeConfirmed) {
  return { spec, digest: preview.plan_sha256, code_confirmed: codeConfirmed === true };
}

/**
 * What has not been run end to end, as a sentence, or null when the combination is verified:
 * the provider (only gemini is), and the parts beyond the model, prompt and evals.
 */
export function unverifiedText(preview, verifiedProvider = "gemini") {
  if (!preview || preview.combination.verified) return null;
  const extra = preview.combination.parts.filter((part) => !BASE_PARTS.includes(part));
  const what = [];
  if (preview.model !== verifiedProvider) what.push(`the ${preview.model} provider`);
  if (extra.length) what.push(`a new agent with ${extra.join(" and ")}`);
  const named = what.length ? what.join(", and ") : "this combination";
  return `Not run end to end yet: ${named}. The console creates only combinations that have been; \`agentbreeder new agent --from-spec\` writes it anyway.`;
}

/** Why Create is off, or null. */
export function createProblem(preview, stale, codeConfirmed) {
  if (!preview) return "Preview first.";
  if (stale) return "The form changed: preview again.";
  if (!preview.combination.verified) return "This combination has not been run end to end.";
  if (preview.runs_code && !codeConfirmed) return "Confirm that this code runs in agents first.";
  return null;
}
