import assert from "node:assert/strict";
import { test } from "node:test";

import {
  emptyReason, levelShown, linePieces, logNotes, logsPath, matchRanges, searchHits, searchStatus,
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
