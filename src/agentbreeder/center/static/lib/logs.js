// The log viewer's pure parts (#821, M5): the read's address, the level and text filters, search,
// the virtual window and the notes under the lines. Nothing here touches the DOM, so each is tested
// without a browser (tests/js/logs.test.mjs).

import { segments, visible } from "./text.js";

// `loglevels.LEVELS`, lowest first. A line with no level (an unclassified format) is kept only
// when no level is chosen, as `--level` does.
export const LEVELS = ["debug", "info", "warning", "error"];
export const LEVEL_CHOICES = ["warning", "error"];
export const TAILS = [200, 1000, 5000, 10000];
// `runtime.SINCE` (tests keep them equal).
export const SINCE = /^(?:[1-9][0-9]{0,5}[smh]|[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)$/;
const SERVICE = /^[a-z][a-z0-9-]{0,30}$/;
const DEPLOYMENT = /^[0-9a-f]{32}$/;

/** The API path for one read, or null when the source is not a valid one. */
export function logsPath(projectId, source, { tail = 200, since = "", previous = false } = {}) {
  if (!source) return null;
  const ok = source.kind === "service" ? SERVICE.test(source.name) : source.kind === "deployment" && DEPLOYMENT.test(source.name);
  if (!ok) return null;
  const query = new URLSearchParams({ tail: String(tail) });
  if (since) query.set("since", since);
  if (previous && source.kind === "deployment") query.set("previous", "1");
  return `/api/projects/${projectId}/logs/${source.kind}/${source.name}?${query}`;
}

/** Whether a line at `level` is kept under the chosen threshold (null keeps every line). */
export function levelShown(level, threshold) {
  if (!threshold) return true;
  return LEVELS.includes(level) && LEVELS.indexOf(level) >= LEVELS.indexOf(threshold);
}

/**
 * The rows to show: each kept line as {index, line, text, level}: `line` as read, `text` its visible
 * form (the escaped text that is searched and copied). `filter` keeps lines containing it, case-insensitively.
 */
export function shownRows(lines, levels, { level = null, filter = "" } = {}) {
  const needle = filter.toLowerCase();
  const rows = [];
  (Array.isArray(lines) ? lines : []).forEach((line, index) => {
    const lineLevel = Array.isArray(levels) ? levels[index] ?? null : null;
    if (!levelShown(lineLevel, level)) return;
    const text = visible(String(line));
    if (needle && !text.toLowerCase().includes(needle)) return;
    rows.push({ index, line: String(line), text, level: lineLevel });
  });
  return rows;
}

/** Where `query` occurs in `text`, case-insensitively, as [start, end) pairs that never overlap. */
export function matchRanges(text, query) {
  if (!query) return [];
  const haystack = text.toLowerCase();
  const needle = query.toLowerCase();
  const ranges = [];
  for (let at = haystack.indexOf(needle); at !== -1; at = haystack.indexOf(needle, at + needle.length)) {
    ranges.push([at, at + needle.length]);
  }
  return ranges;
}

/**
 * A line as pieces to render: {text, escaped, hit}. `escaped` marks a visible escape (styled apart,
 * as <Text> does); `hit` marks a search match. Ranges are over the visible form, which is the runs
 * of `segments(line)` joined.
 */
export function linePieces(line, query) {
  const runs = segments(line);
  const ranges = matchRanges(runs.map((run) => run.text).join(""), query);
  if (!ranges.length) return runs.map((run) => ({ ...run, hit: false }));
  const pieces = [];
  let offset = 0;
  for (const run of runs) {
    const end = offset + run.text.length;
    let cut = offset;
    for (const [start, stop] of ranges) {
      if (stop <= cut || start >= end) continue;
      if (start > cut) pieces.push({ text: run.text.slice(cut - offset, start - offset), escaped: run.escaped, hit: false });
      const to = Math.min(stop, end);
      pieces.push({ text: run.text.slice(Math.max(start, cut) - offset, to - offset), escaped: run.escaped, hit: true });
      cut = to;
    }
    if (cut < end) pieces.push({ text: run.text.slice(cut - offset), escaped: run.escaped, hit: false });
    offset = end;
  }
  return pieces;
}

/** The positions (in `rows`) of the rows that contain `query`. */
export function searchHits(rows, query) {
  if (!query) return [];
  const needle = query.toLowerCase();
  const hits = [];
  rows.forEach((row, position) => {
    if (row.text.toLowerCase().includes(needle)) hits.push(position);
  });
  return hits;
}

/** The search status: "12 matches", "3 of 12" once one is chosen, or "No matches". */
export function searchStatus(hits, current, query) {
  if (!query) return "";
  if (!hits.length) return "No matches";
  if (current < 0) return `${hits.length} ${hits.length === 1 ? "match" : "matches"}`;
  return `${current + 1} of ${hits.length}`;
}

/** The next hit after (or, backwards, before) `current`, wrapping around; -1 with no hits. */
export function stepHit(hits, current, backwards = false) {
  if (!hits.length) return -1;
  if (current < 0) return backwards ? hits.length - 1 : 0;
  return backwards ? (current - 1 + hits.length) % hits.length : (current + 1) % hits.length;
}

/**
 * The slice of rows to render for a fixed row height: [first, last) with `overscan` rows on each
 * side, so scrolling a few rows never shows a gap.
 */
export function windowOf(scrollTop, viewport, rowHeight, count, overscan = 20) {
  if (!count || rowHeight <= 0) return [0, 0];
  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const last = Math.min(count, Math.ceil((scrollTop + viewport) / rowHeight) + overscan);
  return [first, Math.max(first, last)];
}

/** The notes under the lines: what was read, redaction, truncation and a pod with no output. */
export function logNotes(data, { tail }) {
  const notes = [];
  if (!data) return notes;
  const count = Array.isArray(data.lines) ? data.lines.length : 0;
  notes.push(`${count} ${count === 1 ? "line" : "lines"} read (at most the last ${tail}).`);
  notes.push("Secret values this stack knows are shown as [redacted]. Logs can still hold customer messages and model output.");
  if (data.truncated) notes.push("The read hit its 4 MiB limit: only the newest lines are shown. Ask for fewer lines or a later Since.");
  if (data.previous) notes.push("From the previous container (before its last restart).");
  if (!data.format) notes.push("This log's format is not known, so its lines have no level.");
  return notes;
}

function cap(text, limit = 200) {
  const value = String(text ?? "");
  return value.length > limit ? `${value.slice(0, limit)}…` : value;
}

/** The empty state for a read: a coded error that means "no lines here", or null. */
export function emptyReason(error, data) {
  if (error && error.code === "LOG_UNAVAILABLE") {
    return { code: "LOG_UNAVAILABLE", text: "Kubernetes no longer has this container's log (its pod was replaced or the log rotated). Try Previous container, or read a newer pod." };
  }
  // Pod reasons and messages come from the cluster: capped here, and shown through <Text> (escaped).
  if (error && error.code === "POD_NOT_RUNNING") return { code: "POD_NOT_RUNNING", text: cap(error.message) };
  if (data && data.pod_reason && Array.isArray(data.lines) && !data.lines.length) {
    return { code: "POD_PENDING", text: `The pod has no output yet (${cap(data.pod_reason)}).` };
  }
  if (data && Array.isArray(data.lines) && !data.lines.length) return { code: "EMPTY", text: "No log lines in this range." };
  return null;
}

// -- follow (#821, M5 PR 2) ------------------------------------------------------------------------

export const FOLLOW_EVERY = 1000; // ms between reads of a follow's new lines
export const FOLLOW_KEEP = 10000; // lines this page keeps while following, as the largest read
const FOLLOW_ID = /^[0-9a-f]{32}$/;
const REDACTED = "Secret values this stack knows are shown as [redacted]. Logs can still hold customer messages and model output.";
const ENDED = {
  container: "Follow ended: the container stopped or was removed.",
  stdin: "Follow stopped.",
  signal: "Follow ended: the log command was stopped by a signal.",
  interrupt: "Follow ended: the log command was interrupted.",
};

/** The path that starts a follow of `source`, or null when the source is not a valid one. */
export function followPath(projectId, source) {
  const read = logsPath(projectId, source);
  return read ? read.replace(/\?.*$/, "/follow") : null;
}

/** The body that starts a follow: the read's line count and Since (never Previous). */
export function followBody({ tail = 200, since = "" } = {}) {
  return since ? { tail, since } : { tail };
}

/** The path that reads a follow's lines after `after`, or null for an ID the server never gives. */
export function followReadPath(id, after) {
  if (!FOLLOW_ID.test(String(id)) || !Number.isSafeInteger(after) || after < 0) return null;
  return `/api/follows/${id}?after=${after}`;
}

/** A follow before its first answer. `rows` are {seq, line, level}, oldest first. */
export function followInitial() {
  return { rows: [], after: 0, cut: 0, missed: 0, dropped: 0, cliDropped: false, running: true, reason: null, stopped: false, gone: false, error: null, pausedAt: null };
}

/**
 * The follow after one read's envelope: new rows appended (the oldest dropped past FOLLOW_KEEP and
 * counted in `cut`), lines the server's ring lost before this read in `missed`, and how it ended.
 */
export function followMerge(state, reply) {
  const data = reply && reply.data;
  if (!data) return { ...state, running: false, error: (reply && reply.error) || { code: "UNREADABLE", message: "Unreadable response" } };
  const fresh = (Array.isArray(data.lines) ? data.lines : []).filter((row) => row && Number.isSafeInteger(row.seq) && row.seq > state.after && typeof row.line === "string");
  let rows = fresh.length ? state.rows.concat(fresh.map((row) => ({ seq: row.seq, line: row.line, level: row.level ?? null }))) : state.rows;
  let cut = state.cut;
  if (rows.length > FOLLOW_KEEP) {
    cut += rows.length - FOLLOW_KEEP;
    rows = rows.slice(rows.length - FOLLOW_KEEP);
  }
  return {
    ...state,
    rows,
    cut,
    after: Number.isSafeInteger(data.next) && data.next > state.after ? data.next : state.after,
    missed: state.missed + (Number.isSafeInteger(data.missed) && data.missed > 0 ? data.missed : 0),
    dropped: Number.isSafeInteger(data.dropped) ? data.dropped : state.dropped,
    cliDropped: data.cli_dropped === true || state.cliDropped,
    running: data.running === true,
    reason: data.reason ?? null,
    stopped: data.stopped === true,
    gone: data.gone === true,
    error: reply.error || null,
  };
}

/** The rows shown: all of them, or while paused only those up to where the pause began. */
export function followShown(state) {
  if (state.pausedAt === null) return state.rows;
  return state.rows.filter((row) => row.seq <= state.pausedAt);
}

/** Lines that arrived after the pause began (shown by Jump to latest). */
export function followHeld(state) {
  if (state.pausedAt === null) return 0;
  return state.rows.filter((row) => row.seq > state.pausedAt).length;
}

/** One line for the follow's live region: following, paused with N new lines, or how it ended. */
export function followStatus(state, { asked = false } = {}) {
  if (state.error) return `Follow failed: ${cap(state.error.message || "the log command failed")}${state.error.code ? ` (${state.error.code})` : ""}`;
  if (state.running) {
    if (state.pausedAt === null) return "Following: new lines appear as they are written.";
    return "Paused: new lines wait until Jump to latest.";
  }
  if (state.gone) return "The follow is gone (the console restarted, or it ended over a minute ago). Follow again to resume.";
  if (state.stopped && !asked) return "Follow stopped: this page did not read it for 15 seconds (a background tab). Follow again to resume.";
  return ENDED[state.reason] || "Follow ended.";
}

/** The notes under a followed log. */
export function followNotes(state) {
  const notes = [`${state.rows.length} ${state.rows.length === 1 ? "line" : "lines"} kept on this page (at most the newest ${FOLLOW_KEEP}).`, REDACTED];
  if (state.cut) notes.push(`The oldest ${state.cut} ${state.cut === 1 ? "line was" : "lines were"} dropped from this page to keep it fast.`);
  if (state.missed) notes.push(`${state.missed} ${state.missed === 1 ? "line" : "lines"} arrived faster than this page read them and ${state.missed === 1 ? "was" : "were"} skipped. Read again for the full log.`);
  if (state.dropped) notes.push(`${state.dropped} ${state.dropped === 1 ? "record" : "records"} did not match the log format and ${state.dropped === 1 ? "was" : "were"} not shown.`);
  if (state.cliDropped) notes.push("Lines over 1 MiB were dropped whole.");
  return notes;
}
