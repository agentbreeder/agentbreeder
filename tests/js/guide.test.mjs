import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  STEP_ROUTES, actionFor, currentStep, linkHost, pillText, resultText, starterNameProblem, stateWord, tryRoute,
} from "../../src/agentbreeder/center/static/lib/guide.js";

const fixture = (name) => JSON.parse(readFileSync(new URL(`../ui/fixtures/guide/${name}.json`, import.meta.url), "utf8").replaceAll("@PROJECT@", "/p"));

test("every step id of the contract has a screen", () => {
  assert.deepEqual(Object.keys(STEP_ROUTES).sort(), ["build", "keys", "kubernetes", "prerequisites", "project", "release", "services", "try"]);
});

test("each fix action maps to a fixed console move, never to the command", () => {
  const step = (action) => ({ fix: { action, command: "rm -rf /" } });
  assert.deepEqual(actionFor(step("build")), { label: "Build", job: "build" });
  assert.equal(actionFor(step("services-up")).route, "/services");
  assert.equal(actionFor(step("release")).route, "/release");
  assert.equal(actionFor(step("new-agent")).starter, true);
  assert.equal(actionFor(step("example")).example, true);
  for (const action of ["cluster-up", "promote"]) assert.equal(actionFor(step(action)).route, "/targets/kubernetes");
  assert.equal(actionFor(step("set-keys")).route, "/services");
  assert.deepEqual(actionFor(step("invoke")), { label: "Try it", try: true }); // #823
  // An unknown or null action is treated as null.
  for (const action of ["rm", "__proto__", "toString", null, undefined]) assert.equal(actionFor(step(action)), null, String(action));
  assert.equal(actionFor(null), null);
  assert.equal(actionFor({ fix: null }), null);
});

test("states read as words", () => {
  assert.deepEqual(["done", "next", "blocked", "todo", "x"].map(stateWord), ["Done", "Next", "Blocked", "To do", "Unknown"]);
});

test("the pill and result row follow the guide's next step", () => {
  const services = fixture("services");
  assert.equal(currentStep(services).id, "services");
  assert.equal(pillText(services), "Next: Services");
  assert.equal(resultText(services), "Next step: Services. The stack's services are not running.");
  const done = fixture("done");
  assert.equal(currentStep(done), null);
  assert.equal(pillText(done), "All steps done");
  assert.equal(resultText(done), "Every step of the guided path is done.");
  assert.equal(pillText(null), null);
});

test("a blocked step is named as blocked", () => {
  const guide = fixture("services");
  guide.steps[3] = { ...guide.steps[3], state: "blocked", reason: { code: "SERVICES_UNREADABLE", message: "Compose is missing" }, fix: { action: null, command: null, note: "Install Compose", url: null } };
  guide.next = null;
  assert.equal(pillText(guide), "Blocked: Services");
  assert.equal(resultText(guide), "Blocked at: Services. Compose is missing.");
});

test("a stale lock has no button: the CLI instruction is shown instead", () => {
  const build = fixture("lock-stale").steps.find((step) => step.id === "build");
  assert.equal(build.reason.code, "LOCK_STALE");
  assert.equal(actionFor(build), null);
  assert.ok(build.fix.command.includes("build"));
});

test("only https links show a host", () => {
  assert.equal(linkHost("https://aistudio.google.com/apikey"), "aistudio.google.com");
  assert.equal(linkHost("javascript:alert(1)"), null);
  assert.equal(linkHost("http://example.com"), null);
  assert.equal(linkHost("not a url"), null);
});

test("starter names follow new agent's rule", () => {
  for (const name of ["hello", "a", "my-agent-2", "a".repeat(31)]) assert.equal(starterNameProblem(name), null, name);
  for (const name of ["Hello", "1x", "x-", "-x", "a b", "a".repeat(32), "../x"]) assert.ok(starterNameProblem(name), name);
  assert.equal(starterNameProblem(""), "Type a name first.");
});

test("Try it opens the guide's own agent, checked against the agent name rule", () => {
  assert.equal(tryRoute(fixture("done")), "/agents/hello/try");
  const withAgent = (agent) => ({ steps: [{ id: "project", data: { agent } }] });
  for (const agent of ["../x", "Hello", "x-candidate", "a/b", "", 7, null]) assert.equal(tryRoute(withAgent(agent)), null, String(agent));
  assert.equal(tryRoute(null), null);
  assert.equal(tryRoute({ steps: [{ id: "project", data: null }] }), null);
});
