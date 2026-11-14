// The registry browser's data, as pure functions (#824): the project's `registry` view (`registry
// list`, `registry verify`, the search roots and which versions the project declares and locks)
// grouped into one row per name, and the wording for a version's state. Nothing here touches the
// DOM.

/** The kinds, in the sidebar's order: `path` is the route segment, `nav` the sidebar label. */
const LABELS = {
  prompt: { path: "prompts", nav: "Prompts", one: "Prompt" },
  eval: { path: "evals", nav: "Eval sets", one: "Eval set" },
  tool: { path: "tools", nav: "Tools", one: "Tool" },
  knowledge: { path: "knowledge", nav: "Knowledge", one: "Knowledge source" },
  mcp: { path: "mcp", nav: "MCP servers", one: "MCP server" },
  // "Services" alone would repeat the stack's Services link with another destination.
  service: { path: "services", nav: "Service definitions", one: "Service definition" },
  runtime: { path: "runtimes", nav: "Runtimes", one: "Runtime" },
};
export const KINDS = Object.entries(LABELS).map(([kind, meta]) => ({ kind, ...meta }));

export const ROOT_LABELS = { project: "project", builtin: "built-in", path: "shared path" };

const REF = /^([a-z]+)\/([a-z0-9][a-z0-9-]{0,62})@([0-9.]+)$/;

/** `kind/name@1.2.3` as {kind, name, version}, or null. */
export function parseRef(text) {
  const match = REF.exec(String(text || ""));
  return match ? { kind: match[1], name: match[2], version: match[3] } : null;
}

/** Semantic-version order (numeric per part); a negative number when a sorts first. */
export function compareVersions(a, b) {
  const left = String(a).split(".").map(Number);
  const right = String(b).split(".").map(Number);
  for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
    const diff = (left[i] || 0) - (right[i] || 0);
    if (diff) return diff;
  }
  return 0;
}

/**
 * What `registry verify` said about one version: a problem row names its ref, or a pattern for a
 * symlinked directory (`tool/*`, `tool/name@*`) that fails every ref under it.
 */
function problemFor(problems, kind, name, version) {
  if (!Array.isArray(problems)) return null;
  const ref = `${kind}/${name}@${version}`;
  for (const problem of problems) {
    const at = problem && typeof problem.ref === "string" ? problem.ref : "";
    if (at === ref || at === `${kind}/*` || at === `${kind}/${name}@*`) return String(problem.error || "failed verification");
  }
  return null;
}

/**
 * The verify state of the whole registry view: "passed" (verify answered), "verifying" (the first
 * run has not answered yet) or "unknown" (it failed; `message` says why). `stale` marks an earlier
 * answer shown while a new run is going.
 */
export function verifyState(problemsEnvelope) {
  const envelope = problemsEnvelope || {};
  if (Array.isArray(envelope.data)) return { state: "answered", stale: Boolean(envelope.stale), problems: envelope.data };
  const error = envelope.error || {};
  if (error.code === "VERIFYING") return { state: "verifying", stale: false, problems: null };
  return { state: "unknown", stale: false, problems: null, message: error.message || "Verification did not run." };
}

/**
 * One row per name of `kind`: its versions newest first (each resolved from the first root in
 * search order that holds it, as `resolve` does; later copies count as `shadowed`), the latest
 * version, its root, verify status and where the project uses it.
 */
export function groupByName(view, kind) {
  const versions = (view && view.versions && Array.isArray(view.versions.data) && view.versions.data) || [];
  const roots = (view && Array.isArray(view.roots) && view.roots) || [];
  const rootKind = new Map(roots.map((root) => [root.path, root.kind]));
  const verify = verifyState(view && view.problems);
  const inUse = (view && view.in_use && view.in_use.data && view.in_use.data.refs) || {};
  const names = new Map();
  for (const row of versions) {
    const ref = parseRef(row && row.ref);
    if (!ref || ref.kind !== kind) continue;
    let entry = names.get(ref.name);
    if (!entry) names.set(ref.name, (entry = { name: ref.name, versions: [] }));
    const seen = entry.versions.find((v) => v.version === ref.version);
    if (seen) {
      seen.shadowed += 1;
      continue;
    }
    const problem = row.symlinked ? "Under a symlinked directory: every ref under it is refused." : problemFor(verify.problems, kind, ref.name, ref.version);
    const use = Object.prototype.hasOwnProperty.call(inUse, row.ref) ? inUse[row.ref] : null;
    entry.versions.push({
      ref: row.ref,
      version: ref.version,
      root: row.root,
      rootKind: rootKind.get(row.root) || "path",
      symlinked: Boolean(row.symlinked),
      shadowed: 0,
      verify: problem ? "failed" : verify.state === "answered" ? "passed" : verify.state,
      problem,
      declared: (use && Array.isArray(use.declared) && use.declared) || [],
      locked: (use && Array.isArray(use.locked) && use.locked) || [],
    });
  }
  const rows = [...names.values()];
  for (const entry of rows) {
    entry.versions.sort((a, b) => compareVersions(b.version, a.version));
    entry.latest = entry.versions[0];
    const failed = entry.versions.filter((v) => v.verify === "failed").length;
    entry.verify = failed ? "failed" : entry.latest.verify;
    entry.failed = failed;
    entry.used = entry.versions.filter((v) => v.declared.length || v.locked.length);
  }
  rows.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  return rows;
}

/** The verify column in words; never colour alone. */
export function verifyLabel(entry) {
  if (!entry) return "";
  if (entry.verify === "failed") return entry.failed > 1 ? `${entry.failed} versions failed` : "Failed";
  if (entry.verify === "passed") return "Passed";
  if (entry.verify === "verifying") return "Verifying…";
  return "Not verified";
}

/** Where this project uses a version: "declared in agent.yaml, locked in agent.lock". */
export function useText(version) {
  if (!version) return "";
  const parts = [];
  if (version.declared.length) parts.push(`declared in ${version.declared.join(", ")}`);
  if (version.locked.length) parts.push(`locked in ${version.locked.join(", ")}`);
  return parts.join("; ");
}

/** The used-by column for a name: each used version and how, or "". */
export function usedByText(entry) {
  if (!entry || !entry.used || !entry.used.length) return "";
  return entry.used.map((v) => `${v.version} (${[v.declared.length ? "declared" : "", v.locked.length ? "locked" : ""].filter(Boolean).join(", ")})`).join(", ");
}

/** Lines added and removed in a `registry diff --json` answer. */
export function diffStats(result) {
  let added = 0;
  let removed = 0;
  for (const file of (result && Array.isArray(result.files) && result.files) || []) {
    for (const hunk of (Array.isArray(file.hunks) && file.hunks) || []) {
      for (const line of (Array.isArray(hunk.lines) && hunk.lines) || []) {
        if (line.op === "+") added += 1;
        else if (line.op === "-") removed += 1;
      }
    }
  }
  return { added, removed };
}

/** Why a changed file has no hunks, in words, or null when it has some. */
export function noHunkReason(file) {
  if (!file || file.type === "dir") return null;
  if (Array.isArray(file.hunks) && file.hunks.length) return null;
  if (file.binary) return "Binary file: not shown as text.";
  if (file.too_large) return "Too large to compare here; run `agentbreeder registry diff` in a terminal.";
  if (file.truncated || file.omitted) return "Over the size cap: not shown as text.";
  const exec = file.executable || {};
  if (exec.from !== exec.to && file.status === "modified") return `Only the executable bit changed (${exec.from ? "on" : "off"} → ${exec.to ? "on" : "off"}).`;
  if (file.stripped) return "Only control or invisible characters changed.";
  if (file.status === "added") return "Empty file added.";
  if (file.status === "removed") return "Empty file removed.";
  return "Changed in a way not shown as text.";
}

/** A hunk header as diff(1) prints it: `@@ -1,3 +1,4 @@`. */
export function hunkHeader(hunk) {
  const side = (start, count) => (count === 1 ? `${start}` : `${start},${count}`);
  return `@@ -${side(hunk.from_start, hunk.from_count)} +${side(hunk.to_start, hunk.to_count)} @@`;
}

/** Deployments of a used-by row (`registry used-by --json`), as short lines. */
export function deployedLines(row) {
  const lines = [];
  const one = (entry, how) => {
    const where = [entry.target, entry.environment].filter(Boolean).join(", ");
    lines.push(`${how}: ${entry.name || ""} ${String(entry.deployment || "").slice(0, 12)} ${entry.state || ""}${where ? ` (${where})` : ""}${entry.reason ? `: ${entry.reason}` : ""}`.replace(/\s+/g, " ").trim());
  };
  for (const entry of (row && row.deployed) || []) one(entry, "Running");
  for (const entry of (row && row.recorded_not_running) || []) one(entry, "Recorded, not running");
  for (const entry of (row && row.deployed_undetermined) || []) one(entry, "Undetermined");
  return lines;
}
