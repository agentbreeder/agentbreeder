import assert from "node:assert/strict";
import { test } from "node:test";

import {
  KINDS, compareVersions, deployedLines, diffStats, groupByName, hunkHeader, noHunkReason, parseRef, usedByText, useText, verifyLabel, verifyState,
} from "../../src/agentbreeder/center/static/lib/registry.js";

const ROOTS = [
  { path: "/p/registry", kind: "project", present: true },
  { path: "/shared", kind: "path", present: true },
  { path: "/pkg/builtin_registry", kind: "builtin", present: true },
];

function view({ versions, problems = { data: [], error: null, stale: false }, refs = {} }) {
  return { versions: { data: versions, error: null, stale: false }, problems, roots: ROOTS, in_use: { data: { refs, errors: [] }, error: null } };
}

const row = (ref, root = "/p/registry", symlinked = false) => ({ ref, root, path: `${root}/${ref}`, symlinked });

test("the kinds cover every registry kind once", () => {
  assert.deepEqual(KINDS.map((k) => k.kind).sort(), ["eval", "knowledge", "mcp", "prompt", "runtime", "service", "tool"]);
  assert.equal(new Set(KINDS.map((k) => k.nav)).size, KINDS.length);
  assert.ok(!KINDS.some((k) => k.nav === "Services"), "the stack's Services link keeps its name");
});

test("refs parse and versions sort numerically", () => {
  assert.deepEqual(parseRef("tool/orders@1.10.0"), { kind: "tool", name: "orders", version: "1.10.0" });
  assert.equal(parseRef("tool/*"), null);
  assert.equal(parseRef(null), null);
  assert.ok(compareVersions("1.10.0", "1.9.9") > 0);
  assert.ok(compareVersions("0.1.0", "1.0.0") < 0);
  assert.equal(compareVersions("2.0.0", "2.0.0"), 0);
});

test("one row per name: newest first, first root wins, later copies shadowed", () => {
  const rows = groupByName(view({
    versions: [
      row("prompt/a@1.9.0"), row("prompt/a@1.10.0"), row("prompt/a@1.10.0", "/shared"), row("tool/x@1.0.0"), row("prompt/b@0.1.0", "/pkg/builtin_registry"),
    ],
    refs: { "prompt/a@1.9.0": { declared: ["agent.yaml"], locked: ["agent.lock"] } },
  }), "prompt");
  assert.deepEqual(rows.map((r) => r.name), ["a", "b"]);
  const [a, b] = rows;
  assert.deepEqual(a.versions.map((v) => v.version), ["1.10.0", "1.9.0"]);
  assert.equal(a.latest.version, "1.10.0");
  assert.equal(a.latest.shadowed, 1);
  assert.equal(a.latest.rootKind, "project");
  assert.equal(b.latest.rootKind, "builtin");
  assert.equal(usedByText(a), "1.9.0 (declared, locked)");
  assert.equal(useText(a.versions[1]), "declared in agent.yaml; locked in agent.lock");
  assert.equal(usedByText(b), "");
  assert.equal(verifyLabel(a), "Passed");
});

test("verify problems mark the version, a symlinked pattern every version under it", () => {
  const problems = { data: [{ ref: "tool/x@1.0.0", error: "[HASH_MISMATCH] changed" }, { ref: "tool/y@*", error: "[ENTRY_UNSAFE] link" }], error: null, stale: false };
  const rows = groupByName(view({ versions: [row("tool/x@1.0.0"), row("tool/x@1.1.0"), row("tool/y@1.0.0"), row("tool/y@2.0.0"), row("tool/z@1.0.0", "/p/registry", true)], problems }), "tool");
  const [x, y, z] = rows;
  assert.equal(x.verify, "failed");
  assert.equal(x.latest.verify, "passed");
  assert.equal(x.versions[1].problem, "[HASH_MISMATCH] changed");
  assert.equal(verifyLabel(y), "2 versions failed");
  assert.equal(z.verify, "failed");
  assert.match(z.latest.problem, /symlinked/);
});

test("verify still running or failed is said, not shown as passed", () => {
  const verifying = { data: null, error: { code: "VERIFYING", message: "Verifying" }, stale: false };
  assert.equal(verifyState(verifying).state, "verifying");
  const [a] = groupByName(view({ versions: [row("eval/a@1.0.0")], problems: verifying }), "eval");
  assert.equal(verifyLabel(a), "Verifying…");
  const failed = { data: null, error: { code: "TIMEOUT", message: "The command timed out" }, stale: false };
  assert.deepEqual(verifyState(failed), { state: "unknown", stale: false, problems: null, message: "The command timed out" });
  const [b] = groupByName(view({ versions: [row("eval/a@1.0.0")], problems: failed }), "eval");
  assert.equal(verifyLabel(b), "Not verified");
  assert.equal(verifyState({ data: [], error: { code: "TIMEOUT" }, stale: true }).stale, true);
});

test("a missing or malformed view gives no rows", () => {
  assert.deepEqual(groupByName(null, "tool"), []);
  assert.deepEqual(groupByName({ versions: { data: null } }, "tool"), []);
  assert.deepEqual(groupByName({ versions: { data: [{ ref: "nope" }, null] } }, "tool"), []);
});

test("an in_use key from the prototype is never used", () => {
  const [a] = groupByName(view({ versions: [row("tool/constructor@1.0.0")], refs: {} }), "tool");
  assert.deepEqual(a.latest.declared, []);
});

test("diff wording: counts, hunk headers and why a file has no hunks", () => {
  const result = { files: [{ hunks: [{ lines: [{ op: " " }, { op: "-" }, { op: "+" }, { op: "+" }] }] }, { hunks: [] }] };
  assert.deepEqual(diffStats(result), { added: 2, removed: 1 });
  assert.deepEqual(diffStats(null), { added: 0, removed: 0 });
  assert.equal(hunkHeader({ from_start: 5, from_count: 7, to_start: 5, to_count: 1 }), "@@ -5,7 +5 @@");
  assert.equal(noHunkReason({ type: "file", hunks: [{}] }), null);
  assert.equal(noHunkReason({ type: "dir" }), null);
  assert.match(noHunkReason({ type: "file", hunks: [], binary: true }), /Binary/);
  assert.match(noHunkReason({ type: "file", hunks: [], too_large: true }), /registry diff/);
  assert.match(noHunkReason({ type: "file", status: "modified", hunks: [], executable: { from: false, to: true } }), /executable bit changed \(off → on\)/);
  assert.match(noHunkReason({ type: "file", status: "modified", hunks: [], executable: { from: true, to: true }, stripped: true }), /control/);
  assert.equal(noHunkReason({ type: "file", status: "added", hunks: [], executable: { from: null, to: false } }), "Empty file added.");
});

test("deployments of a used-by row as lines", () => {
  const lines = deployedLines({
    deployed: [{ name: "nw", deployment: "0123456789abcdef", state: "running", target: "local", environment: "dev" }],
    recorded_not_running: [{ name: "nw", deployment: "ab", state: "stopped", target: "kind" }],
    deployed_undetermined: [{ name: "nw", deployment: "cd", state: "running", reason: "no build record" }],
  });
  assert.deepEqual(lines, [
    "Running: nw 0123456789ab running (local, dev)",
    "Recorded, not running: nw ab stopped (kind)",
    "Undetermined: nw cd running: no build record",
  ]);
  assert.deepEqual(deployedLines(null), []);
});
