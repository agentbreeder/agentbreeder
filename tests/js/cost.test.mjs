import assert from "node:assert/strict";
import { test } from "node:test";

import { LOCAL_LABEL, WINDOWS, agentCost, amount, asOfText, costNotes, costPath, costRefusal, count, keyLabel, modelLabel, money, partial, windowText } from "../../src/agentbreeder/center/static/lib/cost.js";

const ID = "0123456789abcdef0123456789abcdef";
const CANDIDATE_ID = "fedcba9876543210fedcba9876543210";
const model = (group, name, calls, usd, extra = {}) => ({ model_group: group, model: name, calls, failed: 0, prompt_tokens: calls * 10, completion_tokens: calls * 20, cost_usd: usd, unpriced: 0, ...extra });
const key = (id, name, models, extra = {}) => {
  const sum = (field) => models.reduce((n, m) => n + m[field], 0);
  return {
    key_alias: `agentbreeder-${id}`, kind: "deployment", id, name, target: "local", state: "ready", environment: "dev",
    calls: sum("calls"), failed: sum("failed"), prompt_tokens: sum("prompt_tokens"), completion_tokens: sum("completion_tokens"),
    cost_usd: sum("cost_usd"), unpriced: sum("unpriced"), models, models_cut: 0, ...extra,
  };
};
const ANSWER = {
  days: 30, since: "2026-10-16", until: "2026-11-14", as_of: "2026-11-14T22:05:09Z", currency: "USD",
  source: "litellm spend logs", truncated: false, keys_cut: 0, models_cut: 0,
  keys: [
    key(ID, "hello", [model("chat", "openai/gpt-5.6", 5, 0.002)]),
    key(CANDIDATE_ID, "hello-candidate", [model("chat", "openai/gpt-5.6", 2, 0.001), model("judge", "gemini/x", 1, 0.0005, { unpriced: 1 })], { state: "removed" }),
    key("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "other", [model("chat", "openai/gpt-5.6", 9, 0.009)]),
    { key_alias: "agentbreeder-eval-r1", kind: "eval", run: "r1", calls: 1, failed: 0, prompt_tokens: 1, completion_tokens: 1, cost_usd: 0.1, unpriced: 0, models: [], models_cut: 0 },
  ],
  models: [],
  total: { calls: 18, failed: 0, prompt_tokens: 0, completion_tokens: 0, cost_usd: 0.1125, unpriced: 1 },
};

test("a cost path only for an offered window", () => {
  assert.deepEqual(WINDOWS, [7, 30, 90]);
  assert.equal(costPath("abc", 30), "/api/projects/abc/llm-cost?days=30");
  for (const bad of [0, 1, 31, "30", null, undefined, 7.5]) assert.equal(costPath("abc", bad), null, String(bad));
});

test("amounts keep their currency and enough decimals", () => {
  assert.equal(money(0.00444, "USD"), "USD 0.004440");
  assert.equal(money(0, "USD"), "USD 0.000000");
  assert.equal(money(12.345, "USD"), "USD 12.35");
  assert.equal(money(1, ""), "USD 1.00");
  for (const bad of [-1, NaN, Infinity, "1", null]) assert.equal(money(bad, "USD"), "-");
  assert.equal(count(12345), "12,345");
  assert.equal(count(-1), "-");
  assert.equal(count(1.5), "-");
});

test("models and keys have readable labels", () => {
  assert.equal(modelLabel({ model_group: "chat", model: "openai/gpt-5.6" }), "chat (openai/gpt-5.6)");
  assert.equal(modelLabel({ model_group: "x", model: "x" }), "x");
  assert.equal(modelLabel({ model_group: null, model: "m" }), "m");
  assert.equal(modelLabel({}), "unknown model");
  assert.equal(keyLabel(ANSWER.keys[0]), "hello");
  assert.equal(keyLabel(ANSWER.keys[3]), "eval run r1");
  assert.equal(keyLabel({ kind: "unknown", id: "x" }), "x (not in this state)");
  assert.equal(keyLabel(null), "");
});

test("an agent's cost: its deployments and candidates, their models added up", () => {
  const own = agentCost(ANSWER, "hello");
  assert.deepEqual(own.keys.map((k) => k.name), ["hello", "hello-candidate"]);
  assert.equal(own.total.calls, 8);
  assert.ok(Math.abs(own.total.cost_usd - 0.0035) < 1e-12);
  assert.equal(own.total.unpriced, 1);
  assert.deepEqual(own.models.map((m) => [m.model_group, m.calls]), [["chat", 7], ["judge", 1]]);
  assert.equal(own.modelsCut, false);
  assert.equal(agentCost(ANSWER, "nobody").keys.length, 0);
  assert.equal(agentCost(null, "hello").total.calls, 0);
  const cut = { ...ANSWER, keys: [{ ...ANSWER.keys[0], models_cut: 2 }] };
  assert.equal(agentCost(cut, "hello").modelsCut, true);
});

test("notes say what is partial or unpriced", () => {
  assert.deepEqual(costNotes({ truncated: false, keys_cut: 0, models_cut: 0 }, { unpriced: 0 }), []);
  const notes = costNotes({ truncated: true, keys_cut: 3, models_cut: 1 }, { unpriced: 2 });
  assert.equal(notes.length, 4);
  assert.match(notes[0], /partial/);
  assert.match(notes[1], /2 calls have no price/);
  assert.match(costNotes({}, { unpriced: 1 })[0], /1 call has no price/);
  assert.deepEqual(costNotes({ keys_cut: 3 }, { unpriced: 0 }, { keys: false }), []);
  assert.equal(partial({ truncated: false, keys_cut: 0 }), false);
  assert.equal(partial({ truncated: true, keys_cut: 0 }), true);
  assert.equal(partial({ truncated: false, keys_cut: 2 }), true);
  assert.equal(partial(null), false);
});

test("refusals name a next step", () => {
  assert.deepEqual(costRefusal({ code: "GATEWAY_UNREACHABLE", message: "x" }), { reason: "the model gateway did not answer", next: "start the services (Services, or agentbreeder services up)" });
  assert.equal(costRefusal({ code: "GATEWAY_ERROR", message: "HTTP 500" }).reason, "HTTP 500");
  assert.equal(costRefusal({ code: "STATE_NEEDS_MIGRATION" }).next, "run agentbreeder migrate");
  assert.deepEqual(costRefusal({ code: "ODD", message: "m" }), { reason: "m", next: "run agentbreeder cost --stack DIR in a terminal" });
  assert.equal(costRefusal(null).reason, "the cost could not be read");
});

test("the read time and window", () => {
  assert.equal(asOfText(ANSWER), "as of 2026-11-14 22:05:09 UTC");
  assert.equal(asOfText({ as_of: "yesterday" }), "");
  assert.equal(windowText(ANSWER), "2026-10-16 to 2026-11-14 (UTC days)");
  assert.equal(windowText({}), "");
});

test("a local model (Ollama, #1025 O1) has no price, not $0", () => {
  assert.equal(amount({ calls: 2, local: 2, cost_usd: 0 }, "USD"), LOCAL_LABEL);
  assert.equal(amount({ calls: 3, local: 2, cost_usd: 0.004 }, "USD"), "USD 0.004000");
  assert.equal(amount({ calls: 0, local: 0, cost_usd: 0 }, "USD"), "USD 0.000000");
  assert.equal(amount(undefined, "USD"), "-");
  assert.deepEqual(costNotes({}, { local: 1 }), ["1 call went to a local model (Ollama): no price (local model), not in the amounts."]);
  const summed = agentCost({ keys: [{ key_alias: "agentbreeder-0123456789abcdef0123456789abcdef", kind: "deployment", id: "0123456789abcdef0123456789abcdef", name: "a", target: "local", state: "ready", environment: "dev", calls: 1, failed: 0, prompt_tokens: 1, completion_tokens: 1, cost_usd: 0, unpriced: 0, local: 1, models: [] }] }, "a");
  assert.equal(summed.total.local, 1);
});
