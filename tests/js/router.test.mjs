import assert from "node:assert/strict";
import { test } from "node:test";

import { createRouter, href, pathFrom, routeTitle, titleFor, tokenFrom } from "../../src/agentbreeder/center/static/lib/router.js";

const TOKEN = "A".repeat(43);

test("the one-time token is read only from an exact #t= fragment", () => {
  assert.equal(tokenFrom(`#t=${TOKEN}`), TOKEN);
  assert.equal(tokenFrom(`#t=${TOKEN}&x=1`), null);
  assert.equal(tokenFrom("#t=short"), null);
  assert.equal(tokenFrom(`#/audit?t=${TOKEN}`), null);
  assert.equal(tokenFrom(""), null);
  assert.equal(tokenFrom(undefined), null);
});

test("hash to path", () => {
  assert.equal(pathFrom(""), "/");
  assert.equal(pathFrom("#"), "/");
  assert.equal(pathFrom(`#t=${TOKEN}`), "/");
  assert.equal(pathFrom("#/audit"), "/audit");
  assert.equal(pathFrom("#/audit/"), "/audit");
  assert.equal(pathFrom("#/agents/a/try?x=1"), "/agents/a/try");
});

const router = createRouter([
  { pattern: "/", title: "Home" },
  { pattern: "/audit", title: "Audit" },
  { pattern: "/agents/:id", title: "Agent" },
  { pattern: "/agents/:id/try", title: "Try it" },
]);

test("static and parameter routes match", () => {
  assert.equal(router.match("/").route.title, "Home");
  assert.equal(router.match("/audit").route.title, "Audit");
  assert.deepEqual(router.match("/agents/support-bot").params, { id: "support-bot" });
  assert.equal(router.match("/agents/support-bot/try").route.title, "Try it");
  assert.equal(router.match("/nope"), null);
  assert.equal(router.match("/agents"), null);
});

test("a parameter is never a path, a URL or empty", () => {
  for (const bad of ["..%2Fetc", "a%2Fb", "http%3A%2F%2Fx", "%E0%A4%A", "a%20b", "%00", "..", ".", "%2e%2e", "%2E"]) {
    assert.equal(router.match(`/agents/${bad}`), null, bad);
  }
  assert.equal(router.match(`/agents/${"x".repeat(129)}`), null);
});

test("href builds and refuses bad parameters", () => {
  assert.equal(href("/agents/:id/try", { id: "a.b_c-1" }), "#/agents/a.b_c-1/try");
  assert.equal(href("/"), "#/");
  assert.throws(() => href("/agents/:id", { id: "../x" }));
  assert.throws(() => href("/agents/:id", {}));
  assert.throws(() => href("/agents/:id", { id: ".." }));
  assert.throws(() => href("/agents/:id", { id: "." }));
  assert.equal(href("/agents/:id", { id: "a..b" }), "#/agents/a..b");
});

test("titles", () => {
  assert.equal(titleFor({ title: "Audit" }), "Audit · AgentBreeder console");
  assert.equal(titleFor(null), "AgentBreeder console");
});

test("a route title can name the page from its parameters", () => {
  const route = { title: (params) => `Prompt ${params.name}` };
  assert.equal(routeTitle(route, { name: "greet" }), "Prompt greet");
  assert.equal(titleFor(route, { name: "greet" }), "Prompt greet · AgentBreeder console");
  assert.equal(routeTitle({ title: "Audit" }), "Audit");
  assert.equal(routeTitle(null), "");
});
