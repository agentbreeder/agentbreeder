import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_RUNTIME, FIRST_CASE, MAX_CASES, MAX_SERVERS, MAX_TOOLS, buildSpec, createBody, createProblem, defaultRuntime, formProblem, frameworks, grantTool,
  hasCapability, initialCases, initialForm, invalidPicks, invalidProblem, invalidText, keepOffered, pickFramework, pickGroups, pickServer, pickTool, picksProblem,
  previewBody, removePick, reservedName, runtimeAnnouncement, runtimeLabel, runtimeOf, runtimeStatus, specCases, toolBlocked, toolsFull, unusableRuntimes,
  unusableText, unverifiedText,
} from "../../src/agentbreeder/center/static/lib/builder.js";
import { pyJson } from "../../src/agentbreeder/center/static/lib/cases.js";

const DIGEST = `sha256:${"a".repeat(64)}`;
const ADK = "runtime/adk@1.4.0";
const form = (changes = {}) => ({ ...initialForm(), name: "helper", runtime: ADK, prompt: "You are helper.\r\n", ...changes });
const casesOf = (...cases) => cases.map((data) => pyJson(data)).join("\n") + "\n";
const preview = (changes = {}) => ({
  model: "gemini", plan_sha256: DIGEST, runs_code: false,
  combination: { parts: ["model", "prompt", "evals"], verified: true }, ...changes,
});

test("a new form starts with one working case and needs a name and prompt", () => {
  assert.equal(initialCases(), `${pyJson(FIRST_CASE)}\n`);
  assert.equal(formProblem(initialForm(), initialCases()), "Type the agent's name first.");
  assert.equal(formProblem({ ...initialForm(), name: "helper" }, initialCases()), "Choose a framework first.");
  assert.equal(formProblem({ ...initialForm(), name: "helper", runtime: ADK }, initialCases()), "Write the agent's prompt first.");
  assert.equal(formProblem(form(), initialCases(), "2 picks do not work."), "2 picks do not work.", "an invalid pick holds Preview");
  assert.equal(formProblem(form({ name: "" }), initialCases(), "2 picks do not work."), "Type the agent's name first.");
  assert.equal(formProblem(form(), initialCases()), null);
});

test("the form says what is wrong, in order", () => {
  const cases = initialCases();
  assert.match(formProblem(form({ name: "Helper" }), cases), /lowercase letter first/);
  assert.match(formProblem(form({ model: "mistral" }), cases), /provider/);
  assert.equal(formProblem(form({ model: "openrouter" }), cases), null); // it has a default (#1020)
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

test("the spec is the CLI's schema 2 with the runtime ref, with numbers and CRLF normalised", () => {
  const spec = buildSpec(form({ name: " helper ", modelId: " ", maxCalls: " 3 ", threshold: "0.8", retries: "0" }), casesOf(FIRST_CASE));
  assert.deepEqual(spec, {
    schema_version: 2, name: "helper", runtime: ADK, model: "gemini", model_id: null, max_model_calls: 3,
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
  const anthropic = preview({ model: "anthropic", combination: { parts: ["model", "prompt", "evals"], verified: false } });
  assert.match(unverifiedText(anthropic), /^Not run end to end yet: the anthropic provider\./);
  const tools = preview({ combination: { parts: ["model", "prompt", "evals", "tools", "mcp"], verified: false } });
  assert.match(unverifiedText(tools), /yet: a new agent with tools and MCP servers\./);
  const both = { ...tools, model: "anthropic" };
  assert.match(unverifiedText(both), /the anthropic provider, and a new agent with tools and MCP servers/);
  const router = { ...tools, model: "openrouter" }; // a verified provider is not named (#1020)
  assert.match(unverifiedText(router), /^Not run end to end yet: a new agent with tools and MCP servers\./);
  const openai = { ...tools, model: "openai" }; // nor is openai, verified since #1020
  assert.match(unverifiedText(openai), /^Not run end to end yet: a new agent with tools and MCP servers\./);
  // With the previewed runtime's label (#1085): the framework and version are named.
  assert.match(unverifiedText(tools, undefined, "LangGraph 1.0.0"), /^Not run end to end yet: LangGraph 1\.0\.0 with tools and MCP servers\./);
  assert.match(unverifiedText(both, undefined, "LangGraph 1.0.0"), /the anthropic provider, and LangGraph 1\.0\.0 with tools and MCP servers/);
  const bare = preview({ combination: { parts: ["model", "prompt", "evals"], verified: false } });
  assert.match(unverifiedText(bare, undefined, "Google ADK 1.3.0"), /^Not run end to end yet: a new agent on Google ADK 1\.3\.0\./);
  assert.match(unverifiedText(bare), /^Not run end to end yet: this combination\./);
});

test("create waits for a fresh, verified, confirmed preview", () => {
  assert.equal(createProblem(null, false, false), "Preview first.");
  assert.match(createProblem(preview(), true, false), /preview again/);
  assert.match(createProblem(preview({ combination: { parts: [], verified: false } }), false, true), /not been run end to end/);
  assert.match(createProblem(preview({ runs_code: true }), false, false), /Confirm/);
  assert.equal(createProblem(preview({ runs_code: true }), false, true), null);
  assert.equal(createProblem(preview(), false, false), null);
  // Create's own reason names the framework and the parts that were not run on it (§4.3).
  const mcp = preview({ combination: { parts: ["model", "prompt", "evals", "mcp"], verified: false } });
  assert.equal(createProblem(mcp, false, true, "LangGraph 1.0.0"), "LangGraph 1.0.0 with MCP servers has not been run end to end.");
  assert.equal(createProblem(mcp, false, true), "This combination has not been run end to end.");
  const bare = preview({ combination: { parts: ["model", "prompt", "evals"], verified: false } });
  assert.equal(createProblem(bare, false, true, "Google ADK 1.3.0"), "A new agent on Google ADK 1.3.0 has not been run end to end.");
  assert.equal(createProblem({ ...bare, model: "anthropic" }, false, true, "Google ADK 1.4.0"), "This combination has not been run end to end.");
  assert.equal(createProblem(preview(), false, false, "Google ADK 1.4.0"), null);
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
  const runtimes = [{ ref: ADK, name: "adk", version: "1.4.0", usable: true }];
  const parts = { tools: [{ ref: "tool/clock@0.2.0" }], mcp: SERVERS, runtimes };
  assert.equal(keepOffered(picked, parts), picked, "the same form when everything is still offered");
  const fewer = { tools: [], mcp: [{ ...SERVERS[0], tools: [{ name: "get_me", effect: "read" }] }], runtimes };
  const kept = keepOffered(picked, fewer);
  assert.deepEqual(kept.tools, {});
  assert.deepEqual(kept.mcp.github, { ref: "mcp/github@1.1.0", tools: ["get_me"] });
  assert.deepEqual(keepOffered(picked, { tools: [], mcp: [{ ...SERVERS[0], unusable: "no" }] }).mcp, {});
});

// -- the framework (#1085 S1) --------------------------------------------------------------------

const runtime = (name, version, changes = {}) => ({
  ref: `runtime/${name}@${version}`, name, version, root_kind: "builtin", usable: true, verified: false,
  display_name: name, framework: name, protocol: 1, profile: "v1",
  capabilities: ["knowledge", "mcp", "notes"], limits: { knowledge: 1, mcp_servers: 10, tools: 50 }, reserved_tool_names: [], ...changes,
});
const RUNTIMES = [
  runtime("adk", "1.4.0", { display_name: "Google ADK", verified: true, reserved_tool_names: ["transfer_to_agent"] }),
  runtime("adk", "1.3.0", { display_name: "Google ADK", verified: true }),
  runtime("adk", "1.10.0", { display_name: "Google ADK" }),
  runtime("adk", "1.0.4", { display_name: "Google ADK", capabilities: ["knowledge", "notes"] }),
  runtime("openai-agents", "1.0.0", { display_name: "OpenAI Agents SDK", conformance: { passed: false } }),
  runtime("langgraph", "1.0.0", { display_name: "LangGraph", capabilities: ["knowledge"], limits: { knowledge: 1, mcp_servers: 0, tools: 1 }, reserved_tool_names: ["echo_clock"], conformance: { passed: true }, notes: "Needs an Anthropic-shaped gateway route." }),
  { ref: "runtime/example@0.1.0", name: "example", version: "0.1.0", root_kind: "path", usable: false, verified: false, code: "PROTOCOL_UNSUPPORTED", reason: "protocol 3 is not supported by this AgentBreeder" },
];
const LANGGRAPH = RUNTIMES[5];
const TOOLS = [
  { ref: "tool/clock@0.2.0", name: "clock", version: "0.2.0", functions: ["echo_clock"] },
  { ref: "tool/weather@1.0.0", name: "weather", version: "1.0.0", functions: ["forecast"] },
  { ref: "tool/notes@1.0.0", name: "notes", version: "1.0.0", functions: ["add_note"] },
];

test("frameworks are one per runtime name, run ones first, then by name, newest version first", () => {
  assert.deepEqual(frameworks(RUNTIMES).map((group) => [group.name, group.versions.map((entry) => entry.version)]), [
    ["adk", ["1.10.0", "1.4.0", "1.3.0", "1.0.4"]], // 1.10.0 is newer than 1.4.0: by number, not text
    ["langgraph", ["1.0.0"]],
    ["openai-agents", ["1.0.0"]],
  ]);
  assert.deepEqual(frameworks(undefined), []);
});

test("a runtime the CLI cannot use is text, never a choice", () => {
  assert.deepEqual(unusableRuntimes(RUNTIMES).map((entry) => entry.ref), ["runtime/example@0.1.0"]);
  assert.equal(unusableText(RUNTIMES[6]), "Cannot be used: runtime/example@0.1.0: protocol 3 is not supported by this AgentBreeder (PROTOCOL_UNSUPPORTED)");
  assert.equal(runtimeOf(RUNTIMES, "runtime/example@0.1.0"), null);
  assert.equal(runtimeOf(RUNTIMES, "runtime/nope@1.0.0"), null);
  assert.equal(runtimeOf(undefined, ADK), null);
  assert.equal(runtimeOf(RUNTIMES, ADK), RUNTIMES[0]);
});

test("a new form starts on the default framework's newest version (C1)", () => {
  assert.equal(DEFAULT_RUNTIME, "adk");
  assert.equal(defaultRuntime(RUNTIMES.filter((entry) => entry.version !== "1.10.0")), ADK);
  assert.equal(defaultRuntime(RUNTIMES), "runtime/adk@1.10.0");
  assert.equal(defaultRuntime([LANGGRAPH]), null, "no other framework becomes the default");
  assert.equal(pickFramework(form(), "langgraph", RUNTIMES).runtime, "runtime/langgraph@1.0.0");
  const same = form();
  assert.equal(pickFramework(same, "nope", RUNTIMES), same);
});

test("the status is one of four values, in the console's words", () => {
  assert.equal(runtimeStatus(RUNTIMES[0]), "Run end to end (Northwind)");
  assert.equal(runtimeStatus(LANGGRAPH), "Conformance passed; not run end to end");
  assert.equal(runtimeStatus(RUNTIMES[4]), "Conformance failed; not run end to end");
  assert.equal(runtimeStatus(RUNTIMES[3]), "Not run end to end");
  assert.equal(runtimeStatus({ ...LANGGRAPH, verified: true }), "Run end to end (Northwind)", "a run wins over a report");
  assert.equal(runtimeLabel(LANGGRAPH), "LangGraph 1.0.0");
  assert.equal(runtimeAnnouncement(LANGGRAPH), "LangGraph 1.0.0: Conformance passed; not run end to end");
});

test("a switch marks the picks the framework does not take, each with a reason, and never drops them", () => {
  let picked = pickTool(pickTool(form(), "clock", "tool/clock@0.2.0"), "weather", "tool/weather@1.0.0");
  picked = pickTool(picked, "notes", "tool/notes@1.0.0");
  picked = grantTool(pickServer(picked, "github", "mcp/github@1.1.0", SERVERS), "github", "get_me", true, SERVERS);
  const parts = { tools: TOOLS, mcp: SERVERS, runtimes: RUNTIMES };
  assert.deepEqual(invalidPicks(picked, RUNTIMES[1], parts), [], "everything fits Google ADK 1.3.0");
  assert.deepEqual(invalidPicks(picked, null, parts), [], "nothing is judged before a runtime is chosen");
  const invalid = invalidPicks(picked, LANGGRAPH, parts);
  assert.deepEqual(invalid, [
    // clock has its own reason and takes none of the limit: notes is the one tool, weather is past it.
    { kind: "tool", name: "clock", ref: "tool/clock@0.2.0", reason: "LangGraph 1.0.0 reserves the tool name echo_clock." },
    { kind: "tool", name: "weather", ref: "tool/weather@1.0.0", reason: "LangGraph 1.0.0 takes at most 1 tool." },
    { kind: "mcp", name: "github", ref: "mcp/github@1.1.0", reason: "LangGraph 1.0.0 has no MCP client." },
  ]);
  const two = removePick(picked, { kind: "tool", name: "notes" });
  assert.deepEqual(invalidPicks(two, LANGGRAPH, parts).map((pick) => pick.name), ["clock", "github"], "weather alone fits beside a reserved name");
  assert.equal(invalidText(invalid, LANGGRAPH), "3 picks do not work with LangGraph 1.0.0.");
  assert.equal(invalidProblem(invalid, LANGGRAPH), "3 picks do not work with LangGraph 1.0.0. Remove them or choose another framework.");
  assert.equal(invalidProblem(invalid.slice(0, 1), LANGGRAPH), "1 pick does not work with LangGraph 1.0.0. Remove it or choose another framework.");
  assert.equal(invalidText([], LANGGRAPH), null);
  assert.equal(invalidProblem([], LANGGRAPH), null);
  // The picks are still on the form: switching back clears the marks, and Remove takes one off.
  assert.deepEqual(Object.keys(picked.tools), ["clock", "weather", "notes"]);
  const removed = removePick(removePick(removePick(picked, invalid[2]), invalid[0]), invalid[1]);
  assert.deepEqual(removed.tools, { notes: "tool/notes@1.0.0" });
  assert.deepEqual(removed.mcp, {});
  assert.deepEqual(invalidPicks(removed, LANGGRAPH, parts), []);
  assert.deepEqual(Object.keys(picked.mcp), ["github"], "removing never changes the form it was given");
});

test("an MCP server past the runtime's limit is marked, in name order", () => {
  const two = { ...form(), mcp: { github: { ref: "mcp/github@1.1.0", tools: ["get_me"] }, notes: { ref: "mcp/notes@0.1.0", tools: ["add"] } } };
  const one = runtime("crewai", "1.0.0", { display_name: "CrewAI", limits: { knowledge: 1, mcp_servers: 1, tools: 50 } });
  assert.deepEqual(invalidPicks(two, one, { tools: [], mcp: SERVERS }), [
    { kind: "mcp", name: "notes", ref: "mcp/notes@0.1.0", reason: "CrewAI 1.0.0 takes at most 1 MCP server." },
  ]);
});

test("a reserved function name and the tools limit hold a tool off the picker", () => {
  assert.equal(reservedName(TOOLS[0], LANGGRAPH), "echo_clock");
  assert.equal(reservedName(TOOLS[1], LANGGRAPH), null);
  assert.equal(reservedName(undefined, LANGGRAPH), null);
  assert.equal(reservedName(TOOLS[0], null), null);
  assert.equal(toolBlocked(TOOLS[0], LANGGRAPH), "LangGraph 1.0.0 reserves the tool name echo_clock.");
  assert.equal(toolBlocked(TOOLS[0], RUNTIMES[0]), null);
  assert.equal(toolsFull(form(), LANGGRAPH), false);
  assert.equal(toolsFull(pickTool(form(), "weather", "tool/weather@1.0.0"), LANGGRAPH), true);
  assert.equal(toolsFull(pickTool(form(), "weather", "tool/weather@1.0.0"), RUNTIMES[0]), false);
  assert.equal(toolsFull(pickTool(form(), "weather", "tool/weather@1.0.0"), null), false);
  assert.equal(hasCapability(LANGGRAPH, "mcp"), false);
  assert.equal(hasCapability(RUNTIMES[0], "mcp"), true);
  assert.equal(hasCapability(null, "mcp"), false);
});

test("a re-read gives a form its runtime: the default when it has none or its own is gone", () => {
  const parts = { tools: [], mcp: [], runtimes: RUNTIMES.filter((entry) => entry.version !== "1.10.0") };
  assert.equal(keepOffered(initialForm(), parts).runtime, ADK);
  const chosen = form({ runtime: "runtime/langgraph@1.0.0" });
  assert.equal(keepOffered(chosen, parts), chosen, "a runtime still offered is kept");
  assert.equal(keepOffered(form({ runtime: "runtime/example@0.1.0" }), parts).runtime, ADK, "one that cannot be used is not kept");
  assert.equal(keepOffered(form({ runtime: "runtime/gone@1.0.0" }), parts).runtime, ADK);
  const none = keepOffered(form({ runtime: "runtime/gone@1.0.0" }), { tools: [], mcp: [], runtimes: [LANGGRAPH] });
  assert.equal(none.runtime, null, "no default framework: the user chooses");
  const empty = initialForm();
  assert.equal(keepOffered(empty, { tools: [], mcp: [], runtimes: [] }), empty);
});
