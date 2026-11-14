import assert from "node:assert/strict";
import { test } from "node:test";

import {
  FIRST_CASE, MAX_CASES, MAX_SERVERS, MAX_TOOLS, buildSpec, createBody, createProblem, formProblem, grantTool, initialCases, initialForm, keepOffered, pickGroups,
  pickServer, pickTool, picksProblem, previewBody, specCases, unverifiedText,
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
  assert.match(unverifiedText(tools), /yet: a new agent with tools and MCP servers\./);
  const both = { ...tools, model: "anthropic" };
  assert.match(unverifiedText(both), /the anthropic provider, and a new agent with tools and MCP servers/);
});

test("create waits for a fresh, verified, confirmed preview", () => {
  assert.equal(createProblem(null, false, false), "Preview first.");
  assert.match(createProblem(preview(), true, false), /preview again/);
  assert.match(createProblem(preview({ combination: { parts: [], verified: false } }), false, true), /not been run end to end/);
  assert.match(createProblem(preview({ runs_code: true }), false, false), /Confirm/);
  assert.equal(createProblem(preview({ runs_code: true }), false, true), null);
  assert.equal(createProblem(preview(), false, false), null);
});

const SERVERS = [
  { ref: "mcp/github@1.1.0", name: "github", version: "1.1.0", tools: [{ name: "get_me", effect: "read" }, { name: "list_issues", effect: "read" }] },
  { ref: "mcp/github@1.0.0", name: "github", version: "1.0.0", tools: [{ name: "get_me", effect: "read" }, { name: "issue_read", effect: "read" }] },
  { ref: "mcp/notes@0.1.0", name: "notes", version: "0.1.0", tools: [{ name: "add", effect: "write" }] },
];

test("pickers group versions by name in the server's order", () => {
  assert.deepEqual(pickGroups(SERVERS).map((group) => [group.name, group.versions.map((entry) => entry.version)]), [
    ["github", ["1.1.0", "1.0.0"]],
    ["notes", ["0.1.0"]],
  ]);
  assert.deepEqual(pickGroups(undefined), []);
});

test("a tool pick puts one version per tool in the spec, sorted by name", () => {
  let picked = pickTool(form(), "weather", "tool/weather@1.0.0");
  picked = pickTool(picked, "clock", "tool/clock@0.2.0");
  picked = pickTool(picked, "weather", "tool/weather@1.1.0");
  assert.deepEqual(buildSpec(picked, initialCases()).tools, ["tool/clock@0.2.0", "tool/weather@1.1.0"]);
  assert.deepEqual(buildSpec(pickTool(picked, "clock", ""), initialCases()).tools, ["tool/weather@1.1.0"]);
  assert.deepEqual(form().tools, {}, "picking never changes the form it was given");
});

test("an MCP server needs at least one granted tool, in its declared order", () => {
  let picked = pickServer(form(), "github", "mcp/github@1.1.0", SERVERS);
  assert.match(formProblem(picked, initialCases()), /Grant github at least one of its tools/);
  picked = grantTool(picked, "github", "list_issues", true, SERVERS);
  picked = grantTool(picked, "github", "get_me", true, SERVERS);
  assert.equal(formProblem(picked, initialCases()), null);
  assert.deepEqual(buildSpec(picked, initialCases()).mcp, [{ ref: "mcp/github@1.1.0", tools: ["get_me", "list_issues"] }]);
  const fewer = grantTool(picked, "github", "list_issues", false, SERVERS);
  assert.deepEqual(fewer.mcp.github.tools, ["get_me"]);
  assert.equal(grantTool(form(), "github", "get_me", true, SERVERS).mcp.github, undefined, "no grant without a picked server");
});

test("another version keeps only the grants it still declares; Not used removes the server", () => {
  let picked = pickServer(form(), "github", "mcp/github@1.1.0", SERVERS);
  picked = grantTool(grantTool(picked, "github", "get_me", true, SERVERS), "github", "list_issues", true, SERVERS);
  const older = pickServer(picked, "github", "mcp/github@1.0.0", SERVERS);
  assert.deepEqual(older.mcp.github, { ref: "mcp/github@1.0.0", tools: ["get_me"] });
  assert.deepEqual(buildSpec(pickServer(older, "github", "", SERVERS), initialCases()).mcp, []);
});

test("the picks' limits match the spec's", () => {
  const tools = Object.fromEntries(Array.from({ length: MAX_TOOLS + 1 }, (_, index) => [`t${index}`, `tool/t${index}@1.0.0`]));
  assert.match(picksProblem(form({ tools })), new RegExp(`at most ${MAX_TOOLS} tools`));
  const mcp = Object.fromEntries(Array.from({ length: MAX_SERVERS + 1 }, (_, index) => [`s${index}`, { ref: `mcp/s${index}@1.0.0`, tools: ["a"] }]));
  assert.match(picksProblem(form({ mcp })), new RegExp(`at most ${MAX_SERVERS} MCP servers`));
  assert.equal(picksProblem(form()), null);
});

test("a pick the newest list no longer offers is dropped, and so are grants it no longer declares", () => {
  let picked = pickTool(form(), "clock", "tool/clock@0.2.0");
  picked = grantTool(pickServer(picked, "github", "mcp/github@1.1.0", SERVERS), "github", "list_issues", true, SERVERS);
  picked = grantTool(picked, "github", "get_me", true, SERVERS);
  const parts = { tools: [{ ref: "tool/clock@0.2.0" }], mcp: SERVERS };
  assert.equal(keepOffered(picked, parts), picked, "the same form when everything is still offered");
  const fewer = { tools: [], mcp: [{ ...SERVERS[0], tools: [{ name: "get_me", effect: "read" }] }] };
  const kept = keepOffered(picked, fewer);
  assert.deepEqual(kept.tools, {});
  assert.deepEqual(kept.mcp.github, { ref: "mcp/github@1.1.0", tools: ["get_me"] });
  assert.deepEqual(keepOffered(picked, { tools: [], mcp: [{ ...SERVERS[0], unusable: "no" }] }).mcp, {});
});
