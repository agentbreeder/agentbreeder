import assert from "node:assert/strict";
import { test } from "node:test";

import { runtimeStatus } from "../../src/agentbreeder/center/static/lib/builder.js";
import {
  RESULT_WORDS, checkGroups, countsText, groupText, imageLabel, imagesFor, notCurrent, runProblem, shortDigest,
} from "../../src/agentbreeder/center/static/lib/conformance.js";
import { jobOutcome } from "../../src/agentbreeder/center/static/lib/format.js";

const IMAGE = `sha256:${"c".repeat(64)}`;
const OTHER = `sha256:${"d".repeat(64)}`;
const REF = "runtime/adk@1.4.0";

const report = {
  image: IMAGE,
  result: "incomplete",
  counts: { pass: 2, fail: 1, not_run: 1, not_applicable: 0 },
  checks: [
    { id: "serving.health", group: "Serving", status: "pass" },
    { id: "serving.host", group: "Serving", status: "pass" },
    { id: "sessions.busy", group: "Sessions", status: "fail" },
    { id: "knowledge.sources", group: "Knowledge", status: "not_run" },
  ],
};

test("checks are grouped in order, and a group with a failure is open", () => {
  const groups = checkGroups(report);
  assert.deepEqual(groups.map((group) => [group.group, group.checks.length, group.failed, group.open]), [
    ["Serving", 2, 0, false],
    ["Sessions", 1, 1, true],
    ["Knowledge", 1, 0, false],
  ]);
  assert.equal(groupText(groups[0]), "Serving: 2 checks");
  assert.equal(groupText(groups[1]), "Sessions: 1 check, 1 failed");
  assert.deepEqual(checkGroups(null), []);
});

test("results and counts in words; incomplete is not a pass", () => {
  assert.equal(RESULT_WORDS.incomplete, "Incomplete: not a pass");
  assert.equal(countsText(report.counts), "2 passed, 1 failed, 1 not run, 0 not applicable");
  assert.equal(countsText(null), "");
});

test("a runtime's status names an incomplete report as such (#1085 S3)", () => {
  const entry = { verified: false };
  assert.equal(runtimeStatus({ ...entry, conformance: { passed: false, result: "incomplete" } }), "Conformance incomplete; not run end to end");
  assert.equal(runtimeStatus({ ...entry, conformance: { passed: false, result: "fail" } }), "Conformance failed; not run end to end");
  assert.equal(runtimeStatus({ ...entry, conformance: { passed: false } }), "Conformance failed; not run end to end");
  assert.equal(runtimeStatus({ ...entry, conformance: { passed: true, result: "pass" } }), "Conformance passed; not run end to end");
  assert.equal(runtimeStatus({ verified: true, conformance: { passed: false, result: "incomplete" } }), "Run end to end (Northwind)");
});

test("the project's images of a runtime version, and whether the report is for the current build", () => {
  const images = [
    { agent: "support", image: IMAGE, runtime: REF, built: "2026-11-18T10:00:00Z" },
    { agent: "other", image: OTHER, runtime: "runtime/adk@1.3.0", built: "2026-11-18T11:00:00Z" },
  ];
  const mine = imagesFor(images, REF);
  assert.deepEqual(mine.map((image) => image.agent), ["support"]);
  assert.equal(imageLabel(mine[0]), "support, recorded 2026-11-18 10:00 UTC, sha256:cccccccccccc");
  assert.equal(notCurrent(report, mine), false);
  assert.equal(notCurrent({ ...report, image: OTHER }, mine), true);
  assert.equal(notCurrent(report, []), false);
  assert.equal(shortDigest(IMAGE), "sha256:cccccccccccc");
  assert.match(runProblem([], null), /^No declared-mode image of this runtime version in this project\./);
  assert.equal(runProblem(mine, OTHER), "Choose an image.");
  assert.equal(runProblem(mine, IMAGE), false);
  assert.deepEqual(imagesFor(null, REF), []);
});

test("a conformance run that wrote a failed or incomplete report is a warning, not a failure", () => {
  const ended = { kind: "conformance", state: "failed", exit_code: 1, lines: [] };
  assert.deepEqual(jobOutcome(ended), { tone: "warn", text: "Conformance ran. The report failed or is incomplete (not a pass): see the runtime's Conformance." });
  assert.equal(jobOutcome({ ...ended, state: "succeeded", exit_code: 0 }).text, "Conformance run finished.");
  assert.equal(jobOutcome({ ...ended, exit_code: 2, lines: [{ text: "[CONFORMANCE_ENVIRONMENT] Docker refused" }] }).tone, "error");
  // Exit 1 with the CLI's coded error line is a failed run, not a report.
  assert.equal(jobOutcome({ ...ended, failure_code: "USAGE", failure_message: "bad flag" }).tone, "error");
});
