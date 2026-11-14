import assert from "node:assert/strict";
import { test } from "node:test";

import { LOG_LIMITS, LOG_WINDOWS, MAX_AGENTS, SIGNATURE_REASONS, argsText, functionRows, logPath, logRefusal, logRows, parameterRows, seenAgents, signatureText } from "../../src/agentbreeder/center/static/lib/tools.js";

const LOOKUP = {
  name: "lookup_orders",
  module: "orders",
  function: "lookup_orders",
  description: "Look up orders",
  parameters: { type: "object", properties: { order_id: { type: "string", description: "One order" }, status: { type: "string" } }, required: ["order_id"] },
  signature: 'lookup_orders(order_id: str = "", status: str = "")',
  signature_error: null,
};

test("only a tool version has functions", () => {
  assert.equal(functionRows(null), null);
  assert.equal(functionRows({ kind: "prompt", functions: [LOOKUP] }), null);
  assert.deepEqual(functionRows({ kind: "tool", functions: [], functions_error: null }), { error: "", rows: [] });
});

test("a function row keeps tool.yaml's fields and the signature", () => {
  const { error, rows } = functionRows({ kind: "tool", functions: [LOOKUP], functions_error: null });
  assert.equal(error, "");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, "lookup_orders");
  assert.equal(rows[0].signature, LOOKUP.signature);
  assert.equal(rows[0].why, "");
  assert.deepEqual(rows[0].parameters, [
    { name: "order_id", type: "string", required: true, description: "One order" },
    { name: "status", type: "string", required: false, description: "" },
  ]);
});

test("an unreadable tool.yaml is the error, with no rows", () => {
  assert.deepEqual(functionRows({ kind: "tool", functions: [], functions_error: "[TOOL_INVALID] tool.yaml is missing" }), {
    error: "[TOOL_INVALID] tool.yaml is missing",
    rows: [],
  });
});

test("a missing signature says why", () => {
  for (const code of Object.keys(SIGNATURE_REASONS)) {
    const { signature, why } = signatureText({ signature: null, signature_error: code });
    assert.equal(signature, "");
    assert.ok(why.includes(SIGNATURE_REASONS[code]), why);
  }
  assert.match(signatureText({ signature: null, signature_error: "new_code" }).why, /new_code/);
  assert.match(signatureText({}).why, /unknown reason/);
  assert.match(signatureText({ signature: 3, signature_error: { code: "x" } }).why, /unknown reason/);
});

test("odd parameters and entries never throw", () => {
  assert.deepEqual(parameterRows(null), []);
  assert.deepEqual(parameterRows([]), []);
  assert.deepEqual(parameterRows({ properties: [] }), []);
  assert.deepEqual(parameterRows({ properties: { a: null, b: { type: ["string", "null", 3] } }, required: "a" }), [
    { name: "a", type: "any", required: false, description: "" },
    { name: "b", type: "string | null", required: false, description: "" },
  ]);
  const { rows } = functionRows({ kind: "tool", functions: [null, 3, { name: 7, description: { x: 1 } }], functions_error: null });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, "");
  assert.equal(rows[0].description, "");
});

// -- the execution log (#828 part 2) --------------------------------------------------------------

test("a log path carries only checked values; content is a flag", () => {
  assert.equal(logPath("0123456789ab", { ref: "tool/orders@1.0.0" }), "/api/projects/0123456789ab/tools/log?ref=tool%2Forders%401.0.0&since=24h&limit=100");
  assert.equal(
    logPath("0123456789ab", { ref: "tool/orders", since: "168h", agent: "northwind-support", limit: 500, content: true }),
    "/api/projects/0123456789ab/tools/log?ref=tool%2Forders&since=168h&limit=500&agent=northwind-support&content=1",
  );
  for (const ref of ["", "prompt/x", "tool/Orders", "tool/orders@v1", "mcp/x@1.0.0", null, 7]) assert.equal(logPath("0123456789ab", { ref }), null, String(ref));
  assert.equal(logPath("0123456789ab", { ref: "tool/orders", agent: "--project" }), null);
});

test("the offered windows and row counts stay inside the CLI's limits", () => {
  for (const window of LOG_WINDOWS) assert.match(window.value, /^[1-9][0-9]{0,5}h$/);
  assert.ok(LOG_WINDOWS.every((window) => Number(window.value.slice(0, -1)) <= 720));
  assert.ok(LOG_LIMITS.every((count) => count >= 1 && count <= 1000));
});

test("a refusal says why and what to do next", () => {
  const missing = logRefusal({ code: "CONSOLE_ROLE_MISSING", message: "cli text" }, "tool/orders");
  assert.equal(missing.empty, false);
  assert.match(missing.reason, /^the execution log needs service\/postgres-pgvector@1\.2\.0 with console_database: DB/);
  assert.match(missing.next, /services up --update/);
  const agent = logRefusal({ code: "AGENT_NOT_FOUND", message: "'x' is not in the stack's environment.agents" }, "tool/orders");
  assert.match(agent.reason, /environment\.agents/);
  assert.match(agent.next, /Every agent/);
  assert.match(logRefusal({ code: "TOOL_LOG_TIMEOUT", message: "x" }, "tool/orders").next, /--since/);
  assert.match(logRefusal({ code: "CONSOLE_ROLE_NO_ACCESS", message: "x" }, "tool/orders").next, /services up --update/);
  // The CLI's message names the credential that was refused; the next step is the console's.
  const login = logRefusal({ code: "DB_LOGIN_FAILED", message: "The console credential was refused" }, "tool/orders");
  assert.equal(login.reason, "The console credential was refused");
  assert.match(login.next, /services up/);
  assert.deepEqual(logRefusal({ code: "TOOL_LOG_NO_TABLES", message: "No calls recorded yet" }, "tool/orders"), { empty: true, reason: "", next: "" });
  const other = logRefusal({ code: "CONSOLE_OBJECT_UNEXPECTED", message: "audit_events has row security" }, "tool/orders");
  assert.equal(other.reason, "audit_events has row security");
  assert.equal(other.next, "run agentbreeder tool log tool/orders --project DIR in a terminal");
  assert.equal(logRefusal(null, "tool/orders").reason, "the log could not be read");
  assert.equal(logRefusal({ code: 7, message: {} }, "tool/x").reason, "the log could not be read");
});

test("log rows show '-' for anything unknown and mark console rows", () => {
  const rows = logRows({
    calls: [
      { at: "2026-11-13T16:02:11.123Z", agent: "northwind-support", tool: "tool/orders@1.0.0", function: "lookup_orders", status: "ok", latency_ms: 41, turn_id: "9f1c", trace_id: "4bf9", db_role: "support_agent" },
      { at: null, agent: null, tool: null, function: null, status: null, latency_ms: 1.5, turn_id: null, trace_id: null, db_role: "agentbreeder_console", args: { q: 1 }, error: "boom" },
      null,
      "x",
    ],
  });
  assert.equal(rows.length, 2);
  assert.deepEqual(
    { ...rows[0], key: undefined, args: undefined },
    { key: undefined, at: "2026-11-13T16:02:11.123Z", agent: "northwind-support", version: "1.0.0", function: "lookup_orders", status: "ok", latency: "41 ms", turn: "9f1c", trace: "4bf9", role: "support_agent", args: undefined, error: null },
  );
  assert.equal(rows[1].version, "-");
  assert.equal(rows[1].status, "-");
  assert.equal(rows[1].latency, "-");
  assert.equal(rows[1].role, "agentbreeder_console (console)");
  assert.equal(rows[1].error, "boom");
  assert.deepEqual(logRows(null), []);
  assert.deepEqual(logRows({ calls: "x" }), []);
});

test("arguments are shown as indented JSON, or why not", () => {
  assert.equal(argsText({ order_id: "NW-1" }), '{\n  "order_id": "NW-1"\n}');
  assert.match(argsText(null), /too large, or not an object/);
  const loop = {};
  loop.self = loop;
  assert.equal(argsText(loop), "(unreadable)");
});

test("the agent filter offers the agents seen, capped and checked", () => {
  assert.deepEqual(seenAgents([], [{ agent: "b" }, { agent: "a" }, { agent: "-" }, { agent: "Bad Name" }, null, { agent: "a" }]), ["a", "b"]);
  assert.deepEqual(seenAgents(["c"], [{ agent: "a" }]), ["a", "c"]);
  const many = Array.from({ length: 200 }, (_, i) => ({ agent: `agent-${String(i).padStart(3, "0")}` }));
  assert.equal(seenAgents([], many).length, MAX_AGENTS);
  assert.deepEqual(seenAgents(null, null), []);
});
