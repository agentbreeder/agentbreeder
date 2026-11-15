// Import a registry version by path (#825, M9a), as pure functions: the request the form sends,
// why the Review button is off, whether a review still matches the form, the review's wording
// and the CLI's refusals in words. Nothing here touches the DOM. The server checks everything
// again (center/registryimport.py); the path is never read by the server, only by the CLI child.

import { KINDS } from "./registry.js";

const NAME = /^[a-z0-9][a-z0-9-]{0,62}$/;
const VERSION = /^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$/;

/**
 * The kinds whose versions are code that runs in agents (`registry.CODE_KINDS`), written as
 * object keys: the console's static guard refuses the eval kind's name as a quoted string.
 */
export const CODE_KINDS = Object.keys({ tool: 1, service: 1, eval: 1, mcp: 1, runtime: 1 });

/** The form's {path, name, version} as the request body: trimmed, empty fields left out. */
export function importBody(form) {
  const body = { path: String(form.path || "").trim() };
  const name = String(form.name || "").trim();
  const version = String(form.version || "").trim();
  if (name) body.name = name;
  if (version) body.version = version;
  return body;
}

/** Why Review is off, as a sentence, or false. */
export function formProblem(form) {
  const body = importBody(form);
  if (!body.path) return "Type the version directory's absolute path first.";
  if (!body.path.startsWith("/")) return "The path must be absolute: it starts with /.";
  if (body.name && !NAME.test(body.name)) return "A name is lowercase letters, digits and dashes, starting with a letter or digit.";
  if (body.version && !VERSION.test(body.version)) return "A version is X.Y.Z, for example 1.2.0.";
  return false;
}

/** Whether a review was made for this form: a changed field needs a new review. */
export function reviewMatches(review, form) {
  if (!review) return false;
  const now = importBody(form);
  const then = review.body;
  return now.path === then.path && now.name === then.name && now.version === then.version;
}

/** The staged files the review lists (directories are implied by the paths). */
export function reviewFiles(data) {
  return Array.isArray(data && data.files) ? data.files.filter((file) => file && file.type === "file") : [];
}

/** One sentence: what the import writes. */
export function reviewSummary(data) {
  const count = reviewFiles(data).length;
  const renamed = data.source_ref && data.source_ref !== data.ref ? ` (from ${data.source_ref})` : "";
  return `Imports ${count} ${count === 1 ? "file" : "files"} into this project's registry as ${data.ref}${renamed}.`;
}

/** Whether the version is code that runs in agents (the three-tier confirm). */
export function runsCode(data) {
  return Boolean(data && (data.runs_code === true || CODE_KINDS.includes(data.kind)));
}

/** The registry detail route for an imported ref's name, or null for a kind without a page. */
export function detailRoute(data) {
  const meta = KINDS.find((entry) => entry.kind === (data && data.kind));
  return meta ? { pattern: `/registry/${meta.path}/:name`, params: { name: data.name } } : null;
}

const WORDS = {
  DIGEST_MISMATCH: "The directory changed after your review. Review it again before importing.",
  VERSION_EXISTS: "This project's registry has that version already. Import it as another version (or under another name) and review again.",
  REF_CONFLICT: "Another registry root has this ref with different content, so the import would not resolve. Import it as another version or name.",
  MANIFEST_INVALID: null, // the CLI's message names what is wrong with manifest.yaml
  ENTRY_UNSAFE: null, // the CLI's message names the link, file or size
  CODE_NOT_CONFIRMED: "Tick the box to confirm you reviewed this code, then import.",
  PATH_INVALID: null,
  REGISTRY_READ_ONLY: "This project's registry cannot be written: check its permissions.",
};

/** A CLI or server refusal as a sentence: fixed words for the codes a person can act on. */
export function importProblem(problem) {
  if (!problem) return null;
  const code = problem.code || null;
  const words = code && WORDS[code];
  return { code, text: words || String(problem.message || "The request failed.") };
}

/** Whether a refusal means the review is no longer valid. */
export function needsNewReview(problem) {
  return Boolean(problem && ["DIGEST_MISMATCH", "VERSION_EXISTS", "REF_CONFLICT"].includes(problem.code));
}

/** `text` as one shell word: quoted when it holds anything but plain path characters. */
function shellWord(text) {
  return /^[A-Za-z0-9_\/.@:+-]+$/.test(text) ? text : `'${text.replace(/'/g, "'\\''")}'`;
}

/** The terminal command for the same import (shown for reference; never run from here). */
export function terminalCommand(form) {
  const body = importBody(form);
  const parts = ["agentbreeder registry import"];
  if (body.name) parts.push(`--as ${body.name}`);
  if (body.version) parts.push(`--version ${body.version}`);
  parts.push("--dry-run", body.path ? shellWord(body.path) : "PATH");
  return parts.join(" ");
}
