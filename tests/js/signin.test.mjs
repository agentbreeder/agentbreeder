import assert from "node:assert/strict";
import { test } from "node:test";

import { backoffSeconds, createDrafts, DRAFT_PREFIX, eventTime, eventWords, hoursLabel, newPasswordProblem } from "../../src/agentbreeder/center/static/lib/signin.js";

test("a new password the form can refuse before asking the server", () => {
  assert.equal(newPasswordProblem("", ""), "Enter a password.");
  assert.equal(newPasswordProblem("short", "short"), "Use at least 12 characters.");
  assert.equal(newPasswordProblem("a long passphrase", "a long passphrasf"), "The two passwords differ.");
  assert.equal(newPasswordProblem("a long passphrase", "a long passphrase"), null);
});

test("the backoff wait comes from the 429 reply, capped at five minutes", () => {
  assert.equal(backoffSeconds(null), 0);
  assert.equal(backoffSeconds({ code: "BAD_CREDENTIALS" }), 0);
  assert.equal(backoffSeconds({ code: "BACKOFF", data: { retry_after: 60 } }), 60);
  assert.equal(backoffSeconds({ code: "BACKOFF", data: { retry_after: 9999 } }), 300);
  assert.equal(backoffSeconds({ code: "BACKOFF", data: { retry_after: "x" } }), 30);
  assert.equal(backoffSeconds({ code: "BACKOFF", data: null }), 30);
});

test("security events in words; unknown values as they are", () => {
  assert.deepEqual(eventWords({ event: "sign-in", outcome: "failed", source: "browser" }), { event: "Sign-in with the password", outcome: "Wrong password", source: "Browser" });
  assert.deepEqual(eventWords({ event: "password-reset", outcome: "ok", source: "terminal" }), { event: "Password reset", outcome: "Succeeded", source: "Terminal" });
  assert.deepEqual(eventWords({ event: "new-kind", outcome: "backoff", source: "x" }), { event: "new-kind", outcome: "Refused: too many attempts", source: "x" });
  assert.deepEqual(eventWords({ event: "toString", outcome: undefined, source: null }), { event: "toString", outcome: "", source: "" });
});

test("event times and hours", () => {
  assert.equal(eventTime("x"), "");
  assert.equal(eventTime(0), "");
  assert.ok(eventTime(1_700_000_000, "en-US").includes("2023"));
  assert.equal(hoursLabel(1), "1 hour");
  assert.equal(hoursLabel(8), "8 hours");
});

test("drafts live under one prefix in the tab's storage", () => {
  const store = new Map();
  const storage = { get: (k) => store.get(k) ?? null, set: (k, v) => (v === null ? store.delete(k) : store.set(k, v)) };
  const drafts = createDrafts(storage);
  assert.equal(drafts.get("p:prompt/x@1.0.0:prompt.md"), null);
  drafts.set("p:prompt/x@1.0.0:prompt.md", "text");
  assert.deepEqual([...store.keys()], [`${DRAFT_PREFIX}p:prompt/x@1.0.0:prompt.md`]);
  assert.equal(drafts.get("p:prompt/x@1.0.0:prompt.md"), "text");
  drafts.clear("p:prompt/x@1.0.0:prompt.md");
  assert.equal(store.size, 0);
  // clearAll (sign-out) removes every draft and nothing else.
  const listing = { ...storage, keys: () => [...store.keys()] };
  const all = createDrafts(listing);
  all.set("a", "1");
  all.set("b", "2");
  store.set("console-session", "token");
  all.clearAll();
  assert.deepEqual([...store.keys()], ["console-session"]);
  createDrafts(storage).clearAll(); // a storage that cannot list keys: nothing to do
});
