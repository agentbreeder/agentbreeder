import assert from "node:assert/strict";
import { test } from "node:test";

import { STATE_LABELS, stateText, statusFacts, statusPath, statusRefusal } from "../../src/agentbreeder/center/static/lib/knowledge.js";

const FRESH = {
  ref: "knowledge/northwind-help@1.0.0",
  table: "kb_northwind_help_1_0_0",
  schema: "support_agent",
  state: "fresh",
  documents: { expected: 5, indexed: 5 },
  chunks: { expected: 21, indexed: 21, fresh: 21, stale: 0, missing: 0, extra: 0 },
  updated: { oldest: "2026-11-13T09:12:03Z", newest: "2026-11-13T09:12:04Z" },
  embedding: { alias: "support-embedding", dimensions: 768, table_dimensions: 768 },
  notes: [],
};

test("a status path only for a knowledge ref", () => {
  assert.equal(statusPath("abc", "knowledge/northwind-help@1.0.0"), "/api/projects/abc/knowledge/status?ref=knowledge%2Fnorthwind-help%401.0.0");
  assert.equal(statusPath("abc", "knowledge/northwind-help"), "/api/projects/abc/knowledge/status?ref=knowledge%2Fnorthwind-help");
  for (const bad of ["tool/orders@1.0.0", "knowledge/Help", "knowledge/help@1.0", "knowledge/help@01.0.0", "--project", "", null, undefined]) {
    assert.equal(statusPath("abc", bad), null, String(bad));
  }
});

test("every state has a label and an explanation", () => {
  for (const state of ["not_indexed", "fresh", "stale"]) {
    assert.ok(STATE_LABELS[state]);
    assert.ok(stateText({ state }));
  }
  assert.equal(stateText({ state: "maybe" }), "");
  assert.equal(stateText(null), "");
  assert.match(stateText({ state: "stale" }), /release/);
});

test("the facts of a fresh version", () => {
  assert.deepEqual(statusFacts(FRESH), [
    ["Table", "support_agent.kb_northwind_help_1_0_0"],
    ["Chunks", "21 indexed of 21 (fresh 21, stale 0, missing 0, extra 0)"],
    ["Documents", "5 indexed of 5"],
    ["Indexed", "2026-11-13T09:12:03Z to 2026-11-13T09:12:04Z"],
    ["Embedding", "support-embedding, 768 dimensions"],
  ]);
});

test("a version not indexed yet, and a dimension mismatch", () => {
  const facts = Object.fromEntries(statusFacts({
    ...FRESH,
    state: "not_indexed",
    schema: null,
    chunks: { expected: 3, indexed: 0, fresh: 0, stale: 0, missing: 3, extra: 0 },
    updated: { oldest: null, newest: null },
    embedding: { alias: "e", dimensions: 8, table_dimensions: null },
  }));
  assert.equal(facts.Table, "kb_northwind_help_1_0_0 (not created yet)");
  assert.equal(facts.Indexed, "-");
  assert.equal(facts.Embedding, "e, 8 dimensions");
  const broken = Object.fromEntries(statusFacts({ ...FRESH, state: "stale", schema: null }));
  assert.equal(broken.Table, "kb_northwind_help_1_0_0");
  const mismatch = Object.fromEntries(statusFacts({ ...FRESH, embedding: { alias: "e", dimensions: 8, table_dimensions: 4 } }));
  assert.equal(mismatch.Embedding, "e, 8 dimensions (the table has 4)");
});

test("unknown values are shown as a dash", () => {
  const facts = Object.fromEntries(statusFacts({ state: "stale", chunks: { expected: null, indexed: "21", fresh: -1 }, embedding: {} }));
  assert.equal(facts.Table, "-");
  assert.equal(facts.Chunks, "- indexed of - (fresh -, stale -, missing -, extra -)");
  assert.equal(facts.Documents, "- indexed of -");
  assert.equal(facts.Embedding, "-, dimensions unknown");
  assert.equal(statusFacts(null).length, 5);
});

test("refusals name the next step", () => {
  const down = statusRefusal({ code: "SERVICES_DOWN", message: "x" }, "knowledge/help@1.0.0");
  assert.match(down.next, /services up/);
  const login = statusRefusal({ code: "DB_LOGIN_FAILED", message: "x" }, "knowledge/help@1.0.0");
  assert.match(login.next, /refresh/);
  const busy = statusRefusal({ code: "KNOWLEDGE_STATUS_BUSY", message: "4 reads are running" }, "knowledge/help@1.0.0");
  assert.equal(busy.reason, "4 reads are running");
  const other = statusRefusal({ code: "KNOWLEDGE_OBJECT_AMBIGUOUS", message: "in several schemas" }, "knowledge/help@1.0.0");
  assert.deepEqual(other, { reason: "in several schemas", next: "run agentbreeder knowledge status knowledge/help@1.0.0 --project DIR in a terminal" });
  assert.equal(statusRefusal(null, "knowledge/help").reason, "the index status could not be read");
});
