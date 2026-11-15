import assert from "node:assert/strict";
import { test } from "node:test";

import {
  emptyReason, FOLLOW_KEEP, followBody, followHeld, followInitial, followMerge, followNotes, followPath, followReadPath, followShown, followStatus, levelShown, linePieces, logNotes, logsPath, matchRanges, searchHits, searchStatus,
  shownRows, stepHit, windowOf,
} from "../../src/agentbreeder/center/static/lib/logs.js";

const ID = "0123456789abcdef0123456789abcdef";

test("the read's path carries only checked values", () => {
  assert.equal(logsPath("aaaaaaaaaaaa", { kind: "service", name: "litellm" }), "/api/projects/aaaaaaaaaaaa/logs/service/litellm?tail=200");
  assert.equal(
    logsPath("aaaaaaaaaaaa", { kind: "deployment", name: ID }, { tail: 1000, since: "10m", previous: true }),
    `/api/projects/aaaaaaaaaaaa/logs/deployment/${ID}?tail=1000&since=10m&previous=1`,
  );
  // --previous is for a deployment only.
  assert.equal(logsPath("aaaaaaaaaaaa", { kind: "service", name: "litellm" }, { previous: true }), "/api/projects/aaaaaaaaaaaa/logs/service/litellm?tail=200");
  for (const source of [null, { kind: "service", name: "../x" }, { kind: "service", name: "Lite" }, { kind: "deployment", name: "d1" }, { kind: "other", name: "x" }]) {
    assert.equal(logsPath("aaaaaaaaaaaa", source), null, JSON.stringify(source));
  }
});

test("levels filter like --level, and unclassified lines only show with no level", () => {
  assert.equal(levelShown(null, null), true);
  assert.equal(levelShown(null, "warning"), false);
  assert.equal(levelShown("info", "warning"), false);
  assert.equal(levelShown("warning", "warning"), true);
  assert.equal(levelShown("error", "warning"), true);
  assert.equal(levelShown("warning", "error"), false);
});

test("rows keep the line, its visible form and its index", () => {
  const lines = ["INFO start", "ERROR \u001b[31mboom", "  at frame", "WARNING slow"];
  const levels = ["info", "error", "error", "warning"];
  assert.deepEqual(shownRows(lines, levels).map((r) => r.index), [0, 1, 2, 3]);
  const errors = shownRows(lines, levels, { level: "error" });
  assert.deepEqual(errors.map((r) => r.index), [1, 2]);
  assert.equal(errors[0].text, "ERROR \\x1b[31mboom");
  assert.equal(errors[0].line, lines[1]);
  assert.deepEqual(shownRows(lines, levels, { filter: "FRAME" }).map((r) => r.index), [2]);
  assert.deepEqual(shownRows(lines, levels, { filter: "x1b" }).map((r) => r.index), [1]);
  assert.deepEqual(shownRows(null, null), []);
});

test("search finds rows and steps around them", () => {
  const rows = shownRows(["a timeout", "ok", "Timeout again"], [null, null, null]);
  const hits = searchHits(rows, "timeout");
  assert.deepEqual(hits, [0, 2]);
  assert.equal(stepHit(hits, -1), 0);
  assert.equal(stepHit(hits, -1, true), 1);
  assert.equal(stepHit(hits, 1), 0);
  assert.equal(stepHit(hits, 0, true), 1);
  assert.equal(stepHit([], -1), -1);
  assert.equal(searchStatus(hits, -1, "timeout"), "2 matches");
  assert.equal(searchStatus([0], -1, "x"), "1 match");
  assert.equal(searchStatus(hits, 1, "timeout"), "2 of 2");
  assert.equal(searchStatus([], -1, "x"), "No matches");
  assert.equal(searchStatus(hits, -1, ""), "");
});

test("match ranges never overlap", () => {
  assert.deepEqual(matchRanges("aaaa", "aa"), [[0, 2], [2, 4]]);
  assert.deepEqual(matchRanges("Abc abc", "ABC"), [[0, 3], [4, 7]]);
  assert.deepEqual(matchRanges("abc", ""), []);
});

test("pieces mark matches and keep escapes apart, across a run boundary", () => {
  const pieces = linePieces("go\u001bon", "o\\x1");
  assert.deepEqual(pieces, [
    { text: "g", escaped: false, hit: false },
    { text: "o", escaped: false, hit: true },
    { text: "\\x1", escaped: true, hit: true },
    { text: "b", escaped: true, hit: false },
    { text: "on", escaped: false, hit: false },
  ]);
  // A literal backslash-x typed in the line stays plain text.
  assert.deepEqual(linePieces("\\x1b", ""), [{ text: "\\x1b", escaped: false, hit: false }]);
  assert.equal(linePieces("abc", "zz").map((p) => p.text).join(""), "abc");
});

test("the window covers the view plus overscan, clamped", () => {
  assert.deepEqual(windowOf(0, 400, 20, 10000, 20), [0, 40]);
  assert.deepEqual(windowOf(2000, 400, 20, 10000, 20), [80, 140]);
  assert.deepEqual(windowOf(199000, 400, 20, 10000, 20), [9930, 9990]);
  assert.deepEqual(windowOf(199600, 400, 20, 10000, 20), [9960, 10000]);
  assert.deepEqual(windowOf(0, 400, 20, 0), [0, 0]);
});

test("notes say what was read, redaction and truncation", () => {
  const notes = logNotes({ lines: ["a"], truncated: true, previous: true, format: null }, { tail: 200 });
  assert.equal(notes[0], "1 line read (at most the last 200).");
  assert.ok(notes.some((n) => n.includes("[redacted]")));
  assert.ok(notes.some((n) => n.includes("4 MiB")));
  assert.ok(notes.some((n) => n.includes("previous container")));
  assert.ok(notes.some((n) => n.includes("no level")));
  const plain = logNotes({ lines: ["a", "b"], truncated: false, format: "python" }, { tail: 1000 });
  assert.equal(plain.length, 2);
  assert.deepEqual(logNotes(null, { tail: 200 }), []);
});

test("coded empty states, not errors", () => {
  assert.equal(emptyReason({ code: "LOG_UNAVAILABLE", message: "x" }, null).code, "LOG_UNAVAILABLE");
  assert.equal(emptyReason({ code: "POD_NOT_RUNNING", message: "pending" }, null).text, "pending");
  assert.equal(emptyReason(null, { lines: [], pod_reason: "ContainerCreating" }).text, "The pod has no output yet (ContainerCreating).");
  assert.equal(emptyReason(null, { lines: [] }).code, "EMPTY");
  assert.equal(emptyReason({ code: "NOT_FOUND", message: "x" }, null), null);
  assert.equal(emptyReason(null, { lines: ["a"] }), null);
});

test("cluster text in an empty state is capped", () => {
  const long = "x".repeat(500);
  assert.equal(emptyReason({ code: "POD_NOT_RUNNING", message: long }, null).text.length, 201);
  assert.ok(emptyReason(null, { lines: [], pod_reason: long }).text.endsWith("…)."));
});

// -- follow (M5 PR 2) ---------------------------------------------------------------------------

const reply = (lines, extra = {}) => ({
  data: { running: true, lines, next: lines.length ? lines[lines.length - 1].seq : 0, more: false, missed: 0, dropped: 0, cli_dropped: false, reason: null, stopped: false, ...extra },
  error: null,
});
const row = (seq, level = null) => ({ seq, line: `line ${seq}`, level });

test("follow paths carry only checked values", () => {
  assert.equal(followPath("aaaaaaaaaaaa", { kind: "service", name: "litellm" }), "/api/projects/aaaaaaaaaaaa/logs/service/litellm/follow");
  assert.equal(followPath("aaaaaaaaaaaa", { kind: "deployment", name: ID }), `/api/projects/aaaaaaaaaaaa/logs/deployment/${ID}/follow`);
  assert.equal(followPath("aaaaaaaaaaaa", { kind: "service", name: "../x" }), null);
  assert.deepEqual(followBody({ tail: 1000, since: "", previous: true }), { tail: 1000 });
  assert.deepEqual(followBody({ tail: 200, since: "10m" }), { tail: 200, since: "10m" });
  assert.equal(followReadPath(ID, 12), `/api/follows/${ID}?after=12`);
  for (const [id, after] of [["x/../y", 0], [ID, -1], [ID, 1.5], [ID, "3"]]) assert.equal(followReadPath(id, after), null);
});

test("merging appends new rows once, keeps the newest and counts what was lost", () => {
  let state = followMerge(followInitial(), reply([row(1), row(2, "error")]));
  assert.deepEqual(state.rows.map((r) => r.seq), [1, 2]);
  assert.equal(state.after, 2);
  // A repeated answer (a retried read) adds nothing.
  state = followMerge(state, reply([row(2), row(3)], { missed: 4 }));
  assert.deepEqual(state.rows.map((r) => r.seq), [1, 2, 3]);
  assert.equal(state.missed, 4);
  const many = Array.from({ length: FOLLOW_KEEP }, (_, n) => row(n + 4));
  state = followMerge(state, reply(many));
  assert.equal(state.rows.length, FOLLOW_KEEP);
  assert.equal(state.cut, 3);
  assert.equal(state.rows[0].seq, 4);
  assert.match(followNotes(state).join(" "), /oldest 3 lines were dropped/);
  assert.match(followNotes(state).join(" "), /4 lines arrived faster/);
});

test("pause holds new rows back until Jump to latest", () => {
  let state = followMerge(followInitial(), reply([row(1), row(2)]));
  state = { ...state, pausedAt: 2 };
  state = followMerge(state, reply([row(3), row(4)]));
  assert.deepEqual(followShown(state).map((r) => r.seq), [1, 2]);
  assert.equal(followHeld(state), 2);
  assert.match(followStatus(state), /^Paused/);
  state = { ...state, pausedAt: null };
  assert.equal(followShown(state).length, 4);
  assert.equal(followHeld(state), 0);
  assert.match(followStatus(state), /^Following/);
});

test("the status says how a follow ended", () => {
  const ended = (extra) => followMerge(followInitial(), reply([], { running: false, ...extra }));
  assert.match(followStatus(ended({ reason: "container" })), /container stopped/);
  assert.equal(followStatus(ended({ reason: "stdin", stopped: true }), { asked: true }), "Follow stopped.");
  assert.match(followStatus(ended({ reason: "stdin", stopped: true })), /did not read it for 15 seconds/);
  assert.equal(followStatus(ended({ reason: "other" }), { asked: true }), "Follow ended.");
  const failed = followMerge(followInitial(), { data: { ...reply([]).data, running: false }, error: { code: "DOCKER_LOGS_FAILED", message: "the stream ended" } });
  assert.equal(followStatus(failed), "Follow failed: the stream ended (DOCKER_LOGS_FAILED)");
  const unreadable = followMerge(followInitial(), { data: null, error: null });
  assert.equal(unreadable.running, false);
  assert.match(followStatus(unreadable), /UNREADABLE/);
});

test("follow notes count dropped records and lines", () => {
  const state = followMerge(followInitial(), reply([row(1)], { dropped: 1, cli_dropped: true }));
  const notes = followNotes(state).join(" ");
  assert.match(notes, /1 line kept on this page/);
  assert.match(notes, /1 record did not match/);
  assert.match(notes, /over 1 MiB/);
  assert.match(notes, /\[redacted\]/);
});

test("a follow the server no longer knows says so", () => {
  const gone = followMerge(followInitial(), { data: { running: false, gone: true, lines: [], next: 0 }, error: null });
  assert.match(followStatus(gone), /^The follow is gone/);
});
