import assert from "node:assert/strict";
import { test } from "node:test";

import {
  STEP_WORDS, TABS, agentName, candidateOf, gateStrip, identityText, identityTone, lastReleaseCell, latestRun, listRows, releasedImage, localDate, projectStatus,
  releaseText, releaseTone, releasesFor, shortImage, tabHref,
} from "../../src/agentbreeder/center/static/lib/agents.js";

const RELEASE = { seq: 9, at: "2026-11-13T10:00:00Z", decision: "promoted", score: { run: "r1", passed: 5, cases: 5 }, threshold: 1, image: "agentbreeder/support:abc", problem: null };
const PRODUCTION = { id: "2", name: "support", target: "local", state: "ready", compliance: "compliant", updated: "2026-11-13T10:01:00Z" };

test("a candidate belongs to its agent", () => {
  assert.equal(agentName({ name: "support-candidate" }), "support");
  assert.equal(agentName({ name: "support" }), "support");
  assert.equal(agentName(null), "");
});

test("list rows group by agent, production before its candidate, and drop malformed rows", () => {
  const rows = listRows([
    { id: "3", name: "support-candidate", target: "local" },
    { id: "1", name: "billing", target: "local" },
    { id: "2", name: "support", target: "local" },
    { name: "no-id" },
    null,
  ]);
  assert.deepEqual(rows.map((r) => [r.id, r.agent, r.candidate]), [["1", "billing", false], ["2", "support", false], ["3", "support", true]]);
  assert.deepEqual(listRows(undefined), []);
});

test("a row's project is registered, the default state, or not registered", () => {
  assert.equal(projectStatus({ console_project: "abc", stack: "s-1" }), "registered");
  assert.equal(projectStatus({ console_project: null, stack: null }), "default state");
  assert.equal(projectStatus({ console_project: null, stack: "s-1" }), "not registered");
});

test("identity words and tone never rely on colour alone", () => {
  assert.equal(identityText("compliant"), "compliant");
  assert.equal(identityText("noncompliant:PRINCIPAL_BLOCKED:cleanup_failed"), "noncompliant: PRINCIPAL_BLOCKED, cleanup_failed");
  assert.equal(identityText(null), "not checked");
  assert.equal(identityText("unbound"), "unbound (no principal)");
  assert.equal(identityTone("compliant"), "passed");
  assert.equal(identityTone("noncompliant:X"), "failed");
  assert.equal(identityTone("unverifiable:PROVIDER_ERROR"), "unknown");
});

test("images are shortened for tables", () => {
  assert.equal(shortImage("registry.local/agentbreeder/support:1.0"), "support:1.0");
  assert.equal(shortImage(`a/b@sha256:${"f".repeat(64)}`), `b@${"f".repeat(12)}`);
  assert.equal(shortImage(null), "");
});

test("a release reads as its gate result", () => {
  assert.equal(releaseText(RELEASE), "passed 5/5");
  assert.equal(releaseText({ ...RELEASE, decision: "eval_failed", score: { run: "r", passed: 3, cases: 5 } }), "eval failed 3/5");
  assert.equal(releaseText({ decision: "failed", score: null, problem: { code: "EVAL_RUN_FAILED" } }), "failed (EVAL_RUN_FAILED)");
  assert.equal(releaseText({ decision: "incomplete" }), "no outcome recorded");
  assert.equal(releaseText({ decision: "rolled_back" }), "rolled back");
  assert.equal(releaseText({ decision: "rollback_failed" }), "rollback failed");
  assert.equal(releaseText(null), "not released");
  assert.equal(releaseTone(RELEASE), "passed");
  assert.equal(releaseTone({ decision: "incomplete" }), "neutral");
  assert.equal(releaseTone({ decision: "failed" }), "failed");
});

test("the newest gate run skips rollbacks", () => {
  const rollback = { seq: 10, decision: "rolled_back", image: "old" };
  assert.equal(latestRun([rollback, RELEASE]), RELEASE);
  assert.equal(latestRun([rollback]), null);
  assert.equal(latestRun(undefined), null);
});

test("releases are only the project's own agent's", () => {
  const data = { agent: "support", releases: [RELEASE], truncated: false };
  assert.deepEqual(releasesFor(data, "support"), { entries: [RELEASE], own: true, truncated: false });
  assert.deepEqual(releasesFor(data, "billing"), { entries: [], own: false, truncated: false });
  assert.deepEqual(releasesFor(null, "support"), { entries: [], own: false, truncated: false });
});

test("the list's Last release cell", () => {
  const row = { name: "support", console_project: "p1", candidate: false };
  const view = { data: { agent: "support", releases: [RELEASE], truncated: false } };
  assert.deepEqual(lastReleaseCell(row, view), { text: "passed 5/5", tone: "passed", at: RELEASE.at });
  assert.equal(lastReleaseCell({ ...row, name: "other" }, view).text, "unknown");
  assert.equal(lastReleaseCell({ ...row, candidate: true }, view).text, "—");
  assert.equal(lastReleaseCell({ ...row, console_project: null }, view).text, "unknown");
  assert.equal(lastReleaseCell(row, null).text, "…");
  assert.equal(lastReleaseCell(row, { error: true }).text, "unavailable");
  assert.equal(lastReleaseCell(row, { data: { agent: "support", releases: [], truncated: false } }).text, "not released");
  assert.equal(lastReleaseCell(row, { data: { agent: "support", releases: [], truncated: true } }).text, "none in the newest audit rows");
});

test("the candidate row is found by name", () => {
  const rows = [PRODUCTION, { id: "3", name: "support-candidate", state: "starting" }];
  assert.equal(candidateOf(rows, "support").id, "3");
  assert.equal(candidateOf(rows, "billing"), null);
  assert.equal(candidateOf(null, "support"), null);
});

test("the gate strip of a released, compliant agent passes every step", () => {
  const steps = gateStrip({ run: RELEASE, production: { ...PRODUCTION, image: RELEASE.image } });
  assert.deepEqual(steps.map((s) => s.label), ["Build", "Candidate", "Evals 5/5", "Identity", "Production"]);
  assert.ok(steps.every((s) => s.state === "passed"), JSON.stringify(steps));
  assert.match(steps[2].detail, /needs 100%/);
});

test("a failed eval fails the Evals step and leaves production as it is", () => {
  const steps = gateStrip({ run: { ...RELEASE, decision: "eval_failed", score: { run: "r2", passed: 2, cases: 5 } }, production: PRODUCTION });
  assert.equal(steps[2].label, "Evals 2/5");
  assert.equal(steps[2].state, "failed");
  assert.equal(steps[4].state, "passed");
});

test("an eval run that failed fails only Evals: the build and the candidate ran", () => {
  const steps = gateStrip({ run: { decision: "failed", score: null, image: "agentbreeder/support:abc", problem: { code: "EVAL_RUN_FAILED" } } });
  assert.deepEqual(steps.map((s) => s.state), ["passed", "passed", "failed", "none", "none"]);
  assert.equal(steps[2].detail, "The eval run failed (EVAL_RUN_FAILED)");
});

test("a run with no outcome leaves Candidate and Evals not run", () => {
  const steps = gateStrip({ run: { decision: "incomplete", score: null, image: "img" } });
  assert.deepEqual(steps.slice(0, 3).map((s) => s.state), ["passed", "none", "none"]);
  assert.match(steps[1].detail, /No outcome recorded/);
});

test("production on another image than the last release or rollback put there differs", () => {
  const steps = gateStrip({ run: RELEASE, released: RELEASE.image, production: { ...PRODUCTION, image: "agentbreeder/support:plain-deploy" } });
  assert.equal(steps[4].state, "differs");
  assert.match(steps[4].detail, /not the image the last release or rollback put there/);
  assert.equal(STEP_WORDS.differs, "differs from the last release");
});

test("a failed or unfinished run after a promotion does not make production differ", () => {
  const promoted = { ...RELEASE, seq: 5, image: "img:a" };
  for (const decision of ["eval_failed", "incomplete", "failed"]) {
    const newer = { seq: 6, decision, image: "img:b", score: decision === "eval_failed" ? { run: "r", passed: 1, cases: 5 } : null };
    const entries = [newer, promoted];
    assert.equal(releasedImage(entries), "img:a");
    const steps = gateStrip({ run: latestRun(entries), released: releasedImage(entries), production: { ...PRODUCTION, image: "img:a" } });
    assert.equal(steps[4].state, "passed", decision);
  }
  const rolled = [{ seq: 9, decision: "rolled_back", image: "img:old" }, promoted];
  assert.equal(releasedImage(rolled), "img:old");
  assert.equal(releasedImage([]), null);
});

test("a release that failed at promotion says so on Production", () => {
  const run = { ...RELEASE, decision: "failed", problem: null };
  const steps = gateStrip({ run, released: "img:a", production: { ...PRODUCTION, image: "img:a" } });
  assert.equal(steps[2].state, "passed");
  assert.match(steps[4].detail, /failed after its evals passed/);
});

test("an agent deployed without release shows the release steps as not run", () => {
  const steps = gateStrip({ production: { ...PRODUCTION, compliance: "noncompliant:PRINCIPAL_CHANGED" } });
  assert.deepEqual(steps.slice(0, 3).map((s) => s.state), ["none", "none", "none"]);
  assert.equal(steps[3].state, "failed");
  assert.equal(steps[3].detail, "noncompliant: PRINCIPAL_CHANGED");
});

test("a running candidate and a starting production read as in progress", () => {
  const steps = gateStrip({ run: RELEASE, production: { ...PRODUCTION, state: "starting" }, candidate: { state: "ready" } });
  assert.equal(steps[1].state, "running");
  assert.equal(steps[4].state, "running");
  assert.equal(STEP_WORDS.running, "in progress");
  assert.equal(gateStrip({ production: { ...PRODUCTION, state: "failed" } })[4].state, "failed");
  assert.deepEqual(gateStrip().map((s) => s.state), ["none", "none", "none", "none", "none"]);
});

test("the local date is the browser's, not UTC's", () => {
  assert.equal(localDate(new Date(2026, 0, 5, 23, 30)), "2026-01-05");
});

test("tabs are Overview, Try it, Releases and Logs, and Try it keeps its address", () => {
  assert.deepEqual(TABS.map(([, label]) => label), ["Overview", "Try it", "Releases", "Logs"]);
  assert.equal(tabHref("support", "overview"), "#/agents/support");
  assert.equal(tabHref("support", "try"), "#/agents/support/try");
  assert.throws(() => tabHref("../x", "try"));
});
