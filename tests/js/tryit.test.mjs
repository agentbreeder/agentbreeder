import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_MESSAGE, MAX_TURNS, addPending, canTry, costText, customerProblem, emptyConversation, failureText, latencyText,
  messageProblem, namesText, pending, productionRow, settle, thrownEnvelope, tokensText, unavailable,
} from "../../src/agentbreeder/center/static/lib/tryit.js";

const ROWS = [
  { id: "1", name: "support-candidate", target: "local", state: "ready" },
  { id: "2", name: "support", target: "local", state: "ready" },
  { id: "3", name: "billing", target: "kubernetes", state: "ready" },
  { id: "4", name: "slow", target: "local", state: "starting" },
];
const TURN = { reply: "Hi", session: "s-1", tools: [], sources: [], tokens: { prompt: 1, output: 2 }, latency_ms: 5 };

test("only production rows are tried, never a candidate", () => {
  assert.equal(productionRow(ROWS, "support").id, "2");
  assert.equal(productionRow(ROWS, "support-candidate"), null);
  assert.equal(productionRow(null, "support"), null);
  assert.equal(canTry(ROWS[1]), true);
  assert.equal(canTry(ROWS[0]), false);
  assert.equal(canTry(ROWS[2]), false);
  assert.equal(canTry(ROWS[3]), false);
  assert.equal(canTry(null), false);
});

test("an unavailable deployment says why in words", () => {
  assert.match(unavailable(null, "x"), /no production deployment/);
  assert.match(unavailable(ROWS[2], "billing"), /runs on kubernetes/);
  assert.match(unavailable(ROWS[3], "slow"), /starting, not ready/);
  assert.equal(unavailable(ROWS[1], "support"), null);
});

test("messages and customer IDs are checked before sending", () => {
  assert.equal(messageProblem("  "), "Type a message first.");
  assert.match(messageProblem("x".repeat(MAX_MESSAGE + 1)), /limited to 4000/);
  assert.equal(messageProblem(` ${"x".repeat(MAX_MESSAGE)} `), null);
  assert.equal(customerProblem(""), null);
  assert.equal(customerProblem("C-1001"), null);
  assert.match(customerProblem("a b"), /1 to 64/);
  assert.match(customerProblem("x".repeat(65)), /1 to 64/);
});

test("a reply settles its turn and continues the session", () => {
  let [conversation, id] = addPending(emptyConversation(), "hello");
  assert.equal(pending(conversation), true);
  conversation = settle(conversation, id, { data: TURN, error: null });
  assert.equal(pending(conversation), false);
  assert.equal(conversation.turns[0].state, "reply");
  assert.equal(conversation.turns[0].turn.reply, "Hi");
  assert.equal(conversation.session, "s-1");
});

test("a failure keeps the session and says whether it was sent", () => {
  let conversation = { ...emptyConversation(), session: "s-1" };
  let id;
  [conversation, id] = addPending(conversation, "hello");
  conversation = settle(conversation, id, { data: { sent: false }, error: { code: "NOT_READY", message: "not ready" } });
  const turn = conversation.turns[0];
  assert.equal(turn.state, "failed");
  assert.deepEqual(turn.problem, { code: "NOT_READY", message: "not ready", sent: false });
  assert.equal(conversation.session, "s-1");
  assert.equal(failureText(turn.problem), "Not sent: not ready [NOT_READY].");
  assert.match(failureText({ code: "TIMEOUT", message: "timed out", sent: true }), /second turn/);
  // Unknown counts as sent: a retry may be a second turn.
  [conversation, id] = addPending(conversation, "again");
  conversation = settle(conversation, id, { data: null, error: { code: "UNREACHABLE", message: "gone" } });
  assert.equal(conversation.turns[1].problem.sent, true);
  // A reply without text is a failure, and a bad session id never replaces the session.
  [conversation, id] = addPending(conversation, "odd");
  conversation = settle(conversation, id, { data: { ...TURN, reply: null, session: "a b" }, error: null });
  assert.equal(conversation.turns[2].state, "failed");
  assert.equal(conversation.session, "s-1");
});

test("conversations keep the newest turns", () => {
  let conversation = emptyConversation();
  for (let i = 0; i < MAX_TURNS + 5; i += 1) [conversation] = addPending(conversation, `m${i}`);
  assert.equal(conversation.turns.length, MAX_TURNS);
  assert.equal(conversation.turns[0].message, "m5");
});

test("the inspector's words", () => {
  assert.equal(latencyText(850), "850 ms");
  assert.equal(latencyText(2400), "2.4 s");
  assert.equal(latencyText(65000), "1 min 5 s");
  assert.equal(latencyText(undefined), "unknown");
  assert.equal(tokensText({ prompt: 1812, output: 64 }), "1,812 in, 64 out");
  assert.equal(tokensText({ prompt: null, output: 3 }), "? in, 3 out");
  assert.equal(tokensText({ prompt: null, output: null }), "not reported");
  assert.equal(tokensText(undefined), "not reported");
  assert.equal(costText(0.000444), "$0.000444");
  assert.equal(costText(0.0444), "$0.0444");
  assert.equal(costText(1.5), "$1.5000");
  assert.equal(costText(0), "$0");
  assert.equal(costText(0.0000001), "under $0.000001");
  assert.equal(costText(0.000000999), "under $0.000001");
  assert.equal(costText(0.000001), "$0.000001");
  assert.equal(costText(0.0099999), "$0.010000");
  assert.equal(costText(0.01), "$0.0100");
  for (const odd of [null, undefined, -1, Number.NaN, Infinity, "0.1", {}]) assert.equal(costText(odd), null);
  assert.equal(namesText(["orders", "kb"]), "orders, kb");
  assert.equal(namesText([]), "none");
});

test("a name on two targets: the local row is the one tried", () => {
  const both = [{ id: "k", name: "support", target: "kubernetes", state: "ready" }, { id: "l", name: "support", target: "local", state: "ready" }];
  assert.equal(productionRow(both, "support").id, "l");
  assert.equal(unavailable(productionRow(both, "support"), "support"), null);
  assert.equal(productionRow([both[0]], "support").id, "k");
});

test("a request the console refused (4xx) was not sent; no answer or a 5xx is unknown", () => {
  for (const status of [400, 403, 404, 409, 413]) {
    const envelope = thrownEnvelope({ kind: "http", status, code: "TRY_BUSY", message: "busy" });
    assert.deepEqual(envelope, { data: { sent: false }, error: { code: "TRY_BUSY", message: "busy" } });
    const [conversation, id] = addPending(emptyConversation(), "hi");
    assert.equal(settle(conversation, id, envelope).turns[0].problem.sent, false, String(status));
  }
  for (const error of [{ kind: "http", status: 500, message: "x" }, { kind: "unreachable", message: "gone" }, { kind: "session", status: 401, message: "Sign in" }]) {
    const envelope = thrownEnvelope(error);
    assert.equal(envelope.data, null);
    const [conversation, id] = addPending(emptyConversation(), "hi");
    assert.equal(settle(conversation, id, envelope).turns[0].problem.sent, true);
  }
  assert.equal(thrownEnvelope({ kind: "unreachable", message: "gone" }).error.code, "UNREACHABLE");
});
