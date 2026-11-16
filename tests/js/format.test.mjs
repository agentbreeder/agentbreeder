import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CAUSE_WORDS, auditTime, incompleteSentence, isCandidate, isNorthwind, jobFailure, jobLabel, jobOutcome, portsText,
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

test("a services-up job refused for changed pins says to review the repin again (#964)", () => {
  const lines = [{ seq: 1, text: "Starting" }, { seq: 2, text: "[PINS_CHANGED] the MCP tool definitions or pins differ" }];
  assert.deepEqual(jobOutcome({ kind: "services-up", state: "failed", exit_code: 2, lines }), { tone: "warn", text: "The MCP tool definitions changed since your review: pins unchanged, the rest of Start ran. Review the repin again." });
  assert.match(jobOutcome({ kind: "services-up", state: "failed", exit_code: 2, lines: [{ seq: 1, text: "x [PINS_CHANGED] y" }, null] }).text, /^Start services failed|failed/);
  assert.match(jobOutcome({ kind: "services-up", state: "failed", exit_code: 1 }).text, /failed/);
});

test("a coded failure names its code, cause and the CLI's next step (#1042)", () => {
  const probe = { kind: "services-up", state: "failed", exit_code: 2, failure_code: "MCP_PROBE_FAILED", failure_message: "the github relay couldn't list upstream tools (UPSTREAM_AUTH); see `agentbreeder services logs mcp-github`" };
  const outcome = jobOutcome(probe);
  assert.equal(outcome.tone, "error");
  assert.match(outcome.text, /^Start services failed \(exit 2\): MCP_PROBE_FAILED \(cause UPSTREAM_AUTH\)\. the github relay/);
  assert.match(outcome.text, /agentbreeder services logs mcp-github`\./);
  assert.ok(outcome.text.endsWith(CAUSE_WORDS.UPSTREAM_AUTH));
  assert.deepEqual(jobFailure(probe), { code: "MCP_PROBE_FAILED", message: probe.failure_message, cause: "UPSTREAM_AUTH" });
  const release = { kind: "release", state: "failed", exit_code: 3, failure_code: "DEPLOYER_UNVERIFIED", failure_message: "A verified deployer identity is required (agentbreeder login); production unchanged" };
  assert.equal(jobOutcome(release).text, "The release failed: DEPLOYER_UNVERIFIED. A verified deployer identity is required (agentbreeder login); production unchanged.");
  // The eval gate keeps its own words; a cause with no fixed words is named only.
  assert.equal(jobOutcome({ ...release, exit_code: 5 }).text, "The eval gate failed. Production is unchanged.");
  assert.equal(jobOutcome({ kind: "build", state: "failed", exit_code: 2, failure_code: "X_Y", failure_message: "set (HOME) first." }).text, "Build failed (exit 2): X_Y. set (HOME) first.");
  assert.equal(jobOutcome({ ...probe, failure_message: "the x relay couldn't list upstream tools (NEW_CAUSE)" }).text, "Start services failed (exit 2): MCP_PROBE_FAILED (cause NEW_CAUSE). the x relay couldn't list upstream tools (NEW_CAUSE).");
});

test("only a plain code from a failed job is a failure (#1042)", () => {
  assert.equal(jobFailure({ state: "failed", failure_code: "lower", failure_message: "m" }), null);
  assert.equal(jobFailure({ state: "failed", failure_code: null }), null);
  assert.equal(jobFailure({ state: "succeeded", failure_code: "PORT_BUSY", failure_message: "m" }), null);
  assert.equal(jobFailure(null), null);
  assert.deepEqual(jobFailure({ state: "failed", failure_code: "PORT_BUSY", failure_message: 7 }), { code: "PORT_BUSY", message: "", cause: null });
  assert.equal(jobFailure({ state: "failed", failure_code: "PORT_BUSY", failure_message: "x".repeat(500) }).message.length, 300);
  assert.equal(jobOutcome({ kind: "remove", state: "failed", exit_code: 2, failure_code: "__proto__" }).text, "Remove failed (exit 2). See its log.");
});
