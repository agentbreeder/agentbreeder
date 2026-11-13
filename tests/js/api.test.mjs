import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError, createClient } from "../../src/agentbreeder/center/static/lib/api.js";

function fakeFetch(status, body, calls = []) {
  return async (path, options) => {
    calls.push({ path, options });
    return {
      status,
      ok: status >= 200 && status < 300,
      json: async () => (typeof body === "string" ? JSON.parse(body) : body),
    };
  };
}

const session = (value = null) => ({ value, get() { return this.value; }, set(v) { this.value = v; } });

test("GET carries the console header and the session, never cookies", async () => {
  const calls = [];
  const client = createClient({ fetchImpl: fakeFetch(200, { data: { a: 1 }, error: null, stale: false }, calls), session: session("s") });
  assert.deepEqual(await client.get("/api/overview"), { a: 1 });
  const { options } = calls[0];
  assert.equal(options.headers["X-AgentBreeder-Center"], "1");
  assert.equal(options.headers["X-AgentBreeder-Session"], "s");
  assert.equal(options.credentials, "omit");
  assert.equal(options.method, undefined);
});

test("POST sends JSON; no session header without a session", async () => {
  const calls = [];
  const client = createClient({ fetchImpl: fakeFetch(200, { data: null, error: null, stale: false }, calls), session: session() });
  await client.post("/api/projects", { path: "/x" });
  const { options } = calls[0];
  assert.equal(options.method, "POST");
  assert.equal(options.headers["Content-Type"], "application/json");
  assert.equal(options.body, '{"path":"/x"}');
  assert.ok(!("X-AgentBreeder-Session" in options.headers));
});

test("request keeps the whole envelope", async () => {
  const client = createClient({ fetchImpl: fakeFetch(200, { data: [1], error: { code: "X", message: "m" }, stale: true }), session: session() });
  assert.deepEqual(await client.request("/api/x"), { data: [1], error: { code: "X", message: "m" }, stale: true });
});

test("401 ends the session once and throws kind=session", async () => {
  let ended = 0;
  const client = createClient({ fetchImpl: fakeFetch(401, { data: null, error: { code: "AUTH", message: "no" } }), session: session("s"), onSessionEnded: () => { ended += 1; } });
  await assert.rejects(client.get("/api/overview"), (error) => error instanceof ApiError && error.kind === "session" && error.status === 401);
  assert.equal(ended, 1);
});

test("a network failure is kind=unreachable", async () => {
  const client = createClient({ fetchImpl: async () => { throw new TypeError("Failed to fetch"); }, session: session() });
  await assert.rejects(client.get("/api/overview"), (error) => error.kind === "unreachable" && /not reachable/.test(error.message));
});

test("other errors carry the envelope's code, message and data", async () => {
  const client = createClient({ fetchImpl: fakeFetch(409, { data: { job: "j" }, error: { code: "BUSY", message: "A job is running." } }), session: session() });
  await assert.rejects(client.post("/api/projects/p/jobs", {}), (error) => error.kind === "http" && error.status === 409 && error.code === "BUSY" && error.data.job === "j" && error.message === "A job is running.");
});

test("an unreadable body is reported, not thrown raw", async () => {
  const client = createClient({ fetchImpl: async () => ({ status: 500, ok: false, json: async () => { throw new SyntaxError("x"); } }), session: session() });
  await assert.rejects(client.get("/api/x"), (error) => error.kind === "http" && error.code === "UNREADABLE");
});

test("only /api/ paths are fetched", async () => {
  const client = createClient({ fetchImpl: fakeFetch(200, {}), session: session() });
  await assert.rejects(client.get("https://example.com/api/x"), /not an API path/);
  await assert.rejects(client.get("//evil/api"), /not an API path/);
  for (const escape of ["/api/../x", "/api/%2e%2e/x", "/api/a/./b", "/api/\\..", "/api/a%2Fb", "/api/", "/api//x", "/api/x\n"]) {
    await assert.rejects(client.get(escape), /not an API path/, escape);
  }
  assert.equal(await client.get("/api/jobs/0123abcd?after=12"), null);
  assert.equal(await client.get("/api/projects/0123456789ab/used-by?ref=prompts%2Fa%401.0.0"), null);
});

test("a 2xx answer that is not JSON is an error, not silent null", async () => {
  const client = createClient({ fetchImpl: async () => ({ status: 200, ok: true, json: async () => { throw new SyntaxError("x"); } }), session: session() });
  await assert.rejects(client.get("/api/x"), (error) => error.kind === "http" && error.code === "UNREADABLE");
});
