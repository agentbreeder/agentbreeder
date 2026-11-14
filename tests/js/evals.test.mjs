import assert from "node:assert/strict";
import { test } from "node:test";

import {
  RUN_ID, allPassed, caseState, isAgentName, failedCases, listApi, orderedCases, percent, reportApi, reportHref, scoreText,
} from "../../src/agentbreeder/center/static/lib/evals.js";

const RUN = "20261113-213200-1a2b3c";

test("the run id rule matches eval_reports.RUN", () => {
  assert.ok(RUN_ID.test(RUN));
  for (const bad of ["20261113-213200-1A2B3C", "2026111-213200-1a2b3c", `${RUN}x`, "../x", "", "20261113_213200_1a2b3c"]) {
    assert.ok(!RUN_ID.test(bad), bad);
  }
});

test("the report path carries only a checked agent and the content flag", () => {
  assert.equal(reportApi("0123456789ab", RUN), `/api/projects/0123456789ab/evals/${RUN}`);
  assert.equal(reportApi("0123456789ab", RUN, { agent: "support", content: true }), `/api/projects/0123456789ab/evals/${RUN}?agent=support&content=1`);
  // A bad agent is dropped, never sent.
  assert.equal(reportApi("0123456789ab", RUN, { agent: "../x&content=1" }), `/api/projects/0123456789ab/evals/${RUN}`);
  assert.throws(() => reportApi("0123456789ab", "../../audit"), /bad eval run id/);
});

test("the list path", () => {
  assert.equal(listApi("0123456789ab"), "/api/projects/0123456789ab/evals");
  assert.equal(listApi("0123456789ab", { agent: "support" }), "/api/projects/0123456789ab/evals?agent=support");
  assert.equal(listApi("0123456789ab", { agent: "Support" }), "/api/projects/0123456789ab/evals");
});

test("the viewer's route, with or without the agent", () => {
  assert.equal(reportHref(RUN), `#/audit/evals/${RUN}`);
  assert.equal(reportHref(RUN, "support"), `#/audit/evals/${RUN}/support`);
  assert.equal(reportHref(RUN, "Bad Name"), null);
  assert.equal(reportHref("run-1", "support"), null);
  assert.equal(reportHref(null), null);
});

test("a case is failed, flaky or passed", () => {
  assert.equal(caseState({ passed: false, attempts: 1 }), "failed");
  assert.equal(caseState({ passed: "true" }), "failed");
  assert.equal(caseState(null), "failed");
  assert.equal(caseState({ passed: true, attempts: 2 }), "flaky");
  assert.equal(caseState({ passed: true, attempts: 1 }), "passed");
  assert.equal(caseState({ passed: true }), "passed");
});

test("cases come failed first, then flaky, then passed, each in report order", () => {
  const report = {
    results: [
      { id: "a", passed: true, attempts: 1 },
      { id: "b", passed: false, attempts: 1 },
      "garbage",
      { id: "c", passed: true, attempts: 3 },
      { id: "d", passed: false, attempts: 2 },
    ],
  };
  assert.deepEqual(orderedCases(report).map((row) => row.id), ["b", "d", "c", "a"]);
  assert.deepEqual(failedCases(report).map((row) => row.id), ["b", "d"]);
  assert.deepEqual(orderedCases(null), []);
  assert.deepEqual(failedCases({ results: "x" }), []);
});

test("the score in words", () => {
  assert.equal(scoreText({ cases: 2, passed: 1, pass_rate: 0.5 }), "1 of 2 cases passed (50%)");
  assert.equal(scoreText({ cases: 1, passed: 1, pass_rate: 1 }), "1 of 1 case passed (100%)");
  assert.equal(scoreText({ cases: 40, passed: 39, pass_rate: 0.975 }), "39 of 40 cases passed (97.5%)");
  assert.equal(scoreText({ cases: 2, passed: 1, pass_rate: "x" }), "1 of 2 cases passed");
  assert.equal(scoreText({ cases: "2", passed: 1 }), "Score not recorded");
  assert.equal(scoreText(null), "Score not recorded");
});

test("percent only for a rate", () => {
  assert.equal(percent(0), "0%");
  assert.equal(percent(2 / 3), "66.7%");
  for (const bad of [-0.1, 1.1, NaN, Infinity, "0.5", null]) assert.equal(percent(bad), "");
});

test("an agent name follows the CLI's rule", () => {
  for (const good of ["support", "a", "a-b", "a".repeat(53)]) assert.ok(isAgentName(good), good);
  for (const bad of ["Support", "-a", "a-", "a_b", "a".repeat(54), "my.agent", null, 7]) assert.ok(!isAgentName(bad), String(bad));
});

test("all passed only when the saved header and the readable cases agree", () => {
  const ok = { cases: 2, passed: 2, results: [{ passed: true }, { passed: true, attempts: 2 }] };
  assert.equal(allPassed(ok), true);
  assert.equal(allPassed({ ...ok, passed: 1 }), false); // a case skipped as malformed failed
  assert.equal(allPassed({ ...ok, results: [{ passed: false }] }), false);
  assert.equal(allPassed({ cases: 0, passed: 0, results: [] }), false);
  assert.equal(allPassed({ cases: "2", passed: 2 }), false);
  assert.equal(allPassed(null), false);
});
