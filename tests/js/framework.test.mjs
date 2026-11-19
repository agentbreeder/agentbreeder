import assert from "node:assert/strict";
import { test } from "node:test";

import {
  NOT_RECORDED, OTHER, capabilitiesText, countText, filterEmptyText, filterKey, filterLabel, filterOptions, filterRows, frameworkFor, parseRuntime,
  protocolText, runtimeEntry,
} from "../../src/agentbreeder/center/static/lib/framework.js";

// The resolved view (`GET /api/runtimes`); LangGraph and CrewAI have no runtime yet (fixtures).
const ADK = { ref: "runtime/adk@1.4.0", name: "adk", version: "1.4.0", usable: true, display_name: "Google ADK", protocol: 1, profile: "v1", capabilities: ["knowledge", "mcp", "notes"] };
const ADK13 = { ...ADK, ref: "runtime/adk@1.3.0", version: "1.3.0" };
const LANGGRAPH = { ref: "runtime/langgraph@1.0.0", name: "langgraph", version: "1.0.0", usable: true, display_name: "LangGraph", protocol: 1, profile: "kit", kit: "1.0.0", capabilities: [] };
const CREWAI = { ref: "runtime/crewai@1.0.0", name: "crewai", version: "1.0.0", usable: true, display_name: "CrewAI", protocol: 1, profile: "kit", capabilities: ["knowledge"] };
const UNUSABLE = { ref: "runtime/example@0.1.0", name: "example", version: "0.1.0", usable: false, code: "PROTOCOL_UNSUPPORTED", reason: "protocol 3" };
const RUNTIMES = [ADK, ADK13, LANGGRAPH, CREWAI, UNUSABLE];

const rows = [
  { name: "a", runtime: "runtime/adk@1.4.0" },
  { name: "b", runtime: "runtime/adk@1.3.0" },
  { name: "c", runtime: "runtime/langgraph@1.0.0" },
  { name: "d", runtime: null },
  { name: "e" },
  { name: "f", runtime: "runtime/mystery@2.0.0" },
];

test("a runtime ref is parsed; anything else is not one", () => {
  assert.deepEqual(parseRuntime("runtime/adk@1.4.0"), { ref: "runtime/adk@1.4.0", name: "adk", version: "1.4.0" });
  for (const value of [null, undefined, 7, "", "tool/x@1.0.0", "runtime/Adk@1.0.0", "runtime/adk@1.0.0 ", "runtime/adk@<b>", "runtime/../x@1.0.0"]) {
    assert.equal(parseRuntime(value), null, String(value));
  }
});

test("the badge names the framework from the manifest, else the ref, else Not recorded", () => {
  assert.deepEqual(frameworkFor("runtime/adk@1.4.0", RUNTIMES), { text: "Google ADK", ref: "runtime/adk@1.4.0", known: true });
  // A version not listed takes its name from another version of the same runtime.
  assert.deepEqual(frameworkFor("runtime/adk@0.0.1", RUNTIMES), { text: "Google ADK", ref: "runtime/adk@0.0.1", known: true });
  assert.deepEqual(frameworkFor("runtime/mystery@2.0.0", RUNTIMES), { text: "runtime/mystery@2.0.0", ref: "runtime/mystery@2.0.0", known: false });
  // An unusable version is not a name source.
  assert.equal(frameworkFor("runtime/example@0.1.0", RUNTIMES).known, false);
  // Before the view answers, or when it failed, the ref itself.
  assert.equal(frameworkFor("runtime/adk@1.4.0", null).text, "runtime/adk@1.4.0");
  assert.deepEqual(frameworkFor(null, RUNTIMES), { text: "Not recorded", ref: null, known: false });
  // A recorded runtime of another shape is shown as recorded, capped; blank is Not recorded.
  assert.deepEqual(frameworkFor("runtime/adk@1.4.0-rc1", RUNTIMES), { text: "runtime/adk@1.4.0-rc1", ref: null, known: false });
  assert.equal(frameworkFor("x".repeat(300), RUNTIMES).text.length, 100);
  assert.deepEqual(frameworkFor("  ", RUNTIMES), { text: "Not recorded", ref: null, known: false });
});

test("runtimeEntry finds only a usable version", () => {
  assert.equal(runtimeEntry(RUNTIMES, "runtime/adk@1.3.0"), ADK13);
  assert.equal(runtimeEntry(RUNTIMES, "runtime/example@0.1.0"), null);
  assert.equal(runtimeEntry(null, "runtime/adk@1.3.0"), null);
});

test("the filter offers each framework present, by name, then Not recorded", () => {
  assert.deepEqual(filterOptions(rows, RUNTIMES), [
    { value: "", label: "All frameworks" },
    { value: "adk", label: "Google ADK" },
    { value: "langgraph", label: "LangGraph" },
    { value: "mystery", label: "mystery" },
    { value: NOT_RECORDED, label: "Not recorded" },
  ]);
  // Every row has a runtime: no Not recorded option.
  assert.deepEqual(filterOptions(rows.slice(0, 1), RUNTIMES).map((option) => option.value), ["", "adk"]);
  assert.deepEqual(filterOptions([], RUNTIMES), [{ value: "", label: "All frameworks" }]);
});

test("a runtime recorded in another shape is its own filter value", () => {
  const odd = [...rows, { name: "g", runtime: "runtime/adk@1.4.0-rc1" }];
  assert.deepEqual(filterOptions(odd, RUNTIMES).map((option) => option.value), ["", "adk", "langgraph", "mystery", OTHER, NOT_RECORDED]);
  assert.deepEqual(filterRows(odd, OTHER).map((row) => row.name), ["g"]);
  assert.equal(filterLabel(OTHER, odd, RUNTIMES), "Other runtime");
  assert.equal(filterEmptyText(OTHER, rows, RUNTIMES), "No agents on another runtime.");
  assert.equal(parseRuntime(`runtime/${OTHER}@1.0.0`), null);
});

test("the filter keeps the rows of one framework, or those with no runtime", () => {
  assert.equal(filterRows(rows, "").length, 6);
  assert.deepEqual(filterRows(rows, "adk").map((row) => row.name), ["a", "b"]);
  assert.deepEqual(filterRows(rows, NOT_RECORDED).map((row) => row.name), ["d", "e"]);
  assert.deepEqual(filterRows(rows, "crewai"), []);
  assert.deepEqual(filterRows(null, "adk"), []);
  assert.equal(filterKey({ runtime: "runtime/langgraph@1.0.0" }), "langgraph");
  assert.equal(NOT_RECORDED.includes("_"), true); // never a runtime name
  assert.equal(parseRuntime(`runtime/${NOT_RECORDED}@1.0.0`), null);
});

test("the empty state and the count", () => {
  assert.equal(filterEmptyText("crewai", rows, RUNTIMES), "No agents on CrewAI.");
  assert.equal(filterEmptyText("nothing", rows, RUNTIMES), "No agents on nothing.");
  assert.equal(filterEmptyText(NOT_RECORDED, rows, RUNTIMES), "No agents without a recorded runtime.");
  assert.equal(filterLabel("langgraph", rows, RUNTIMES), "LangGraph");
  assert.equal(countText(3, 9, true), "3 of 9 agents");
  assert.equal(countText(9, 9, false), "9 agents");
  assert.equal(countText(1, 1, false), "1 agent");
  assert.equal(countText(0, 1, true), "0 of 1 agent");
});

test("the Overview's protocol and capabilities rows", () => {
  assert.equal(protocolText(ADK), "1 (v1 profile, before the kit)");
  assert.equal(protocolText(LANGGRAPH), "1 (kit profile, kit 1.0.0)");
  assert.equal(protocolText(CREWAI), "1 (kit profile)");
  assert.equal(protocolText(null), null);
  assert.equal(capabilitiesText(ADK), "knowledge, mcp, notes");
  assert.equal(capabilitiesText(LANGGRAPH), "none");
  assert.equal(capabilitiesText(null), "none");
});
