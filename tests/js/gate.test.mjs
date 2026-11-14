import assert from "node:assert/strict";
import { test } from "node:test";

import { parseCases } from "../../src/agentbreeder/center/static/lib/cases.js";
import {
  CORRECTNESS_PASS, caseHref, casesById, evalSetRef, expectation, failureRows, gateFailedRun, promptHref, tailApi,
} from "../../src/agentbreeder/center/static/lib/gate.js";

const RUN = "20261113-213200-1a2b3c";
const release = (decision, run = RUN, extra = {}) => ({ seq: 1, decision, score: { run, passed: 1, cases: 2 }, ...extra });

test("only a latest release that failed the gate gives its run", () => {
  assert.equal(gateFailedRun([release("eval_failed")]), RUN);
  assert.equal(gateFailedRun([release("promoted"), release("eval_failed")]), null); // a later pass
  assert.equal(gateFailedRun([release("failed")]), null); // not the gate
  assert.equal(gateFailedRun([release("eval_failed", "run-6")]), null); // not a run id
  assert.equal(gateFailedRun([{ seq: 1, decision: "eval_failed", score: null }]), null);
  // A rollback is not a release run: the run before it counts (latestRun).
  assert.equal(gateFailedRun([release("rolled_back"), release("eval_failed")]), RUN);
  assert.equal(gateFailedRun([]), null);
  assert.equal(gateFailedRun(null), null);
});

test("the tail path names the agent, encoded", () => {
  assert.equal(tailApi("0123456789ab", "support"), "/api/projects/0123456789ab/logs/failed/support");
  assert.equal(tailApi("0123456789ab", "../x"), "/api/projects/0123456789ab/logs/failed/..%2Fx");
});

test("each check kind says what was expected and what happened", () => {
  const row = { tools: ["lookup_order", "web_search"], correctness: 0.1 };
  const kase = { id: "c2", reference: "Refunds take 5-10 days.", expect: { sources_any: ["Refund policy", "FAQ"] } };
  assert.deepEqual(expectation("matches:30 days", row), { expected: "The reply matches 30 days", actual: "It does not" });
  assert.deepEqual(expectation("avoids:guarantee", row), { expected: "The reply does not match guarantee", actual: "It does" });
  assert.deepEqual(expectation("called:refund", row), { expected: "Calls refund", actual: "Called lookup_order, web_search" });
  assert.deepEqual(expectation("called:refund", {}), { expected: "Calls refund", actual: "Called no tool" });
  assert.deepEqual(expectation("did_not_call:web_search", row), { expected: "Does not call web_search", actual: "Called lookup_order, web_search" });
  assert.deepEqual(expectation("agent_responded", row), { expected: "A reply", actual: "No reply, or the call failed" });
  assert.deepEqual(expectation("judge_correct", row, kase), { expected: "Refunds take 5-10 days.", actual: "Judged 0.10 (a pass needs 0.70)" });
  assert.equal(expectation("judge_correct", row).expected, `An answer the judge scores at least ${CORRECTNESS_PASS.toFixed(2)}`);
  assert.equal(expectation("judge_correct", {}).actual, "Judged below the pass mark");
  assert.equal(expectation("retrieved_expected_article", row, kase).expected, "Cites one of Refund policy, FAQ in its top 3 sources");
  assert.equal(expectation("retrieved_expected_article", row).expected, "Cites an expected source in its top 3");
  assert.match(expectation("patterns_finished", row).actual, /took too long/);
  // A `#2` suffix (two names equal once capped) still reads as its check.
  assert.equal(expectation("judge_correct#2", row, kase).expected, "Refunds take 5-10 days.");
  assert.deepEqual(expectation("something_new", row), { expected: "", actual: "Failed" });
  // A pattern keeps everything after the first colon, colons included.
  assert.equal(expectation("matches:a:b", row).expected, "The reply matches a:b");
});

test("one row per failed check of each failed case, replies only with content", () => {
  const report = {
    content: true,
    results: [
      { id: "c1", passed: true, attempts: 1, failed_checks: [] },
      { id: "c2", passed: false, failed_checks: ["matches:30 days", "judge_correct"], correctness: 0.2, reason: "wrong", reply: "No idea", tools: [] },
      { id: "c3", passed: false, failed_checks: [], reason: "" },
    ],
  };
  const byId = casesById(parseCases('{"id": "c2", "turns": ["hi"], "reference": "Thirty days, in full."}\n').entries);
  const rows = failureRows(report, byId);
  assert.equal(rows.length, 3);
  assert.deepEqual(rows[0], { key: "0:0", id: "c2", first: true, check: "matches:30 days", expected: "The reply matches 30 days", actual: "It does not", reason: "wrong", reply: "No idea" });
  assert.equal(rows[1].expected, "Thirty days, in full.");
  assert.equal(rows[1].reply, null); // the reply and the edit link once per case
  assert.equal(rows[1].first, false);
  assert.deepEqual(rows[2], { key: "1", id: "c3", first: true, check: null, expected: "", actual: "The case failed with no failed check recorded", reason: "", reply: null });
  // Without --content the reply is never taken, even if a row carries one.
  assert.equal(failureRows({ ...report, content: false }, byId)[0].reply, null);
  assert.deepEqual(failureRows(null), []);
});

test("cases by id keep the first of a repeated id and skip blank lines", () => {
  const parsed = parseCases('{"id": "a", "reference": "1"}\n\n{"id": "a", "reference": "2"}\n{"id": 3}\n');
  const byId = casesById(parsed.entries);
  assert.equal(byId.size, 1);
  assert.equal(byId.get("a").reference, "1");
  assert.equal(casesById(null).size, 0);
});

test("edit links: the case by id when the router takes it, else the eval set", () => {
  assert.deepEqual(evalSetRef("eval/support-evals@1.0.0"), { kind: "eval", name: "support-evals", version: "1.0.0" });
  assert.equal(evalSetRef("prompt/support@1.0.0"), null);
  assert.equal(caseHref("eval/support-evals@1.0.0", "refund-annual"), "#/registry/evals/support-evals/case/refund-annual");
  assert.equal(caseHref("eval/support-evals@1.0.0", "has space"), "#/registry/evals/support-evals");
  assert.equal(caseHref("eval/support-evals@1.0.0", ".."), "#/registry/evals/support-evals");
  assert.equal(caseHref("prompt/x@1.0.0", "a"), null);
  assert.equal(caseHref(null, "a"), null);
  assert.equal(promptHref({ prompt: { ref: "prompt/northwind-support@1.0.1" } }), "#/registry/prompts/northwind-support");
  assert.equal(promptHref({ prompt: { path: "prompts/demo.md" } }), null);
  assert.equal(promptHref({ prompt: { ref: "eval/x@1.0.0" } }), null);
  assert.equal(promptHref(null), null);
});
