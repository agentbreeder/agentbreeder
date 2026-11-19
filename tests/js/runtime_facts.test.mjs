// runtime/adk@1.3.0 and 1.4.0 in the console (#1062): failed model calls in Try it, a turn's and a
// trace's route facts (fallbacks, unknown tools, cached prompt tokens, a guardrail block), and
// cached prompt tokens in the cost views.
import assert from "node:assert/strict";
import { test } from "node:test";

import { agentCost } from "../../src/agentbreeder/center/static/lib/cost.js";
import { traceLine } from "../../src/agentbreeder/center/static/lib/traces.js";
import {
  MODEL_CODES, addPending, correlationText, emptyConversation, failureText, fallbackText, guardrailText, settle, tokensText,
  traceFlags, traceSummary, unknownToolsText,
} from "../../src/agentbreeder/center/static/lib/tryit.js";

const CORRELATION = "e".repeat(32);

function failed(error, data) {
  const [conversation, id] = addPending(emptyConversation(), "hi");
  return settle(conversation, id, { error, data }).turns[0].problem;
}

test("a failed model call keeps its code, the runtime's next step and its correlation id", () => {
  const message = "The agent answered 429: The gateway's rate limit for this agent was reached (retry in 7 s). Wait and send the message again.";
  const problem = failed({ code: "MODEL_RATE_LIMITED", message }, { sent: true, status: 429, correlation_id: CORRELATION, retry_after_s: 7 });
  assert.deepEqual(problem, { code: "MODEL_RATE_LIMITED", message, sent: true, correlation: CORRELATION, retryAfter: 7 });
  const text = failureText(problem);
  assert.match(text, /^The model call failed, so there is no reply: The agent answered 429: .* \[MODEL_RATE_LIMITED\]\./);
  // The runtime's message already names the wait, so it is not said twice.
  assert.doesNotMatch(text, /asks to wait/);
  assert.match(text, /Tools may already have run in this turn, so sending it again is a second turn\.$/);
  const unnamed = { ...problem, message: "The agent answered 429: rate limited." };
  assert.match(failureText(unnamed), /The gateway asks to wait 7 s before sending again\./);
  assert.match(correlationText(problem), /Logs tab/);
});

test("every model code is a model failure, MODEL_BLOCKED included", () => {
  assert.ok(MODEL_CODES.includes("MODEL_BLOCKED"));
  for (const code of MODEL_CODES.filter((item) => item !== "MODEL_STEP_LIMIT")) {
    const problem = failed({ code, message: "m" }, { sent: true, correlation_id: CORRELATION, retry_after_s: null });
    assert.equal(problem.correlation, CORRELATION);
    assert.equal(problem.retryAfter, undefined);
    assert.match(failureText(problem), /^The model call failed/);
    // Only the codes whose next step is to send again warn that it is a second turn.
    assert.equal(/second turn/.test(failureText(problem)), ["MODEL_RATE_LIMITED", "MODEL_UNAVAILABLE"].includes(code), code);
  }
});

test("odd correlation ids and waits are dropped; other codes keep their words", () => {
  const odd = failed({ code: "MODEL_UNAVAILABLE", message: "m" }, { sent: true, correlation_id: "<b>", retry_after_s: "7" });
  assert.equal(odd.correlation, undefined);
  assert.equal(odd.retryAfter, undefined);
  assert.equal(correlationText(odd), null);
  const agent = failed({ code: "AGENT_ERROR", message: "The agent answered 500: x" }, { sent: true, correlation_id: CORRELATION });
  assert.equal(agent.correlation, undefined);
  assert.match(failureText(agent), /second turn/);
});

test("tokens show the cached part only when the runtime reported some", () => {
  assert.equal(tokensText({ prompt: 812, output: 64, cached: 600 }), "812 in (600 cached), 64 out");
  assert.equal(tokensText({ prompt: 812, output: 64, cached: 0 }), "812 in, 64 out");
  assert.equal(tokensText({ prompt: 812, output: 64, cached: null }), "812 in, 64 out");
  assert.equal(tokensText({ prompt: 1812, output: 64, cached: 1500 }), "1,812 in (1,500 cached), 64 out");
});

test("a fallback, unknown tools and a guardrail block in words", () => {
  assert.equal(fallbackText(1, ["chat-backup"]), "A gateway fallback served this turn (served by chat-backup).");
  assert.equal(fallbackText(2, []), "A gateway fallback served this turn.");
  assert.equal(fallbackText(0, ["chat"]), null);
  assert.equal(fallbackText(null, ["chat"]), null);
  assert.equal(fallbackText(true, ["chat"]), null);
  assert.equal(unknownToolsText(["delete_everything"]), "The model asked for a tool this agent does not have (delete_everything); the runtime refused it, so it is not a tool call.");
  assert.match(unknownToolsText(["a", "b"]), /tools this agent does not have \(a, b\); the runtime refused them, so they are not tool calls/);
  assert.equal(unknownToolsText([]), null);
  assert.equal(unknownToolsText("x"), null);
  assert.equal(guardrailText({ action: "blocked", name: "presidio-pii", entity: "US_SSN" }), "Blocked by guardrail presidio-pii (US_SSN).");
  assert.equal(guardrailText({ action: "blocked", name: null, entity: null }), "Blocked by a guardrail.");
  assert.equal(guardrailText({ action: "allowed", name: "g" }), null);
  assert.equal(guardrailText(null), null);
});

test("a trace's flags and lines carry the root span's facts", () => {
  const trace = {
    status: "error", latency_ms: 41, span_count: 2, tokens: { prompt: 321, completion: 70, total: 391 },
    cached_prompt_tokens: 300, fallbacks: 1, served_by: ["chat-backup"], unknown_tools: ["delete_everything"],
    guardrail: { action: "blocked", name: "presidio-pii", entity: "US_SSN" }, spans: [], tool_calls: [], models: [],
  };
  assert.deepEqual(traceFlags(trace), [
    "A gateway fallback served this turn (served by chat-backup).",
    "The model asked for a tool this agent does not have (delete_everything); the runtime refused it, so it is not a tool call.",
    "Blocked by guardrail presidio-pii (US_SSN).",
  ]);
  assert.match(traceLine(trace), /321 in \(300 cached\), 70 out tokens/);
  assert.match(traceSummary(trace), /391 tokens \(300 cached\)/);
  const old = { status: "ok", tokens: { prompt: 1, completion: 2, total: 3 }, span_count: 1, spans: [] };
  assert.deepEqual(traceFlags(old), []);
  assert.match(traceLine(old), /1 in, 2 out tokens/);
  assert.doesNotMatch(traceSummary(old), /cached/);
});

test("an agent's cached prompt tokens add up, and are unknown when any key did not report them", () => {
  const key = (id, cached) => ({
    key_alias: `agentbreeder-${id}`, kind: "deployment", id, name: "hello", calls: 1, failed: 0, prompt_tokens: 10,
    completion_tokens: 5, cached_prompt_tokens: cached, cost_usd: 0.1, unpriced: 0, models: [],
  });
  const both = agentCost({ keys: [key("a".repeat(32), 4), key("b".repeat(32), 6)] }, "hello");
  assert.equal(both.total.cached_prompt_tokens, 10);
  const one = agentCost({ keys: [key("a".repeat(32), 4), key("b".repeat(32), null)] }, "hello");
  assert.equal(one.total.cached_prompt_tokens, null);
});

test("MODEL_STEP_LIMIT has its own plain-language line (#1085 S2)", () => {
  assert.ok(MODEL_CODES.includes("MODEL_STEP_LIMIT"));
  const problem = failed({ code: "MODEL_STEP_LIMIT", message: "The agent answered 502: step limit" }, { sent: true, correlation_id: CORRELATION, retry_after_s: 3 });
  assert.equal(problem.correlation, CORRELATION);
  const text = failureText(problem);
  assert.match(text, /^The agent reached its limit on model calls for one turn while the model still asked for tools, so there is no reply: /);
  assert.match(text, /\[MODEL_STEP_LIMIT\]/);
  assert.match(text, /Sending it again starts a new turn\.$/);
  assert.doesNotMatch(text, /Tools ran|shorter request/); // nothing has shown either
  assert.doesNotMatch(text, /The model call failed/);
});
