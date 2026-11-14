// The eval-set case editor (#826, M10 PR 2), as pure functions: a cases file (JSON lines, one case
// per line, as `evals.load_eval_set` reads it) to rows the editor shows, and back. Nothing here
// touches the DOM.
//
// Byte for byte: a line whose case was not changed is written back exactly as it was read, blank
// lines included, so opening and leaving the file unchanged is "No changes" and a review shows
// only the cases that were edited. A changed or new case is written the way Python's
// `json.dumps` writes it (", " and ": " separators, non-ASCII as \u escapes), as the starter's
// and Northwind's files are.

/** At most this many cases (`evals.MAX_CASES`). */
export const MAX_CASES = 1000;

/** The checks the editor shows, in `expect` (evals.run_case). Other `expect` keys are kept. */
export const CHECKS = [
  { key: "must_match", label: "Reply matches (one pattern per line)" },
  { key: "must_not_match", label: "Reply does not match (one pattern per line)" },
  { key: "tools_include", label: "Calls these tools (one per line)" },
  { key: "tools_exclude", label: "Does not call these tools (one per line)" },
  { key: "sources_any", label: "Cites one of these sources in its top 3 (one per line)" },
];

/** A JSON value as Python's `json.dumps(value)` writes it (ensure_ascii, default separators). */
export function pyJson(value) {
  if (value === null || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number") return JSON.stringify(value);
  if (typeof value === "string") {
    return JSON.stringify(value).replace(/[\u007f-\uffff]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
  }
  if (Array.isArray(value)) return `[${value.map(pyJson).join(", ")}]`;
  return `{${Object.entries(value).map(([k, v]) => `${pyJson(k)}: ${pyJson(v)}`).join(", ")}}`;
}

const plainObject = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);

/**
 * The file's lines as entries: {raw} for a blank line, {raw, data} for a case. Returns
 * {entries, error}: error is "LINE: why" for the first line that is not a JSON object (the editor
 * then offers the file as text only).
 */
export function parseCases(text) {
  const source = String(text ?? "");
  const ending = source.endsWith("\n") ? "\n" : "";
  const lines = (ending ? source.slice(0, -1) : source).split("\n");
  if (source === "") return { entries: [], ending: "\n", error: null };
  const entries = [];
  for (let i = 0; i < lines.length; i += 1) {
    const raw = lines[i];
    if (!raw.trim()) {
      entries.push({ raw });
      continue;
    }
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      return { entries: [], ending, error: `line ${i + 1} is not JSON` };
    }
    if (!plainObject(data)) return { entries: [], ending, error: `line ${i + 1} is not a JSON object` };
    entries.push({ raw, data });
  }
  return { entries, ending, error: null };
}

/** The file text: unchanged lines as read, changed ones (raw === null) as `pyJson`. */
export function serializeCases(entries, ending = "\n") {
  const lines = entries.map((entry) => (entry.raw !== null && entry.raw !== undefined ? entry.raw : pyJson(entry.data)));
  return lines.length ? lines.join("\n") + ending : "";
}

const list = (value) => (Array.isArray(value) ? value.filter((item) => typeof item === "string") : []);

/**
 * A case's turns as form items: a string turn is {text}; an object turn (Northwind's
 * `{"new_session": true, "message": …}`) is {text: its message, data: the object}, so its other
 * keys are kept; an object without a string message is {text: null, data} and is kept as it is.
 */
function turnForms(turns) {
  const items = (Array.isArray(turns) ? turns : []).map((turn) => {
    if (typeof turn === "string") return { text: turn };
    if (plainObject(turn)) return { text: typeof turn.message === "string" ? turn.message : null, data: turn };
    return { text: null, data: turn };
  });
  return items.length ? items : [{ text: "" }];
}

/** A form turn back as the case's turn, or null when it is an emptied text turn. */
function turnValue(item) {
  if (item.text === null) return item.data;
  const text = item.text.trim();
  if (!text) return null;
  return item.data !== undefined ? { ...item.data, message: text } : text;
}

/** Whether a turn says something: a non-empty string, or an object's non-empty message. */
function spoken(turn) {
  if (typeof turn === "string") return Boolean(turn.trim());
  return plainObject(turn) && typeof turn.message === "string" && Boolean(turn.message.trim());
}

const scalar = (value) => ["string", "number", "boolean"].includes(typeof value);

/** A case as the editor's form: strings throughout (lists one item per line). */
export function caseForm(data) {
  const expect = plainObject(data.expect) ? data.expect : {};
  const form = {
    id: scalar(data.id) ? String(data.id) : "",
    turns: turnForms(data.turns),
    reference: typeof data.reference === "string" ? data.reference : "",
  };
  for (const { key } of CHECKS) form[key] = list(expect[key]).join("\n");
  return form;
}

// A check list's items: one per line, as typed (a leading space can matter to a pattern); only
// blank lines and a trailing carriage return are dropped.
const lines = (text) => String(text || "").split("\n").map((line) => line.replace(/\r$/, "")).filter((line) => line.trim());

/**
 * The case written from a form, keeping the original's other keys (category, unknown `expect`
 * keys) and key order. A field the form did not change keeps the original value exactly (a
 * number id, a pattern's spaces, a non-string list item). An empty check list is left out:
 * `sources_any: []` would add a check that always fails.
 */
export function applyForm(data, form) {
  const before = caseForm(data || {});
  const next = { ...(data || {}) };
  if (form.id !== before.id || !("id" in next)) next.id = form.id.trim();
  if (JSON.stringify(form.turns) !== JSON.stringify(before.turns) || !("turns" in next)) {
    next.turns = form.turns.map(turnValue).filter((turn) => turn !== null);
  }
  const expect = plainObject(next.expect) ? { ...next.expect } : {};
  for (const { key } of CHECKS) {
    if (form[key] === before[key]) continue; // unchanged: as it was, present or not
    const items = lines(form[key]);
    if (items.length) expect[key] = items;
    else delete expect[key];
  }
  if (Object.keys(expect).length || plainObject(next.expect)) next.expect = expect;
  if (form.reference !== before.reference || !("reference" in next)) next.reference = form.reference.trim();
  // The original's key order (an edited Northwind case keeps id, category, turns, …); a key the
  // form adds goes last.
  const ordered = {};
  for (const key of Object.keys(data || {})) if (key in next) ordered[key] = next[key];
  for (const key of Object.keys(next)) if (!(key in ordered)) ordered[key] = next[key];
  return ordered;
}

/**
 * The cases file an eval version's manifest names (`cases:`, default `golden.jsonl`, as
 * `evals.load_eval_set` reads it), from its `registry show` files; null when the manifest is not
 * shown. The editor opens only that file: it is the one the review's load checks.
 */
export function casesFile(show) {
  const files = (show && Array.isArray(show.files) && show.files) || [];
  const manifest = files.find((file) => file && file.path === "manifest.yaml" && typeof file.text === "string");
  if (!manifest) return null;
  const match = /^cases:[ \t]*(?:"([^"\n]*)"|'([^'\n]*)'|([^\s#]+))/m.exec(manifest.text);
  const name = match ? match[1] ?? match[2] ?? match[3] : "";
  // `manifest.get("cases") or "golden.jsonl"`: null, ~ and an empty value are the default.
  return name && !["null", "~", "Null", "NULL"].includes(name) ? name : "golden.jsonl";
}

/** A new case's data: the next free id. */
export function newCase(entries) {
  const ids = new Set(entries.filter((entry) => entry.data).map((entry) => entry.data.id));
  let n = 1;
  while (ids.has(`case-${n}`)) n += 1;
  return { id: `case-${n}`, turns: [], expect: {}, reference: "" };
}

/**
 * Why the cases would not load, per case index (into the cases, not the lines) as {field, text}
 * items, plus a whole-set problem. The CLI checks again (EVAL_INVALID) when the review runs; these are the same rules
 * (`evals.load_eval_set`: id, turns and reference present, 1..MAX_CASES cases) plus what makes a
 * case useless (an empty id, a duplicate id, no turn, an empty reference).
 */
export function caseProblems(entries) {
  const cases = entries.filter((entry) => entry.data);
  const seen = new Map();
  const problems = cases.map(() => []);
  const add = (index, field, text) => problems[index].push({ field, text });
  cases.forEach((entry, index) => {
    const data = entry.data;
    const id = scalar(data.id) ? String(data.id).trim() : "";
    if (!id) add(index, "id", "needs an id");
    else if (seen.has(id)) add(index, "id", `has the same id as case ${seen.get(id) + 1}`);
    else seen.set(id, index);
    if (!(Array.isArray(data.turns) && data.turns.some(spoken))) add(index, "turns", "needs at least one turn");
    if (typeof data.reference !== "string" || !data.reference.trim()) add(index, "reference", "needs a reference answer");
  });
  let set = null;
  if (!cases.length) set = "The eval set needs at least one case.";
  else if (cases.length > MAX_CASES) set = `The eval set has more than ${MAX_CASES} cases.`;
  return { cases: problems, set, count: problems.filter((p) => p.length).length + (set ? 1 : 0) };
}

/**
 * The case an EVAL_INVALID message names ("golden.jsonl:3 needs id, turns, reference"), as an
 * index into the cases, or null. Lines count blank ones; cases do not.
 */
export function caseAtLine(entries, message, file) {
  const name = String(file || "").split("/").pop();
  const match = new RegExp(`${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}:(\\d+)\\b`).exec(String(message || ""));
  if (!match) return null;
  const line = Number(match[1]) - 1;
  if (!entries[line] || !entries[line].data) return null;
  return entries.slice(0, line).filter((entry) => entry.data).length;
}
