// A Try it turn's trace (#907): the pure helpers behind the span tree.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_SPAN_DEPTH, attributionText, descendants, spanFacts, spanTokensText, spanTree, traceProblemText, tracePath, traceSummary,
} from "../../src/agentbreeder/center/static/lib/tryit.js";

const span = (id, parent, extra = {}) => ({ span_id: id, parent_id: parent, name: id, kind: "AGENT", status: "unset", ...extra });
const ids = (nodes) => nodes.map((node) => [node.span.span_id, ids(node.children)]);

test("a trace path needs a 32-hex id", () => {
  assert.equal(tracePath("0123456789ab", "a".repeat(32)), `/api/projects/0123456789ab/traces/${"a".repeat(32)}`);
  for (const bad of ["A".repeat(32), "a".repeat(31), null, undefined, 7, `${"a".repeat(32)}/x`]) {
    assert.equal(tracePath("0123456789ab", bad), null);
  }
});

test("the tree follows parent ids; a missing parent makes a root", () => {
  const tree = spanTree([span("r", null), span("a", "r"), span("b", "a"), span("c", "r"), span("o", "gone")]);
  assert.deepEqual(ids(tree), [["r", [["a", [["b", []]]], ["c", []]]], ["o", []]]);
});

test("cycles and repeats never drop or repeat a span", () => {
  const tree = spanTree([span("x", "y"), span("y", "x"), span("z", "y"), span("s", "s"), span("x", null), null, { name: "no id" }]);
  const seen = [];
  const walk = (nodes) => nodes.forEach((node) => {
    seen.push(node.span.span_id);
    walk(node.children);
  });
  walk(tree);
  assert.deepEqual(seen.sort(), ["s", "x", "y", "z"]);
  assert.deepEqual(spanTree(null), []);
});

test("descendants list a branch depth first, flat", () => {
  const [root] = spanTree([span("r", null), span("a", "r"), span("b", "a"), span("c", "r")]);
  assert.deepEqual(descendants(root).map((node) => [node.span.span_id, node.children.length]), [["a", 0], ["b", 0], ["c", 0]]);
  const chain = Array.from({ length: 600 }, (_, i) => span(`s${i}`, i ? `s${i - 1}` : null));
  const [top] = spanTree(chain);
  assert.equal(descendants(top).length, 599);
  assert.equal(MAX_SPAN_DEPTH, 8);
});

test("a span's facts in words", () => {
  assert.equal(spanTokensText(null), null);
  assert.equal(spanTokensText({ prompt: 321, completion: null }), "321 in, ? out");
  assert.equal(
    spanFacts(span("c", null, { kind: "LLM", status: "ok", latency_ms: 20, model: "gemini-2.5-flash", tokens: { prompt: 1321, completion: 70 } })),
    "llm · 20 ms · model gemini-2.5-flash · 1,321 in, 70 out tokens",
  );
  assert.equal(spanFacts(span("lookup", null, { kind: "TOOL", status: "error", tool: "lookup_order" })), "tool · error · tool lookup_order");
  assert.equal(spanFacts(span("lookup_order", null, { kind: "TOOL", tool: "lookup_order" })), "tool");
  assert.equal(spanFacts({ span_id: "q", kind: null }), "unknown");
});

test("the trace in one line", () => {
  const trace = {
    status: "error", latency_ms: 41, spans: [span("a", null)], truncated: true, models: ["chat", 7],
    tokens: { total: 391 }, tool_calls: [{}, {}],
  };
  assert.equal(traceSummary(trace), "Error · 41 ms · 1 span (Phoenix had more; the rest are not shown) · model chat · 391 tokens · 2 tool calls");
  assert.equal(traceSummary({ status: "ok", spans: [], models: [], tool_calls: [{}] }), "OK · 0 spans · 1 tool call");
  assert.equal(traceSummary({}), "Status unknown · 0 spans · 0 tool calls");
});

test("attribution and failures in words", () => {
  assert.match(attributionText({ attribution: "exact" }), /Recorded by the agent's runtime/);
  assert.match(attributionText({ attribution: "window" }), /Matched by time/);
  assert.equal(attributionText({}), "Not attributed to a deployment.");
  assert.match(traceProblemText({ code: "TRACES_NOT_FOUND", message: "x" }), /yet.*\[TRACES_NOT_FOUND\]/);
  assert.match(traceProblemText({ code: "TRACES_NO_PHOENIX", message: "x" }), /does not run Phoenix/);
  assert.equal(traceProblemText({ code: "TRACES_AUTH", message: "Phoenix refused the key" }), "Phoenix refused the key [TRACES_AUTH]");
  assert.equal(traceProblemText(null), "The trace could not be read [TRACE_FAILED]");
});

test("a span whose counts are also on a span under it says they are counted there (#1009)", () => {
  const tokens = { prompt: 1756, completion: 104 };
  assert.equal(spanFacts(span("call_llm", null, { kind: "CHAIN", tokens, tokens_counted: false })), "chain · 1,756 in, 104 out tokens, counted in a span under it");
  assert.equal(spanFacts(span("g", null, { kind: "LLM", tokens, tokens_counted: true })), "llm · 1,756 in, 104 out tokens");
  assert.equal(spanFacts(span("g", null, { kind: "LLM", tokens })), "llm · 1,756 in, 104 out tokens");
});

test("a trace still arriving says so instead of guessing its deployment (#1010)", () => {
  const trace = { status: "ok", spans: [span("a", "gone"), span("b", "a")], models: [], tool_calls: [], incomplete: true, attribution: null };
  assert.equal(traceSummary(trace), "OK · 2 spans so far · 0 tool calls");
  assert.match(attributionText(trace), /^Still arriving: Phoenix does not have this trace's root span yet/);
  assert.doesNotMatch(attributionText(trace), /does not record/);
  assert.match(attributionText({ ...trace, attribution: "window" }), /^Still arriving/);
  assert.match(attributionText({ ...trace, attribution: "exact" }), /^Recorded by the agent's runtime/);
  assert.match(attributionText({ ...trace, incomplete: false, attribution: "window" }), /^Matched by time/);
});
