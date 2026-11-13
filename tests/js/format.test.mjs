import assert from "node:assert/strict";
import { test } from "node:test";

import {
  auditTime, incompleteSentence, isCandidate, isNorthwind, jobLabel, jobOutcome, portsText,
  reasonCodes, serviceState, serviceUp, toolGuidance, verifySentence,
} from "../../src/agentbreeder/center/static/lib/format.js";

test("verify in words, intact and broken", () => {
  assert.deepEqual(verifySentence({ ok: true, rows: 19, head: "63639e0123456789", incomplete_attempts: [] }), { ok: true, text: "Chain intact: 19 rows checked, head 63639e012345…" });
  assert.equal(verifySentence({ ok: true, rows: 1, head: "" }).text, "Chain intact: 1 row checked.");
  const broken = verifySentence({ ok: false, rows: 4, first_invalid_seq: 5, problem: "hash mismatch" });
  assert.equal(broken.ok, false);
  assert.equal(broken.text, "Chain broken at row 5: hash mismatch. 4 rows before it check out.");
  assert.equal(verifySentence(null).ok, null);
});

test("raw verify keys never reach the sentence", () => {
  const text = verifySentence({ ok: true, rows: 2, head: "ab", first_invalid_seq: null, incomplete_attempts: [] }).text;
  assert.ok(!/first_invalid_seq|incomplete_attempts|true|null/.test(text), text);
});

test("incomplete attempts", () => {
  assert.equal(incompleteSentence({ incomplete_attempts: [] }), null);
  assert.equal(incompleteSentence({}), null);
  assert.equal(incompleteSentence({ incomplete_attempts: ["0123456789abcdef"] }), "1 attempt has no final row: 01234567.");
  assert.equal(incompleteSentence({ incomplete_attempts: ["aaaaaaaaaa", "bbbbbbbbbb"] }), "2 attempts have no final row: aaaaaaaa, bbbbbbbb.");
});

test("audit time comes from `at` (#811 item 1)", () => {
  const text = auditTime("2026-11-11T18:02:03+00:00", "en-US");
  assert.match(text, /2026/);
  assert.match(text, /Nov/);
  assert.equal(auditTime("", "en-US"), "");
  assert.equal(auditTime("not a time", "en-US"), "not a time");
});

test("reason codes", () => {
  assert.equal(reasonCodes({ reasons: [{ code: "IDENTITY_OK" }, { code: "POLICY_ALLOW" }, {}] }), "IDENTITY_OK, POLICY_ALLOW");
  assert.equal(reasonCodes({ reasons: null }), "");
});

test("candidates and ports", () => {
  assert.equal(isCandidate({ name: "support-candidate" }), true);
  assert.equal(isCandidate({ name: "support" }), false);
  assert.equal(isCandidate(null), false);
  assert.equal(portsText({ host_ports: {} }), "internal only");
  assert.equal(portsText({}), "internal only");
  assert.equal(portsText({ state: "running", host_ports: { "4000": 4000 } }), "4000 → 127.0.0.1:4000");
  assert.equal(portsText({ state: "absent", host_ports: { pg: 65534 } }), "not listening (last: pg → 127.0.0.1:65534)");
  assert.equal(portsText({ state: "exited", host_ports: {} }), "internal only");
  assert.equal(serviceUp({ state: "running" }), true);
  assert.equal(serviceUp({ state: "paused" }), true);
  assert.equal(serviceUp({ state: "absent" }), false);
  assert.equal(serviceUp({ state: "exited" }), false);
  assert.equal(serviceUp(null), false);
  assert.equal(serviceState({ state: "running", health: "healthy" }), "running (healthy)");
  assert.equal(serviceState({ state: "exited" }), "exited");
});

test("Northwind's cost figures only for the onboarded Northwind", () => {
  assert.equal(isNorthwind({ name: "northwind", path: "/Users/a/agentbreeder-projects/northwind" }), true);
  assert.equal(isNorthwind({ name: "northwind", path: "/Users/a/code/northwind" }), false);
  assert.equal(isNorthwind({ name: "support", path: "/Users/a/agentbreeder-projects/northwind" }), false);
  assert.equal(isNorthwind(null), false);
});

test("job outcomes", () => {
  assert.equal(jobOutcome({ state: "running" }), null);
  assert.deepEqual(jobOutcome({ kind: "build", state: "succeeded" }), { tone: "ok", text: "Build finished." });
  assert.equal(jobOutcome({ kind: "release", state: "failed", exit_code: 5 }).text, "The eval gate failed. Production is unchanged.");
  assert.equal(jobOutcome({ kind: "release", state: "failed", exit_code: 1 }).text, "The release failed. See its log.");
  assert.equal(jobOutcome({ kind: "promote", state: "failed", exit_code: 5 }).tone, "error");
  assert.equal(jobOutcome({ kind: "remove", state: "failed", exit_code: 2 }).text, "Remove failed (exit 2). See its log.");
  assert.equal(jobOutcome({ kind: "release", state: "waiting_approval" }).tone, "warn");
  assert.match(jobOutcome({ kind: "release", state: "interrupted" }).text, /leftover candidate/);
  assert.equal(jobLabel("nope"), "nope");
});

test("tool guidance names the install command and the restart", () => {
  const text = toolGuidance("kind", { install: "brew install kind" });
  assert.match(text, /brew install kind/);
  assert.match(text, /restart agentbreeder up/);
  assert.match(toolGuidance("kubectl", {}), /not on the console's PATH/);
});
