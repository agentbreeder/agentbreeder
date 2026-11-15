// Service usage (#822, M6 console half, part 4): the words, numbers and sparkline shapes for the
// Home services table and a service's Usage tab, from `GET /api/projects/ID/usage` (the server's
// five-minute ring of `services usage --follow --json` samples) and `services status --json`.
// Pure, so `node --test` covers it. Nothing here touches the DOM.

import { visible } from "./text.js";

/** How often a visible page asks for the ring (the CLI prints at most one sample every 2 s). */
export const USAGE_EVERY = 3000;
/** The ring's length in seconds when the answer does not say. */
export const WINDOW = 300;
export const SPARK_WIDTH = 96;
export const SPARK_HEIGHT = 24;

const UNITS = ["B", "KiB", "MiB", "GiB", "TiB"];

/** Bytes in IEC units ("512 MiB", "1.5 GiB"); null or a bad value is "no value". */
export function bytesText(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "no value";
  let n = value;
  let unit = 0;
  while (n >= 1024 && unit < UNITS.length - 1) {
    n /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || n >= 100 ? 0 : 1;
  return `${n.toFixed(digits)} ${UNITS[unit]}`;
}

/** Bytes per second. */
export function rateText(value) {
  const text = bytesText(value);
  return text === "no value" ? text : `${text}/s`;
}

/** A percentage with one decimal ("2.3%"). */
export function percentText(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "no value";
  return `${value.toFixed(1)}%`;
}

/** A sample's time (ISO 8601 UTC) in milliseconds; NaN when it does not parse. */
function at(sample) {
  return Date.parse(sample && sample.time);
}

/**
 * One container's rows across the ring, oldest first: [{t, row}], `row` null for a sample in
 * which the container did not appear (stopped, or not yet listed). `source` is "service" or
 * "agent"; a service is matched by name, an agent container by name and role (a sidecar has one).
 */
export function seriesFor(samples, source, name, role) {
  const list = Array.isArray(samples) ? samples : [];
  const out = [];
  for (const sample of list) {
    const t = at(sample);
    if (Number.isNaN(t)) continue;
    const rows = Array.isArray(sample.containers) ? sample.containers : [];
    const row = rows.find((r) => r && r.source === source && r.name === name && (source !== "agent" || (r.role || null) === (role || null))) || null;
    out.push({ t, row });
  }
  return out;
}

/** The newest row of a series, or null. Only the newest sample counts: an older row is stale. */
export function latestRow(series) {
  return series.length ? series[series.length - 1].row : null;
}

/** [{t, value}] of one field (null where the container or Docker had no value). */
export function valueSeries(series, key) {
  return series.map(({ t, row }) => ({ t, value: row && typeof row[key] === "number" ? row[key] : null }));
}

/**
 * [{t, value}] of a counter's rate in bytes per second between consecutive samples. Null where
 * either side has no value, and where the counter went down (the container restarted).
 */
export function rateSeries(series, key) {
  const out = [];
  for (let i = 0; i < series.length; i += 1) {
    const { t, row } = series[i];
    const before = i > 0 ? series[i - 1] : null;
    const now = row && typeof row[key] === "number" ? row[key] : null;
    const then = before && before.row && typeof before.row[key] === "number" ? before.row[key] : null;
    const seconds = before ? (t - before.t) / 1000 : 0;
    out.push({ t, value: now !== null && then !== null && seconds > 0 && now >= then ? (now - then) / seconds : null });
  }
  return out;
}

/** The sum of two series point by point (null when either is null). */
export function sumSeries(a, b) {
  return a.map((point, i) => ({ t: point.t, value: point.value !== null && b[i] && b[i].value !== null ? point.value + b[i].value : null }));
}

/** The newest value of a series, or null. */
export function lastValue(points) {
  return points.length ? points[points.length - 1].value : null;
}

/**
 * Polyline point lists for a sparkline: x by time over the window ending at the newest sample,
 * y scaled to the highest value (at least `floor`, so an idle container draws flat at the bottom
 * instead of filling the height with noise). A gap (null) splits the line.
 */
export function sparkLines(points, { window = WINDOW, width = SPARK_WIDTH, height = SPARK_HEIGHT, floor = 0 } = {}) {
  if (!points.length) return [];
  const end = points[points.length - 1].t;
  const start = end - window * 1000;
  const values = points.map((p) => p.value).filter((v) => v !== null);
  const top = Math.max(floor, ...values, Number.MIN_VALUE);
  const lines = [];
  let current = [];
  for (const { t, value } of points) {
    if (value === null || t < start) {
      if (current.length) lines.push(current);
      current = [];
      continue;
    }
    const x = ((t - start) / (end - start || 1)) * width;
    const y = height - (value / top) * (height - 2) - 1;
    current.push(`${round(x)},${round(y)}`);
  }
  if (current.length) lines.push(current);
  // A single point draws as a short flat dash, so it is visible.
  return lines.map((line) => (line.length === 1 ? [line[0], `${round(Number(line[0].split(",")[0]) + 1.5)},${line[0].split(",")[1]}`] : line).join(" "));
}

function round(n) {
  return Math.round(n * 10) / 10;
}

/**
 * A sparkline's words for screen readers: what it shows, over how long, its low, high and newest
 * value (colour and shape are never the only cue).
 */
export function sparkLabel(what, points, format, window = WINDOW) {
  const values = points.map((p) => p.value).filter((v) => v !== null);
  const minutes = Math.round(window / 60);
  const span = `last ${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  if (!values.length) return `${what}, ${span}: no readings yet`;
  const now = lastValue(points);
  return `${what}, ${span}: low ${format(Math.min(...values))}, high ${format(Math.max(...values))}, now ${now === null ? "no value" : format(now)}`;
}

/** Memory in words: "120 MiB of 7.7 GiB". */
export function memoryText(row) {
  if (!row || typeof row.memory_used !== "number") return "no value";
  return typeof row.memory_limit === "number" && row.memory_limit > 0 ? `${bytesText(row.memory_used)} of ${bytesText(row.memory_limit)}` : bytesText(row.memory_used);
}

/** Two rates in words: "1.2 KiB/s in, 300 B/s out". */
export function pairText(first, second, words) {
  return `${rateText(first)} ${words[0]}, ${rateText(second)} ${words[1]}`;
}

/** Two counters in words: "12 MiB in, 3.0 MiB out since it started". */
export function totalsText(row, keys, words) {
  if (!row) return "";
  return `${bytesText(row[keys[0]])} ${words[0]}, ${bytesText(row[keys[1]])} ${words[1]} since it started`;
}

/**
 * A service's status chip: {tone, icon, word}. The word carries the meaning; the icon and the
 * colour (tone ok|warn|error|off) repeat it. From `services status --json` state and health.
 */
export function statusChip(info) {
  const state = info && info.state;
  const health = info && info.health;
  if (!state || state === "absent") return { tone: "off", icon: "○", word: "Not created" };
  if (state === "running") {
    if (health === "unhealthy") return { tone: "error", icon: "✕", word: "Unhealthy" };
    if (health === "starting") return { tone: "warn", icon: "◐", word: "Starting" };
    if (health === "healthy") return { tone: "ok", icon: "✓", word: "Healthy" };
    return { tone: "ok", icon: "✓", word: "Running" };
  }
  if (state === "restarting") return { tone: "warn", icon: "↻", word: "Restarting" };
  if (state === "paused") return { tone: "warn", icon: "‖", word: "Paused" };
  if (state === "removing") return { tone: "warn", icon: "◐", word: "Removing" };
  if (state === "dead") return { tone: "error", icon: "✕", word: "Dead" };
  if (state === "exited") return { tone: "off", icon: "■", word: "Stopped" };
  if (state === "created") return { tone: "off", icon: "○", word: "Created" };
  return { tone: "off", icon: "?", word: visible(state) };
}

/**
 * Why a running service has no usage figures, or null when it has them. `usage` is the route's
 * envelope ({data, error}) or null before the first answer.
 */
export function usageGap(usage, row) {
  if (row) return null;
  if (!usage || (!usage.data && !usage.error)) return "Reading usage…";
  if (usage.error) return "No reading: see the note above.";
  const samples = (usage.data && usage.data.samples) || [];
  return samples.length ? "Not in the latest reading." : "Waiting for the first reading…";
}

/** The note for a feed that failed or was refused, or null. */
export function usageProblem(usage) {
  if (!usage || !usage.error) return null;
  const message = usage.error.message || "The usage command failed.";
  return usage.data && usage.data.samples && usage.data.samples.length
    ? `${message} The figures are from the last reading; it is tried again in a few seconds.`
    : `${message} It is tried again in a few seconds.`;
}

/**
 * Up services with no row in the newest sample, and rows of services that status does not count
 * as up: a signature that changes when status and usage disagree, so the page re-reads status
 * once per disagreement (a container stopped or started outside the console). "" when they agree
 * or there is no sample yet.
 */
export function statusMismatch(services, samples, isUp) {
  const list = Array.isArray(samples) ? samples : [];
  if (!list.length || !services) return "";
  const rows = Array.isArray(list[list.length - 1].containers) ? list[list.length - 1].containers : [];
  const seen = new Set(rows.filter((r) => r && r.source === "service").map((r) => r.name));
  const odd = [];
  for (const [name, info] of Object.entries(services)) {
    if (isUp(info) !== seen.has(name)) odd.push(name);
  }
  for (const name of seen) if (!Object.prototype.hasOwnProperty.call(services, name)) odd.push(name);
  return odd.sort().join(",");
}

/** A disk reading's time in local time, with "(the last reading failed)" when stale. */
export function diskAsOf(disk, locale) {
  if (!disk || !disk.as_of) return "not read yet";
  const date = new Date(disk.as_of);
  const time = Number.isNaN(date.getTime()) ? String(disk.as_of) : date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return `as of ${time}${disk.stale ? " (the last reading failed)" : ""}`;
}

/** A service container's writable-layer size from the disk reading, or null. */
export function serviceDisk(disk, name) {
  const rows = (disk && Array.isArray(disk.containers) && disk.containers) || [];
  const row = rows.find((r) => r && r.source === "service" && r.name === name);
  return row ? row.size : null;
}
