import assert from "node:assert/strict";
import { test } from "node:test";

import {
  FIRST_CASE, MAX_CASES, buildSpec, createBody, createProblem, formProblem, initialCases, initialForm, previewBody, specCases, unverifiedText,
} from "../../src/agentbreeder/center/static/lib/builder.js";
import { pyJson } from "../../src/agentbreeder/center/static/lib/cases.js";

const DIGEST = `sha256:${"a".repeat(64)}`;
const form = (changes = {}) => ({ ...initialForm(), name: "helper", prompt: "You are helper.\r\n", ...changes });
const casesOf = (...cases) => cases.map((data) => pyJson(data)).join("\n") + "\n";
const preview = (changes = {}) => ({
  model: "gemini", plan_sha256: DIGEST, runs_code: false,
  combination: { parts: ["model", "prompt", "evals"], verified: true }, ...changes,
});

test("a new form starts with one working case and needs a name and prompt", () => {
  assert.equal(initialCases(), `${pyJson(FIRST_CASE)}\n`);
  assert.equal(formProblem(initialForm(), initialCases()), "Type the agent's name first.");
  assert.equal(formProblem({ ...initialForm(), name: "helper" }, initialCases()), "Write the agent's prompt first.");
  assert.equal(formProblem(form(), initialCases()), null);
});

test("the form says what is wrong, in order", () => {
  const cases = initialCases();
  assert.match(formProblem(form({ name: "Helper" }), cases), /lowercase letter first/);
  assert.match(formProblem(form({ model: "mistral" }), cases), /provider/);
  assert.match(formProblem(form({ model: "openrouter" }), cases), /OpenRouter needs a model id/);
  assert.equal(formProblem(form({ model: "openrouter", modelId: "openrouter/a/b" }), cases), null);
  assert.match(formProblem(form({ prompt: "é".repeat(16 * 1024 + 1) }), cases), /over 32 KiB/);
  for (const maxCalls of ["0", "21", "1.5", "", "x"]) assert.match(formProblem(form({ maxCalls }), cases), /Model calls/);
  for (const threshold of ["1.1", "-0", "x", "", "2"]) assert.match(formProblem(form({ threshold }), cases), /threshold/);
  for (const threshold of ["0", "1", "0.8", ".5", "1.0"]) assert.equal(formProblem(form({ threshold }), cases), null);
  for (const retries of ["4", "-1", "a"]) assert.match(formProblem(form({ retries }), cases), /Retries/);
  assert.match(formProblem(form({ modelId: "gemini/a b" }), cases), /A model id is letters/);
  assert.equal(formProblem(form({ modelId: "gemini/gemini-3.8-flash" }), cases), null);
});

test("the cases must load and fit the spec", () => {
  assert.match(formProblem(form(), ""), /at least one eval case/);
  assert.match(formProblem(form(), casesOf({ ...FIRST_CASE, reference: "" })), /Fix the eval cases first \(1 to fix\)/);
  assert.match(formProblem(form(), casesOf(FIRST_CASE, FIRST_CASE)), /Fix the eval cases/);
  assert.match(formProblem(form(), casesOf({ ...FIRST_CASE, expect: { sources_any: ["a"] } })), /case 1: cited sources need knowledge/);
  assert.match(formProblem(form(), casesOf({ ...FIRST_CASE, turns: [{ message: "hi" }] })), /a turn is not text/);
  assert.match(formProblem(form(), casesOf({ ...FIRST_CASE, turns: Array(11).fill("hi") })), /more than 10 turns/);
  assert.match(formProblem(form(), casesOf({ ...FIRST_CASE, reference: "x".repeat(4001) })), /over 4000 characters/);
  assert.match(formProblem(form(), casesOf({ ...FIRST_CASE, id: "x y" })), /case 1: an id is letters/);
  assert.match(formProblem(form(), casesOf({ ...FIRST_CASE, id: " hello" })), /case 1: an id is letters/);
  const big = Array.from({ length: 70 }, (_, i) => ({ ...FIRST_CASE, id: `c${i}`, reference: "x".repeat(3999) }));
  assert.match(formProblem(form(), casesOf(...big)), /over 256 KiB/);
  const many = Array.from({ length: MAX_CASES + 1 }, (_, i) => ({ ...FIRST_CASE, id: `c${i}` }));
  assert.match(formProblem(form(), casesOf(...many)), /at most 100 eval cases/);
});

test("the spec is the CLI's schema 1, with numbers and CRLF normalised", () => {
  const spec = buildSpec(form({ name: " helper ", modelId: " ", maxCalls: " 3 ", threshold: "0.8", retries: "0" }), casesOf(FIRST_CASE));
  assert.deepEqual(spec, {
    schema_version: 1, name: "helper", model: "gemini", model_id: null, max_model_calls: 3,
    prompt: { text: "You are helper.\n" },
    evals: { cases: [FIRST_CASE], threshold: 0.8, retries: 0 },
    tools: [], knowledge: [], mcp: [],
  });
  assert.equal(buildSpec(form({ modelId: " gemini/x " }), initialCases()).model_id, "gemini/x");
  assert.deepEqual(specCases(casesOf({ id: 7, turns: ["hi"], reference: "r" })).cases, [{ id: "7", turns: ["hi"], expect: {}, reference: "r" }]);
});

test("bodies carry the spec, and create the previewed digest", () => {
  const spec = buildSpec(form(), initialCases());
  assert.deepEqual(previewBody(spec), { spec });
  assert.deepEqual(createBody(spec, preview(), true), { spec, digest: DIGEST, code_confirmed: true });
  assert.equal(createBody(spec, preview(), "yes").code_confirmed, false);
});

test("an unverified combination names what has not been run end to end", () => {
  assert.equal(unverifiedText(null), null);
  assert.equal(unverifiedText(preview()), null);
  const openai = preview({ model: "openai", combination: { parts: ["model", "prompt", "evals"], verified: false } });
  assert.match(unverifiedText(openai), /^Not run end to end yet: the openai provider\./);
  const tools = preview({ combination: { parts: ["model", "prompt", "evals", "tools", "mcp"], verified: false } });
  assert.match(unverifiedText(tools), /yet: a new agent with tools and mcp\./);
  const both = { ...tools, model: "anthropic" };
  assert.match(unverifiedText(both), /the anthropic provider, and a new agent with tools and mcp/);
});

test("create waits for a fresh, verified, confirmed preview", () => {
  assert.equal(createProblem(null, false, false), "Preview first.");
  assert.match(createProblem(preview(), true, false), /preview again/);
  assert.match(createProblem(preview({ combination: { parts: [], verified: false } }), false, true), /not been run end to end/);
  assert.match(createProblem(preview({ runs_code: true }), false, false), /Confirm/);
  assert.equal(createProblem(preview({ runs_code: true }), false, true), null);
  assert.equal(createProblem(preview(), false, false), null);
});
