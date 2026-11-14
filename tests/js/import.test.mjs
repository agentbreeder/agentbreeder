import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CODE_KINDS, detailRoute, formProblem, importBody, importProblem, needsNewReview, reviewFiles, reviewMatches, reviewSummary, runsCode, terminalCommand,
} from "../../src/agentbreeder/center/static/lib/import.js";

const DATA = {
  ref: "prompt/support-copy@1.0.0", kind: "prompt", name: "support-copy", version: "1.0.0",
  source_ref: "prompt/northwind-support@1.0.0", runs_code: false,
  files: [{ path: "docs", type: "dir" }, { path: "manifest.yaml", type: "file" }, { path: "prompt.md", type: "file" }],
};

test("the body trims fields and leaves out empty ones", () => {
  assert.deepEqual(importBody({ path: " /a/b ", name: "", version: " " }), { path: "/a/b" });
  assert.deepEqual(importBody({ path: "/a", name: "x", version: "1.0.0" }), { path: "/a", name: "x", version: "1.0.0" });
});

test("Review is off with a reason until the form can be sent", () => {
  assert.match(formProblem({ path: "" }), /absolute path first/);
  assert.match(formProblem({ path: "relative" }), /starts with \//);
  assert.match(formProblem({ path: "-rf" }), /starts with \//);
  assert.match(formProblem({ path: "/a", name: "Bad Name" }), /lowercase/);
  assert.match(formProblem({ path: "/a", version: "1.0" }), /X\.Y\.Z/);
  assert.equal(formProblem({ path: "/a", name: "ok-name", version: "1.2.0" }), false);
});

test("a review matches only the form it was made for", () => {
  const review = { body: importBody({ path: "/a", version: "1.1.0" }), data: DATA };
  assert.equal(reviewMatches(review, { path: "/a ", version: "1.1.0" }), true);
  assert.equal(reviewMatches(review, { path: "/a", version: "1.2.0" }), false);
  assert.equal(reviewMatches(review, { path: "/b", version: "1.1.0" }), false);
  assert.equal(reviewMatches(review, { path: "/a", name: "x", version: "1.1.0" }), false);
  assert.equal(reviewMatches(null, { path: "/a" }), false);
});

test("the review lists files and names a rename", () => {
  assert.deepEqual(reviewFiles(DATA).map((f) => f.path), ["manifest.yaml", "prompt.md"]);
  assert.deepEqual(reviewFiles({}), []);
  assert.equal(reviewSummary(DATA), "Imports 2 files into this project's registry as prompt/support-copy@1.0.0 (from prompt/northwind-support@1.0.0).");
  const same = { ...DATA, source_ref: DATA.ref, files: [{ path: "manifest.yaml", type: "file" }] };
  assert.equal(reviewSummary(same), "Imports 1 file into this project's registry as prompt/support-copy@1.0.0.");
});

test("code-bearing kinds take the code confirm", () => {
  assert.deepEqual(CODE_KINDS, ["tool", "service", "eval", "mcp", "runtime"]);
  for (const kind of CODE_KINDS) assert.equal(runsCode({ kind, runs_code: false }), true, kind);
  assert.equal(runsCode({ kind: "prompt", runs_code: true }), true);
  assert.equal(runsCode({ kind: "prompt", runs_code: false }), false);
  assert.equal(runsCode({ kind: "knowledge" }), false);
  assert.equal(runsCode(null), false);
});

test("the imported name links to its registry page", () => {
  assert.deepEqual(detailRoute(DATA), { pattern: "/registry/prompts/:name", params: { name: "support-copy" } });
  assert.deepEqual(detailRoute({ kind: "service", name: "pg" }).pattern, "/registry/services/:name");
  assert.equal(detailRoute({ kind: "agent", name: "x" }), null);
});

test("refusals get fixed words where a person can act", () => {
  assert.match(importProblem({ code: "DIGEST_MISMATCH", message: "x" }).text, /changed after your review/);
  assert.match(importProblem({ code: "VERSION_EXISTS", message: "x" }).text, /another version/);
  assert.match(importProblem({ code: "CODE_NOT_CONFIRMED" }).text, /Tick the box/);
  assert.equal(importProblem({ code: "MANIFEST_INVALID", message: "manifest.yaml is missing" }).text, "manifest.yaml is missing");
  assert.equal(importProblem({}).text, "The request failed.");
  assert.equal(importProblem(null), null);
  assert.equal(needsNewReview({ code: "DIGEST_MISMATCH" }), true);
  assert.equal(needsNewReview({ code: "MANIFEST_INVALID" }), false);
});

test("the terminal command mirrors the form", () => {
  assert.equal(terminalCommand({ path: "/a", name: "x", version: "1.0.1" }), "agentbreeder registry import --as x --version 1.0.1 --dry-run /a");
  assert.equal(terminalCommand({}), "agentbreeder registry import --dry-run PATH");
  assert.equal(terminalCommand({ path: "/my dir/it's" }), "agentbreeder registry import --dry-run '/my dir/it'\\''s'");
});
