import assert from "node:assert/strict";
import { test } from "node:test";

import { createJobs, MAX_LINES } from "../../src/agentbreeder/center/static/lib/jobs.js";

// A fake client answering from scripted tables and a manual timer queue.
function harness({ activity = { running: [] }, jobs = {}, startError = null } = {}) {
  const calls = [];
  const timers = [];
  const client = {
    async get(path) {
      calls.push(["GET", path]);
      if (path === "/api/activity") return typeof activity === "function" ? activity() : activity;
      const match = /^\/api\/jobs\/([^?]+)\?after=(\d+)$/.exec(path);
      const script = jobs[match[1]];
      const reply = typeof script === "function" ? script(Number(match[2])) : script.shift();
      if (reply instanceof Error) throw reply;
      return reply;
    },
    async post(path, body) {
      calls.push(["POST", path, body]);
      if (path.endsWith("/jobs") && startError) throw startError;
      if (path.endsWith("/jobs")) return { job: "j1" };
      return {};
    },
  };
  const store = createJobs({
    client,
    setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimer: (id) => { if (timers[id - 1]) timers[id - 1].fn = null; },
  });
  const tick = async () => {
    const pending = timers.splice(0);
    for (const t of pending) if (t.fn) await t.fn();
    await new Promise((r) => setImmediate(r));
  };
  return { store, calls, timers, tick };
}

const flush = () => new Promise((r) => setImmediate(r));

test("a started job is followed and its lines are appended by seq", async () => {
  const { store, calls, tick } = harness({
    jobs: {
      j1: [
        { kind: "build", project: "p1", state: "running", lines: [{ seq: 1, text: "a" }, { seq: 2, text: "b" }] },
        { kind: "build", project: "p1", state: "running", lines: [{ seq: 2, text: "b" }, { seq: 3, text: "c" }] },
        { kind: "build", project: "p1", state: "succeeded", exit_code: 0, lines: [] },
      ],
    },
  });
  assert.equal(await store.start("p1", "build"), "j1");
  assert.deepEqual(calls[0], ["POST", "/api/projects/p1/jobs", { kind: "build", options: {} }]);
  await flush();
  let [entry] = store.list();
  assert.deepEqual(entry.lines.map((l) => l.text), ["a", "b"]);
  assert.equal(store.runningIn("p1").id, "j1");
  await tick();
  [entry] = store.list();
  assert.deepEqual(entry.lines.map((l) => l.text), ["a", "b", "c"]);
  assert.ok(calls.some(([, path]) => path === "/api/jobs/j1?after=2"));
  assert.equal(store.version("p1"), 0);
  await tick();
  [entry] = store.list();
  assert.equal(entry.state, "succeeded");
  assert.equal(entry.collapsed, true, "a finished job collapses to its result line");
  assert.equal(store.version("p1"), 1, "the project's screens read again");
  assert.equal(store.runningIn("p1"), null);
});

test("keys go only in the body; a busy start follows the job holding the lock", async () => {
  const { store, calls } = harness({ jobs: { other: [{ kind: "release", project: "p1", state: "running", lines: [] }] } });
  await store.start("p1", "services-up", { digest: "d" }, { K: "v" });
  assert.deepEqual(calls[0][2], { kind: "services-up", options: { digest: "d" }, keys: { K: "v" } });
  const busy = Object.assign(new Error("A job is running."), { kind: "http", status: 409, code: "BUSY", data: { job: "other" } });
  const refused = harness({ startError: busy, jobs: { other: [{ kind: "release", project: "p1", state: "running", lines: [] }] } });
  await assert.rejects(refused.store.start("p1", "build"), /A job is running/);
  await flush();
  const [entry] = refused.store.list();
  assert.equal(entry.id, "other");
  assert.equal(entry.kind, "release");
});

test("activity follows jobs started elsewhere", async () => {
  const { store } = harness({
    activity: { running: [{ id: "x", kind: "release", project: "p2" }] },
    jobs: { x: [{ kind: "release", project: "p2", state: "running", lines: [{ seq: 1, text: "hi" }] }] },
  });
  store.begin();
  await flush();
  await flush();
  assert.equal(store.running().length, 1);
  assert.equal(store.list()[0].lines[0].text, "hi");
});

test("cancel is asked once; stop is a separate request", async () => {
  const { store, calls } = harness({ jobs: { j1: () => ({ kind: "build", project: "p", state: "running", lines: [], cancelling: true, can_stop: true }) } });
  await store.start("p", "build");
  await flush();
  await store.cancel("j1");
  await store.cancel("j1");
  assert.equal(calls.filter(([, path]) => path === "/api/jobs/j1/cancel").length, 1);
  assert.equal(store.list()[0].cancelAsked, true);
  assert.equal(store.list()[0].canStop, true);
  await store.stop("j1");
  assert.ok(calls.some(([, path]) => path === "/api/jobs/j1/stop"));
});

test("cancel failure resets the control", async () => {
  const calls = [];
  const client = {
    async get() { return { kind: "build", project: "p", state: "running", lines: [] }; },
    async post(path) {
      calls.push(path);
      if (path.endsWith("/jobs")) return { job: "j1" };
      throw Object.assign(new Error("no answer"), { kind: "unreachable" });
    },
  };
  const store = createJobs({ client, setTimer: () => 1, clearTimer: () => {} });
  await store.start("p", "build");
  await flush();
  await store.cancel("j1");
  const [entry] = store.list();
  assert.equal(entry.cancelAsked, false, "Cancel is offered again");
  assert.match(entry.error, /Cancel was not sent: no answer/);
  await store.cancel("j1");
  assert.equal(calls.filter((p) => p.endsWith("/cancel")).length, 2);
});

test("one loop per job: follow during an in-flight poll does not start another", async () => {
  let gets = 0;
  let release;
  const client = {
    get: () => { gets += 1; return new Promise((resolve) => { release = () => resolve({ kind: "build", project: "p", state: "running", lines: [] }); }); },
    post: async () => ({ job: "j1" }),
  };
  const timers = [];
  const store = createJobs({ client, setTimer: (fn) => { timers.push(fn); return timers.length; }, clearTimer: () => {} });
  await store.start("p", "build");
  store.follow("j1");
  store.follow("j1");
  assert.equal(gets, 1);
  release();
  await flush();
  assert.equal(timers.length, 1, "one next round scheduled");
});

test("an answer from before a halt is dropped; only the new loop schedules", async () => {
  const pending = [];
  const running = { kind: "build", project: "p", state: "running", lines: [] };
  const client = {
    get: (path) => (path === "/api/activity" ? new Promise(() => {}) : new Promise((resolve) => pending.push(() => resolve(running)))),
    post: async () => ({ job: "j1" }),
  };
  const rounds = [];
  const store = createJobs({ client, setTimer: (fn, ms) => { if (ms === 1500) rounds.push(fn); return rounds.length; }, clearTimer: () => {} });
  await store.start("p", "build"); // request 0 in flight
  store.halt();
  store.begin(); // a fresh loop: request 1
  assert.equal(pending.length, 2);
  pending[0](); // the stale answer
  await flush();
  assert.equal(rounds.length, 0, "the stale answer schedules nothing");
  store.follow("j1");
  assert.equal(pending.length, 2, "the new request still counts as in flight");
  pending[1]();
  await flush();
  assert.equal(rounds.length, 1, "the new loop schedules its next round");
});

test("only finished jobs can be dismissed", async () => {
  const { store, tick } = harness({
    jobs: { j1: [{ kind: "build", project: "p", state: "running", lines: [] }, { kind: "build", project: "p", state: "failed", exit_code: 2, lines: [] }] },
  });
  await store.start("p", "build");
  await flush();
  store.dismiss("j1");
  assert.equal(store.list().length, 1);
  await tick();
  store.dismiss("j1");
  assert.equal(store.list().length, 0);
});

test("a failed poll retries; a session end stops the loop", async () => {
  const sessionEnded = Object.assign(new Error("s"), { kind: "session" });
  const { store, timers } = harness({ jobs: { j1: [new Error("net"), sessionEnded] } });
  await store.start("p", "build");
  await flush();
  assert.equal(store.list()[0].error, "Could not read the job, retrying: net");
  assert.equal(timers.length, 1);
  await timers.shift().fn();
  await flush();
  assert.equal(timers.length, 0, "no retry after the session ended");
});

test("lines are capped, oldest dropped", async () => {
  const many = Array.from({ length: MAX_LINES + 10 }, (_, i) => ({ seq: i + 1, text: String(i) }));
  const { store } = harness({ jobs: { j1: [{ kind: "build", project: "p", state: "succeeded", lines: many }] } });
  await store.start("p", "build");
  await flush();
  const [entry] = store.list();
  assert.equal(entry.lines.length, MAX_LINES);
  assert.equal(entry.dropped, 10);
  assert.equal(entry.lines[0].text, "10");
});

test("halt stops every loop", async () => {
  const { store, timers } = harness({ jobs: { j1: () => ({ kind: "build", project: "p", state: "running", lines: [] }) } });
  await store.start("p", "build");
  await flush();
  store.halt();
  for (const t of timers) assert.equal(t.fn, null);
  // Back from unreachable: the running job's loop resumes.
  timers.length = 0;
  store.begin();
  await flush();
  assert.ok(timers.some((t) => t.fn && t.ms === 1500), "the running job polls again");
});
