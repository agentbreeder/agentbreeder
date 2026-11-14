import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BUMPS, EDITABLE_KINDS, agentUses, bumped, editKind, editProblem, editableFile, editableFiles, gutterLines, isSaveShortcut, movableFrom, needsNewReview,
  notEditableReason, saveVersion,
} from "../../src/agentbreeder/center/static/lib/edit.js";

test("bumped raises one part and resets the lower ones", () => {
  assert.equal(bumped("0.1.0", "patch"), "0.1.1");
  assert.equal(bumped("0.1.9", "minor"), "0.2.0");
  assert.equal(bumped("1.4.2", "major"), "2.0.0");
  assert.equal(bumped("0.1.0", "anything"), "0.1.1");
  assert.equal(bumped("1.2", "patch"), null);
  assert.equal(bumped(undefined, "patch"), null);
  assert.deepEqual(BUMPS, ["patch", "minor", "major"]);
  assert.deepEqual(EDITABLE_KINDS, ["prompt", "eval", "tool"]); // PR 2 adds eval sets (#826)
});

test("saveVersion skips versions the name already has", () => {
  assert.equal(saveVersion("0.1.0", "patch", ["0.1.0"]), "0.1.1");
  assert.equal(saveVersion("0.1.0", "patch", ["0.1.0", "0.1.1", "0.1.2"]), "0.1.3");
  assert.equal(saveVersion("0.1.0", "minor", ["0.2.0"]), "0.2.1");
  assert.equal(saveVersion("bad", "patch", []), null);
});

const file = (path, extra = {}) => ({ path, type: "file", binary: false, truncated: false, omitted: false, stripped: false, text: "x", ...extra });

test("only exact text files other than the manifest are editable", () => {
  assert.equal(editableFile(file("prompt.md")), true);
  assert.equal(editableFile(file("manifest.yaml")), false);
  assert.equal(editableFile(file("MANIFEST.YAML")), false);
  assert.equal(editableFile(file("a.bin", { binary: true, text: null })), false);
  assert.equal(editableFile(file("big.md", { truncated: true })), false);
  assert.equal(editableFile(file("late.md", { omitted: true, text: null })), false);
  assert.equal(editableFile(file("ctl.md", { stripped: true })), false);
  assert.equal(editableFile({ path: "dir", type: "dir" }), false);
  assert.equal(editableFile(null), false);
  assert.equal(notEditableReason(file("a.bin", { binary: true, text: null })), "binary");
  assert.equal(notEditableReason(file("big.md", { truncated: true })), "over the size the console shows");
  assert.match(notEditableReason(file("ctl.md", { stripped: true })), /control or invisible/);
  assert.equal(notEditableReason(file("x.json", { text: null })), "not text");
  assert.equal(notEditableReason(file("prompt.md")), null);
  assert.equal(notEditableReason(file("manifest.yaml")), null);
  assert.equal(notEditableReason({ path: "d", type: "dir" }), null);
});

test("editableFiles puts markdown and text first, then by path", () => {
  const show = { files: [file("manifest.yaml"), file("z.json"), file("b.md"), file("a.txt"), { path: "d", type: "dir" }] };
  assert.deepEqual(editableFiles(show).map((f) => f.path), ["a.txt", "b.md", "z.json"]);
  assert.deepEqual(editableFiles(null), []);
});

test("gutterLines has one number per line", () => {
  assert.equal(gutterLines(""), "1");
  assert.equal(gutterLines("a\nb\n"), "1\n2\n3");
  assert.equal(gutterLines(undefined), "1");
});

test("the save shortcut is Cmd+S or Ctrl+S only", () => {
  assert.equal(isSaveShortcut({ key: "s", metaKey: true }), true);
  assert.equal(isSaveShortcut({ key: "S", ctrlKey: true }), true);
  assert.equal(isSaveShortcut({ key: "s" }), false);
  assert.equal(isSaveShortcut({ key: "s", ctrlKey: true, shiftKey: true }), false);
  assert.equal(isSaveShortcut({ key: "s", metaKey: true, altKey: true }), false);
  assert.equal(isSaveShortcut({ key: "a", metaKey: true }), false);
  assert.equal(isSaveShortcut(null), false);
});

test("movableFrom lists the other versions agent.yaml declares, newest first", () => {
  const v = (version, declared = []) => ({ version, ref: `prompt/p@${version}`, declared });
  const entry = { versions: [v("0.1.2"), v("0.1.10", ["agent.yaml"]), v("0.1.1", ["agent.yaml", "stack.yaml"]), v("0.1.0", ["stack.yaml"])] };
  assert.deepEqual(movableFrom(entry, v("0.1.2")).map((x) => x.version), ["0.1.10", "0.1.1"]);
  assert.deepEqual(movableFrom(entry, entry.versions[1]).map((x) => x.version), ["0.1.1"]);
  assert.deepEqual(movableFrom(null, v("1.0.0")), []);
  assert.deepEqual(movableFrom({ versions: [{ version: "1.0.0" }] }, v("2.0.0")), []);
  assert.equal(agentUses(entry.versions[1]), true);
  assert.equal(agentUses(entry.versions[3]), false);
  assert.equal(agentUses({}), false);
  assert.equal(agentUses(null), false);
});

test("refusals a person can act on get fixed words; others keep the message", () => {
  assert.match(editProblem({ code: "VERSION_EXISTS", message: "raw" }).text, /another tab/);
  assert.match(editProblem({ code: "DIGEST_MISMATCH", message: "raw" }).text, /Review the changes again/);
  assert.match(editProblem({ code: "BASE_MISMATCH", message: "raw" }).text, /agent.yaml changed/);
  assert.match(editProblem({ code: "EDIT_UNCHANGED", message: "raw" }).text, /Nothing changed/);
  assert.deepEqual(editProblem({ code: "WRITE_BUSY", message: "Another save is in progress" }), { code: "WRITE_BUSY", text: "Another save is in progress" });
  assert.deepEqual(editProblem({ code: "REF_AMBIGUOUS", message: "twice" }), { code: "REF_AMBIGUOUS", text: "twice" });
  assert.deepEqual(editProblem({}), { code: null, text: "The request failed." });
  assert.equal(editProblem(null), null);
  assert.equal(needsNewReview({ code: "VERSION_EXISTS" }), true);
  assert.equal(needsNewReview({ code: "DIGEST_MISMATCH" }), true);
  assert.equal(needsNewReview({ code: "BASE_MISMATCH" }), true);
  assert.equal(needsNewReview({ code: "EDIT_INVALID" }), false);
  assert.equal(needsNewReview(null), false);
});

test("a tool edits only Python under src/, with the code confirm", () => {
  assert.ok(EDITABLE_KINDS.includes("tool"));
  const tool = editKind("tool");
  assert.equal(tool.confirm, "code");
  assert.equal(tool.body, "text");
  for (const path of ["src/orders.py", "src/pkg/mod.py"]) assert.equal(tool.file(path), true, path);
  for (const path of ["tool.yaml", "orders.py", "src/notes.txt", "src/x.py/y", "requirements.txt"]) assert.equal(tool.file(path), false, path);
  const show = {
    kind: "tool",
    files: [
      { type: "file", path: "manifest.yaml", text: "kind: tool\n" },
      { type: "file", path: "tool.yaml", text: "functions: []\n" },
      { type: "file", path: "src/orders.py", text: "def f():\n    pass\n" },
    ],
  };
  assert.deepEqual(editableFiles(show, "tool").map((file) => file.path), ["src/orders.py"]);
  assert.equal(notEditableReason(show.files[1], "tool", show), "the console edits only Python source files (src/**/*.py)");
  assert.match(tool.note, /quality gate, not a security review/);
});

test("a fork refusal has its own words", () => {
  assert.equal(editProblem({ code: "FORK_UNCHANGED", message: "x" }).text, "Fork it as a new version.");
  assert.equal(editProblem({ code: "TOOL_INVALID", message: "lookup_orders is not a top-level def" }).text, "lookup_orders is not a top-level def");
});
