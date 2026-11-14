import assert from "node:assert/strict";
import { test } from "node:test";

import { SIGNATURE_REASONS, functionRows, parameterRows, signatureText } from "../../src/agentbreeder/center/static/lib/tools.js";

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
