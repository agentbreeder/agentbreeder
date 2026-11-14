import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  CHECKS, MAX_CASES, applyForm, caseAtLine, caseForm, caseProblems, casesFile, newCase, parseCases, pyJson, serializeCases,
} from "../../src/agentbreeder/center/static/lib/cases.js";
import { EDITABLE_KINDS, editKind, editableFiles, notEditableReason } from "../../src/agentbreeder/center/static/lib/edit.js";

const NORTHWIND = new URL("../../src/agentbreeder/examples/northwind/registry/eval/northwind-golden/1.0.1/golden.jsonl", import.meta.url);
const STARTER =
  '{"id": "hours", "turns": ["When is support open?"], "expect": {"must_match": ["9"]}, "reference": "Support is open 9am to 5pm Pacific, Monday to Friday."}\n' +
  '{"id": "unknown", "turns": ["What is Acme\'s stock price today?"], "expect": {}, "reference": "The agent says it does not know rather than inventing a number."}\n';

test("an unchanged file is written back byte for byte, and a form round trip changes nothing", () => {
  for (const text of [readFileSync(NORTHWIND, "utf8"), STARTER, "\n{\"id\": \"a\"}\n\n", "{\"id\":\"compact\"}"]) {
    const { entries, ending, error } = parseCases(text);
    assert.equal(error, null);
    assert.equal(serializeCases(entries, ending), text);
  }
  // Every Northwind and starter case (string turns, a new-session object turn, category, an
  // empty expect) comes back as the same line after the form is applied unchanged.
  for (const text of [readFileSync(NORTHWIND, "utf8"), STARTER]) {
    for (const entry of parseCases(text).entries.filter((e) => e.data)) {
      assert.equal(pyJson(applyForm(entry.data, caseForm(entry.data))), entry.raw);
    }
  }
});

test("pyJson writes what Python's json.dumps writes", () => {
  // json.dumps({"id": "x", "s": "é😀\x01\"\\\t/\x7f", "n": [1, 2.5, True, None]})
  const value = { id: "x", s: "é😀\u0001\"\\\t/\u007f", n: [1, 2.5, true, null] };
  assert.equal(pyJson(value), '{"id": "x", "s": "\\u00e9\\ud83d\\ude00\\u0001\\"\\\\\\t/\\u007f", "n": [1, 2.5, true, null]}');
});

test("a file that is not JSON lines says which line", () => {
  assert.equal(parseCases('{"id": "a"}\nnot json\n').error, "line 2 is not JSON");
  assert.equal(parseCases("[1]\n").error, "line 1 is not a JSON object");
  assert.deepEqual(parseCases("").entries, []);
});

test("a form edit changes only that case's fields and keeps the others", () => {
  const data = { id: "a", category: "refunds", turns: ["hi", { new_session: true, message: "again" }], expect: { must_match: ["x"], custom: 1 }, reference: "r", extra: true };
  const form = caseForm(data);
  assert.deepEqual(form.turns, [{ text: "hi" }, { text: "again", data: { new_session: true, message: "again" } }]);
  form.turns[1].text = "  once more ";
  form.must_match = "x\n\n y \r\n";
  form.sources_any = "";
  form.tools_include = "escalate_to_human";
  form.reference = " new ";
  const next = applyForm(data, form);
  assert.deepEqual(Object.keys(next), ["id", "category", "turns", "expect", "reference", "extra"]);
  assert.deepEqual(next.turns, ["hi", { new_session: true, message: "once more" }]);
  // Items are kept as typed (a pattern's spaces can matter); blank lines and a CR are dropped.
  assert.deepEqual(next.expect, { must_match: ["x", " y "], custom: 1, tools_include: ["escalate_to_human"] });
  assert.equal(next.reference, "new"); // a changed reference is trimmed
  // An empty check list is left out (`sources_any: []` would add a check that always fails).
  assert.ok(!("sources_any" in next.expect));
  // An emptied text turn is dropped; an object turn with no message is kept as it is.
  assert.deepEqual(applyForm({ turns: ["a", { tool: 1 }] }, { ...caseForm({ turns: ["a", { tool: 1 }] }), turns: [{ text: "" }, { text: null, data: { tool: 1 } }] }).turns, [{ tool: 1 }]);
  assert.deepEqual(CHECKS.map((c) => c.key), ["must_match", "must_not_match", "tools_include", "tools_exclude", "sources_any"]);
});

test("a field the form did not change keeps its original value exactly", () => {
  const data = { id: 7, turns: [" hi "], expect: { must_match: [" 30 days", 5], sources_any: [] }, reference: " r " };
  const form = caseForm(data);
  assert.equal(form.id, "7");
  form.tools_include = "escalate";
  const next = applyForm(data, form);
  assert.equal(next.id, 7);
  assert.deepEqual(next.turns, [" hi "]);
  assert.deepEqual(next.expect, { must_match: [" 30 days", 5], sources_any: [], tools_include: ["escalate"] });
  assert.equal(next.reference, " r ");
  // Edited back to what it was: the same data, so the editor can write the original line again.
  assert.equal(pyJson(applyForm(data, caseForm(data))), pyJson(data));
});

test("the cases file is the one the manifest names", () => {
  const show = (text) => ({ files: [{ type: "file", path: "manifest.yaml", text }] });
  assert.equal(casesFile(show("kind: eval\ncases: golden.jsonl\n")), "golden.jsonl");
  assert.equal(casesFile(show("kind: eval\ncases: 'set.jsonl'  # x\n")), "set.jsonl");
  assert.equal(casesFile(show('cases: "a b.jsonl"\n')), "a b.jsonl");
  assert.equal(casesFile(show("kind: eval\n")), "golden.jsonl"); // the loader's default
  assert.equal(casesFile({ files: [] }), null);
  for (const empty of ["cases:\n", "cases: null\n", "cases: ~\n", 'cases: ""\n']) assert.equal(casesFile(show(empty)), "golden.jsonl", empty);
  // A manifest that names a .json file: the console offers no file (the server edits only .jsonl).
  const json = { files: [...show("cases: set.json\n").files, { type: "file", path: "set.json", text: "{}" }] };
  assert.deepEqual(editableFiles(json, "eval"), []);
});

test("an edited case is serialised, the others keep their bytes", () => {
  const { entries, ending } = parseCases(STARTER);
  const edited = entries.map((entry, i) => (i === 1 ? { raw: null, data: { ...entry.data, reference: "Says it does not know." } } : entry));
  const lines = serializeCases(edited, ending).split("\n");
  assert.equal(lines[0], STARTER.split("\n")[0]);
  assert.equal(lines[1], '{"id": "unknown", "turns": ["What is Acme\'s stock price today?"], "expect": {}, "reference": "Says it does not know."}');
});

test("case problems follow the loader's rules and name what to fix", () => {
  const { entries } = parseCases(STARTER);
  assert.equal(caseProblems(entries).count, 0);
  const bad = [
    { data: { id: "a", turns: ["q"], reference: "r" } },
    { data: { id: "a", turns: [" "], reference: "" } },
    { raw: "" },
    { data: { turns: [{ new_session: true, message: "hi" }], reference: "r" } },
  ];
  const problems = caseProblems(bad);
  const texts = (list) => list.map((p) => `${p.field}: ${p.text}`);
  assert.deepEqual(problems.cases.map(texts), [[], ["id: has the same id as case 1", "turns: needs at least one turn", "reference: needs a reference answer"], ["id: needs an id"]]);
  // A number id is an id (the loader needs only the key).
  assert.equal(caseProblems([{ data: { id: 5, turns: ["q"], reference: "r" } }]).count, 0);
  assert.equal(problems.count, 2);
  assert.equal(caseProblems([]).set, "The eval set needs at least one case.");
  const many = Array.from({ length: MAX_CASES + 1 }, (_, i) => ({ data: { id: `c${i}`, turns: ["q"], reference: "r" } }));
  assert.match(caseProblems(many).set, /more than 1000/);
});

test("a new case takes the next free id", () => {
  assert.deepEqual(newCase([{ data: { id: "case-1" } }, { raw: "" }]), { id: "case-2", turns: [], expect: {}, reference: "" });
});

test("an EVAL_INVALID line maps to its case, counting blank lines as the CLI does", () => {
  const { entries } = parseCases('{"id": "a"}\n\n{"id": "b"}\n');
  assert.equal(caseAtLine(entries, "the edited eval set does not load: golden.jsonl:3 needs id, turns, reference", "golden.jsonl"), 1);
  assert.equal(caseAtLine(entries, "golden.jsonl:2 is not JSON", "golden.jsonl"), null); // a blank line
  assert.equal(caseAtLine(entries, "other.jsonl:1 is not JSON", "golden.jsonl"), null);
  assert.equal(caseAtLine(entries, "eval manifest needs judge.alias", "golden.jsonl"), null);
});

test("each edit kind names its files; eval sets edit only top-level .jsonl files", () => {
  assert.deepEqual(EDITABLE_KINDS, ["prompt", "eval", "tool"]);
  assert.equal(editKind("eval").body, "cases");
  assert.equal(editKind("prompt").body, "text");
  assert.equal(editKind("tool").body, "text");
  assert.equal(editKind("service"), null);
  assert.equal(editKind("__proto__"), null);
  const file = (path, more = {}) => ({ type: "file", path, text: "x", ...more });
  const show = { files: [file("manifest.yaml", { text: "cases: golden.jsonl\n" }), file("golden.jsonl"), file("other.jsonl"), file("fixtures.sql"), file("more/cases.jsonl"), file("big.jsonl", { truncated: true })] };
  assert.deepEqual(editableFiles(show, "eval").map((f) => f.path), ["golden.jsonl"]);
  assert.deepEqual(editableFiles(show, "prompt").map((f) => f.path), ["fixtures.sql", "golden.jsonl", "more/cases.jsonl", "other.jsonl"]);
  assert.deepEqual(editableFiles(show, "tool"), []);
  assert.equal(notEditableReason(file("other.jsonl"), "eval", show), "the console edits only the cases file its manifest names");
  assert.equal(notEditableReason(file("golden.jsonl"), "eval", show), null);
  // Without the manifest, any top-level .jsonl (the server's own rule).
  assert.deepEqual(editableFiles({ files: show.files.slice(1) }, "eval").map((f) => f.path), ["golden.jsonl", "other.jsonl"]);
  assert.equal(notEditableReason(file("manifest.yaml"), "eval"), null);
  assert.equal(notEditableReason(file("big.jsonl", { truncated: true }), "eval"), "over the size the console shows");
});
