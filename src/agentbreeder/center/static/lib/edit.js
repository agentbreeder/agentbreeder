// Prompt and eval-set editing and "Move agent to this version" (#826, M10), as pure functions:
// which files of a version the editor can open, the version a save writes, the save shortcut, the
// line gutter and the wording of the CLI's refusals. Nothing here touches the DOM.

import { casesFile } from "./cases.js";
import { compareVersions } from "./registry.js";

/**
 * The kinds the console edits, one entry each (the server's `center/edits.py` KINDS, which checks
 * the same paths again). `file(path, show)`: the files of a version the editor opens (`files`, in
 * words). `body`: how the
 * file is edited ("text", the mono textarea; "cases", the eval case editor). `confirm`: null, or
 * "code" for files that run in agents (the save then needs the "this code runs in agents"
 * checkbox, sent as `code_confirmed`). `note`: what a saved change does. M12 adds `tool`.
 */
export const EDIT_KINDS = {
  prompt: {
    file: () => true,
    files: "text files",
    body: "text",
    confirm: null,
    note: "A prompt steers the agent: review the change, and it takes effect only after the agent is moved to the new version and a release passes the eval gate.",
  },
  eval: {
    // The cases file the manifest names (the one the review's load checks), else any top-level .jsonl.
    file: (path, show) => /^[^/]+\.jsonl$/.test(path) && (!casesFile(show) || path === casesFile(show)),
    files: "the cases file its manifest names",
    body: "cases",
    confirm: null,
    note: "These cases are the release's eval gate: a release of an agent that uses the new version must pass them. The review loads the edited set the way a release does.",
  },
};

/** Kinds the console edits. */
export const EDITABLE_KINDS = Object.keys(EDIT_KINDS);

/** The edit spec of `kind`, or null. */
export function editKind(kind) {
  return Object.prototype.hasOwnProperty.call(EDIT_KINDS, kind) ? EDIT_KINDS[kind] : null;
}

export const BUMPS = ["patch", "minor", "major"];

/** X.Y.Z raised by one part ("patch", "minor" or "major"), or null for a bad version. */
export function bumped(version, part) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(String(version || ""));
  if (!match) return null;
  let [major, minor, patch] = match.slice(1).map(Number);
  if (part === "major") [major, minor, patch] = [major + 1, 0, 0];
  else if (part === "minor") [minor, patch] = [minor + 1, 0];
  else patch += 1;
  return `${major}.${minor}.${patch}`;
}

/**
 * The version a save writes: `base` raised by `part`, then by patches past any version the name
 * already has (listed in any root), so "Save as X.Y.Z" names a free version when it was offered.
 * The CLI still refuses one that appears meanwhile (VERSION_EXISTS, another tab).
 */
export function saveVersion(base, part, existing = []) {
  const taken = new Set(existing);
  let version = bumped(base, part);
  for (let i = 0; version && taken.has(version) && i < 1000; i += 1) version = bumped(version, "patch");
  return version;
}

/** Whether a `registry show` file is text the editor can open and save back byte for byte. */
export function editableFile(file) {
  if (!file || file.type !== "file") return false;
  if (/^manifest\.yaml$/i.test(String(file.path || ""))) return false;
  return !file.binary && !file.truncated && !file.omitted && !file.stripped && typeof file.text === "string";
}

/** The files of `kind` the editor can open, `prompt.md`-like files first. */
export function editableFiles(show, kind = "prompt") {
  const spec = editKind(kind);
  const files = ((show && Array.isArray(show.files) && show.files) || []).filter((file) => editableFile(file) && Boolean(spec) && spec.file(String(file.path), show));
  const rank = (file) => (/\.(md|txt)$/i.test(file.path) ? 0 : 1);
  return files.sort((a, b) => rank(a) - rank(b) || (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}

/** Why a file of the version is not offered, or null: shown so a missing file is not a mystery. */
export function notEditableReason(file, kind = "prompt", show = null) {
  if (!file || file.type !== "file") return null;
  if (/^manifest\.yaml$/i.test(String(file.path || ""))) return null;
  const spec = editKind(kind);
  if (editableFile(file)) return spec && !spec.file(String(file.path), show) ? `the console edits only ${spec.files}` : null;
  if (file.binary) return "binary";
  if (file.truncated || file.omitted) return "over the size the console shows";
  if (file.stripped) return "holds control or invisible characters the console cannot show exactly";
  return "not text";
}

/** The line numbers for the gutter: one per line of `text` (at least one). */
export function gutterLines(text) {
  const count = String(text ?? "").split("\n").length;
  return Array.from({ length: count }, (_, i) => i + 1).join("\n");
}

/** Cmd+S (macOS) or Ctrl+S elsewhere, without Alt or Shift. */
export function isSaveShortcut(event) {
  if (!event || event.altKey || event.shiftKey) return false;
  return Boolean(event.metaKey || event.ctrlKey) && String(event.key || "").toLowerCase() === "s";
}

/**
 * Versions of the same name the project's agent.yaml declares, other than `version`: the OLD of
 * "Move agent to this version" (`agent set-ref OLD NEW`). Newest first.
 */
export function movableFrom(entry, version) {
  if (!entry || !version) return [];
  return (entry.versions || [])
    .filter((v) => v.version !== version.version && (v.declared || []).includes("agent.yaml"))
    .sort((a, b) => compareVersions(b.version, a.version));
}

/** Whether the project's agent.yaml declares `version` itself. */
export function agentUses(version) {
  return Boolean(version && (version.declared || []).includes("agent.yaml"));
}

const WORDS = {
  VERSION_EXISTS: "That version exists already (saved from another tab or a terminal?). Pick another version and review again.",
  DIGEST_MISMATCH: "The version changed after your review. Review the changes again before saving.",
  BASE_MISMATCH: "agent.yaml changed after your review. Review the change again.",
  EDIT_UNCHANGED: "Nothing changed: the text is the same as this version's.",
  CODE_NOT_CONFIRMED: "Tick the box to confirm you reviewed this code, then save.",
  WRITE_BUSY: null, // the server's own message says what holds it
};

/** A CLI or server refusal as a sentence: fixed words for the codes a person can act on. */
export function editProblem(problem) {
  if (!problem) return null;
  const code = problem.code || null;
  const words = code && WORDS[code];
  return { code, text: words || String(problem.message || "The request failed.") };
}

/** Whether a problem means the review is no longer valid (review again before saving). */
export function needsNewReview(problem) {
  return Boolean(problem && ["VERSION_EXISTS", "DIGEST_MISMATCH", "BASE_MISMATCH"].includes(problem.code));
}
