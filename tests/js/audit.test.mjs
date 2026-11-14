import assert from "node:assert/strict";
import { test } from "node:test";

import { agentOf, pageLabel, production, releaseEval, rowAgents } from "../../src/agentbreeder/center/static/lib/audit.js";

const IMAGE = "sha256:" + "a".repeat(64);
const OTHER = "sha256:" + "b".repeat(64);
const RUN = "20261113-101500-abc123";

function release(decision, image = IMAGE, extra = []) {
  return { seq: 9, action: "release", decision, attempt_id: "r1", deployment_id: null, agent: null, reasons: [...extra, { code: "RELEASE_IMAGE", message: image }] };
}

test("a candidate belongs to its agent", () => {
  assert.equal(agentOf("support-candidate"), "support");
  assert.equal(agentOf("support"), "support");
  assert.equal(agentOf("-candidate"), null);
  assert.equal(agentOf("Support"), null);
  assert.equal(agentOf("x\u202eevil"), null);
  assert.equal(agentOf(null), null);
});

test("each row names its agent; an attempt's first row takes its deployment's agent", () => {
  const rows = [
    { seq: 3, attempt_id: "p", action: "transition", agent: "support-candidate", image_digest: IMAGE },
    { seq: 2, attempt_id: "p", action: "deploy", agent: "support-candidate" },
    { seq: 1, attempt_id: "p", action: "attempt_started", agent: null, image_digest: IMAGE },
    { seq: 0, attempt_id: "q", action: "attempt_started", agent: null },
  ];
  const agents = rowAgents(rows, []);
  assert.deepEqual(agents[0], { name: "support", candidate: true, deployment: "support-candidate" });
  assert.equal(agents[2].name, "support");
  assert.equal(agents[3], null);
});

test("a release row is its image's: production first, else the candidate on this page", () => {
  assert.equal(rowAgents([release("promoted")], [{ name: "support", image: IMAGE, state: "ready" }])[0].name, "support");
  const page = [release("started"), { seq: 8, attempt_id: "c", agent: "helper-candidate", image_digest: IMAGE }];
  assert.deepEqual(rowAgents(page, [])[0], { name: "helper", candidate: false, deployment: null });
  assert.equal(rowAgents([release("started", OTHER)], [{ name: "support", image: IMAGE }])[0], null);
  assert.equal(rowAgents([release("started", "not-an-image")], [{ name: "support", image: "not-an-image" }])[0], null);
  // Only release rows are matched by image.
  assert.equal(rowAgents([{ ...release("x"), action: "disable" }], [{ name: "support", image: IMAGE }])[0], null);
});

test("an image two agents ran names neither", () => {
  const page = [release("started"), { seq: 8, attempt_id: "c", agent: "helper-candidate", image_digest: IMAGE }];
  assert.equal(rowAgents(page, [{ name: "support", image: IMAGE, state: "ready" }])[0], null);
  // The same agent twice (candidate and production) is not ambiguous.
  assert.equal(rowAgents(page, [{ name: "helper", image: IMAGE, state: "ready" }])[0].name, "helper");
});

test("an attempt split across pages leaves its first row unknown", () => {
  assert.equal(rowAgents([{ seq: 1, attempt_id: "p", action: "attempt_started", agent: null }], [])[0], null);
});

test("garbage in, nulls out", () => {
  assert.deepEqual(rowAgents(null, null), []);
  assert.deepEqual(rowAgents([null, {}, { agent: 5 }], "x"), [null, null, null]);
});

test("production is a ready local deployment, never a candidate", () => {
  const ready = { name: "support", target: "local", state: "ready", endpoint: "http://127.0.0.1:8001" };
  assert.equal(production([ready], "support"), ready);
  assert.equal(production([{ ...ready, state: "failed" }], "support"), null);
  assert.equal(production([{ ...ready, name: "support-candidate" }], "support-candidate"), null);
  assert.equal(production([{ ...ready, target: "kubernetes" }], "support"), null);
  assert.equal(production(null, "support"), null);
});

test("a release row's eval run and report path", () => {
  const row = release("promoted", IMAGE, [{ code: "RELEASE_EVAL", message: `${RUN} 61/61` }]);
  assert.deepEqual(releaseEval(row, "northwind"), { run: RUN, passed: 61, cases: 61, report: `deploy/northwind.eval-${RUN}.json` });
  assert.equal(releaseEval(row, null).report, null);
  assert.equal(releaseEval(row, "../etc").report, null);
  assert.equal(releaseEval(release("started"), "northwind"), null);
  assert.equal(releaseEval({ ...row, reasons: [{ code: "RELEASE_EVAL", message: "../x 1/1" }] }, "northwind"), null);
  assert.equal(releaseEval({ ...row, action: "deploy" }, "northwind"), null);
});

test("page label", () => {
  assert.equal(pageLabel([], 50), "Page 1, 50 rows, newest first.");
  assert.equal(pageLabel([51, 2], 1), "Page 3, 1 row, newest first.");
  assert.equal(pageLabel([], 0), "No rows on this page.");
});
