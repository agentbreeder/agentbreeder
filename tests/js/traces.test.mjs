import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_LAST, LAST_CHOICES, defaultDeployment, startText, toolCallsText, traceLine, tracesPath, tracesProblemText, windowText,
} from "../../src/agentbreeder/center/static/lib/traces.js";

const ID = "18bc2abed83946b6a52c045e973d1844";

test("tracesPath takes only a 32-hex deployment id and a listed count", () => {
  assert.equal(tracesPath("p1", ID), `/api/projects/p1/deployments/${ID}/traces?last=${DEFAULT_LAST}`);
  assert.equal(tracesPath("p1", ID, 50), `/api/projects/p1/deployments/${ID}/traces?last=50`);
  for (const bad of [ID.toUpperCase(), ID.slice(1), `${ID}0`, "../x", null, 7]) assert.equal(tracesPath("p1", bad), null);
  for (const last of [0, 7, 51, "20", Number.NaN]) assert.equal(tracesPath("p1", ID, last), null);
  assert.ok(LAST_CHOICES.every((n) => n >= 1 && n <= 50));
});

test("defaultDeployment prefers production, else the first row", () => {
  const rows = [{ id: "a" }, { id: "b" }];
  assert.equal(defaultDeployment(rows, { id: "b" }), "b");
  assert.equal(defaultDeployment(rows, { id: "z" }), "a");
  assert.equal(defaultDeployment(rows, null), "a");
  assert.equal(defaultDeployment([], null), "");
});

test("startText shows UTC to the second, or says it is unknown", () => {
  assert.equal(startText("2026-11-14T03:10:33.358423+00:00"), "2026-11-14 03:10:33 UTC");
  for (const bad of [null, "", "yesterday", 5]) assert.equal(startText(bad), "start unknown");
});

test("traceLine names status, latency, models, tokens and spans", () => {
  const trace = { status: "error", latency_ms: 41, models: ["gemini-2.5-flash"], tokens: { prompt: 321, completion: 70 }, span_count: 3, spans_truncated: false };
  assert.equal(traceLine(trace), "Error · 41 ms · model gemini-2.5-flash · 321 in, 70 out tokens · 3 spans");
  assert.equal(traceLine({ status: null, span_count: 1, spans_truncated: true }), "Status unknown · 1 span (Phoenix had more)");
  assert.equal(traceLine({ status: "ok", models: ["a", 3, "b"], tokens: { prompt: null, completion: 4 } }), "OK · models a, b · ? in, 4 out tokens · 0 spans");
});

test("toolCallsText lists calls with their errors and latency", () => {
  const trace = { tool_calls: [{ name: "lookup_order", status: "error", latency_ms: 11 }, { name: "refund", status: "ok" }, { name: 3 }] };
  assert.equal(toolCallsText(trace), "lookup_order (error, 11 ms), refund");
  assert.equal(toolCallsText({ tool_calls: [] }), null);
  assert.equal(toolCallsText({}), null);
});

test("windowText speaks only when a trace was matched by time", () => {
  assert.equal(windowText({ traces: [{ attribution: "exact" }] }), null);
  assert.match(windowText({ traces: [{ attribution: "window" }], overlapping: [] }), /^Matched by time/);
  assert.match(windowText({ traces: [{ attribution: "window" }], overlapping: ["x", "y"] }), /2 other deployments/);
  assert.equal(windowText({}), null);
});

test("tracesProblemText keeps the code and words the common ones", () => {
  assert.match(tracesProblemText({ code: "TRACES_NO_PHOENIX", message: "x" }), /does not run Phoenix.*\[TRACES_NO_PHOENIX\]$/);
  assert.match(tracesProblemText({ code: "TRACES_UNREACHABLE" }), /did not answer.*\[TRACES_UNREACHABLE\]$/);
  assert.equal(tracesProblemText({ code: "NOT_FOUND", message: "no deployment" }), "no deployment [NOT_FOUND]");
  assert.equal(tracesProblemText(null), "The traces could not be read [TRACES_FAILED]");
  assert.equal(tracesProblemText({}), "The traces could not be read [TRACES_FAILED]");
  assert.match(tracesProblemText({ code: "TRACE_BUSY", message: "4 trace reads" }), /still running.*\[TRACE_BUSY\]$/);
});
