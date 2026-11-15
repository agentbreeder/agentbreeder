import assert from "node:assert/strict";
import { test } from "node:test";

import {
  SHOWN, eventText, eventTime, eventTone, newestId, shownEvents, sourceText, watchNote, watchProblem,
} from "../../src/agentbreeder/center/static/lib/watch.js";

const died = { id: 1, event: "died", time: "2026-11-14T12:00:00Z", source: "service", name: "postgres", deployment: null, exit_code: 0, oom: false };
const agent = { id: 2, event: "unhealthy", time: null, source: "agent", name: "northwind-support", deployment: "a".repeat(32), role: "egress" };
const restarted = { id: 3, event: "restarted", time: "2026-11-14T12:00:01Z", source: "service", name: "postgres", deployment: null };

test("an event in words, from its fields only", () => {
  assert.equal(eventText(died), "postgres exited by itself (exit 0)");
  assert.equal(eventText({ ...died, exit_code: 137, oom: true }), "postgres exited by itself (exit 137, out of memory)");
  assert.equal(eventText({ ...died, exit_code: null }), "postgres exited by itself (no exit code)");
  assert.equal(eventText(agent), "agent northwind-support (egress) became unhealthy");
  assert.equal(eventText(restarted), "postgres started again");
  assert.equal(eventText({ ...restarted, event: "other" }), "postgres");
  assert.equal(eventText(null), "");
  assert.equal(sourceText({ ...agent, role: undefined }), "agent northwind-support");
  assert.equal(sourceText(null), "");
});

test("tones: exits are errors, health warnings, restarts plain", () => {
  assert.equal(eventTone(died), "error");
  assert.equal(eventTone(agent), "warning");
  assert.equal(eventTone(restarted), "");
  assert.equal(eventTone(null), "");
});

test("a missing time says so", () => {
  assert.equal(eventTime(agent), "time not reported");
  assert.match(eventTime(died, "en-GB"), /2026/);
});

test("the newest id and the shown slice", () => {
  const watch = { data: { events: [restarted, agent, died] } };
  assert.equal(newestId(watch), 3);
  assert.equal(newestId(null), 0);
  assert.equal(newestId({ data: { events: [{ id: "x" }] } }), 0);
  assert.deepEqual(shownEvents(watch, 2), { events: [restarted, agent], more: 1 });
  const many = { data: { events: Array.from({ length: SHOWN + 2 }, (_, i) => ({ ...died, id: i })) } };
  assert.equal(shownEvents(many).events.length, SHOWN);
  assert.equal(shownEvents(many).more, 2);
  assert.deepEqual(shownEvents({}), { events: [], more: 0 });
});

test("problems and the note", () => {
  assert.equal(watchProblem({ data: null, error: null }), null);
  assert.match(watchProblem({ error: { code: "STACK_NOT_UP", message: "never brought up" } }), /^never brought up Container events are not being watched/);
  assert.match(watchProblem({ error: {} }), /^The watch command failed\./);
  assert.equal(watchNote(null), "Starting to watch its containers…");
  assert.equal(watchNote({ data: { running: false, status: null, held: 0 } }), "");
  const note = watchNote({ data: { running: true, status: { time: "2026-11-14T12:00:00Z" }, held: 2 } }, "en-GB");
  assert.match(note, /^Watching its containers while this page is open\. Last checked .*2026.*\. 2 restarts during a Start services job not shown\.$/);
  assert.match(watchNote({ data: { running: true, status: null, held: 1 } }), /1 restart during/);
});
