// The framework an agent runs, everywhere but Build (#1085 S2, console spec §6): pure functions over
// `GET /api/runtimes` (the engine's resolved view of every `runtime/` version, the same list as
// Build's `parts.runtimes`) and a deployment's `runtime` ref (`list --all --json`, F2a). Nothing
// here touches the DOM or the network, and no framework's name is written in this file: names come
// from the runtime manifest (C8), and a ref the registries do not hold is shown as the ref itself.

// A `runtime/` ref as `list --json` prints it (`registry.REF`: the version is digits and dots).
const REF = /^runtime\/([a-z0-9][a-z0-9-]{0,62})@([0-9.]{1,40})$/;

/** The Agents filter's value for a deployment with no recorded runtime. Not a runtime name (they
 * have no underscore), so the two never collide in the URL. */
export const NOT_RECORDED = "_none";
export const NOT_RECORDED_TEXT = "Not recorded";
// A recorded runtime that is not a `runtime/` ref of today's shape is shown as recorded, capped.
const MAX_SHOWN = 100;
const recorded = (value) => (typeof value === "string" && value.trim() ? value.slice(0, MAX_SHOWN) : null);

/** `{ref, name, version}` of a `runtime/` ref, or null for anything else. */
export function parseRuntime(ref) {
  const match = typeof ref === "string" ? REF.exec(ref) : null;
  return match ? { ref, name: match[1], version: match[2] } : null;
}

/** The usable runtime version with this ref in `runtimes`, or null. */
export function runtimeEntry(runtimes, ref) {
  if (!Array.isArray(runtimes)) return null;
  return runtimes.find((entry) => entry && entry.usable && entry.ref === ref) || null;
}

/**
 * What the badge shows for a ref: `{text, ref, known}`. `text` is the framework's own name from
 * the manifest (C8); a ref the registries do not hold shows the ref itself (`known` false), and no
 * ref shows "Not recorded" (`ref` null).
 */
export function frameworkFor(ref, runtimes) {
  const parsed = parseRuntime(ref);
  if (!parsed) {
    const raw = recorded(ref);
    return raw ? { text: raw, ref: null, known: false } : { text: NOT_RECORDED_TEXT, ref: null, known: false };
  }
  const entry = runtimeEntry(runtimes, parsed.ref);
  if (entry && typeof entry.display_name === "string" && entry.display_name) return { text: entry.display_name, ref: parsed.ref, known: true };
  // Another version of the same runtime names the framework, when this one is not listed.
  const sibling = Array.isArray(runtimes) ? runtimes.find((other) => other && other.usable && other.name === parsed.name && other.display_name) : null;
  if (sibling) return { text: sibling.display_name, ref: parsed.ref, known: true };
  return { text: parsed.ref, ref: parsed.ref, known: false };
}

/** A row's value for the framework filter: its runtime's name (`adk`), or NOT_RECORDED. */
export function filterKey(row) {
  const parsed = parseRuntime(row && row.runtime);
  if (parsed) return parsed.name;
  return recorded(row && row.runtime) ? OTHER : NOT_RECORDED;
}

/** The filter's value for a runtime recorded in a shape the console does not read. */
export const OTHER = "_other";
export const OTHER_TEXT = "Other runtime";

/**
 * The filter's options for these rows: "All frameworks", each framework present (by its own name
 * when the registries hold it, else the runtime name), then "Not recorded" when a row has none.
 * `[{value, label}]`; the value is kept in the URL.
 */
export function filterOptions(rows, runtimes) {
  const names = new Map();
  let unrecorded = false;
  let other = false;
  for (const row of rows || []) {
    const parsed = parseRuntime(row && row.runtime);
    if (!parsed) {
      if (filterKey(row) === OTHER) other = true;
      else unrecorded = true;
      continue;
    }
    if (!names.has(parsed.name)) {
      const shown = frameworkFor(parsed.ref, runtimes);
      names.set(parsed.name, shown.known ? shown.text : parsed.name);
    }
  }
  const options = [...names.entries()]
    .map(([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label) || a.value.localeCompare(b.value));
  return [
    { value: "", label: "All frameworks" },
    ...options,
    ...(other ? [{ value: OTHER, label: OTHER_TEXT }] : []),
    ...(unrecorded ? [{ value: NOT_RECORDED, label: NOT_RECORDED_TEXT }] : []),
  ];
}

/** The rows the filter keeps: all of them for "", else those whose key matches. */
export function filterRows(rows, value) {
  if (!Array.isArray(rows)) return [];
  if (!value) return rows;
  return rows.filter((row) => filterKey(row) === value);
}

/** The name the filter's value stands for, for its empty state: "No agents on LangGraph." */
export function filterLabel(value, rows, runtimes) {
  if (value === NOT_RECORDED) return NOT_RECORDED_TEXT;
  if (value === OTHER) return OTHER_TEXT;
  const option = filterOptions(rows, runtimes).find((item) => item.value === value);
  if (option) return option.label;
  // A framework no row runs (an address kept from earlier): its name, when the registries hold it.
  const entry = Array.isArray(runtimes) ? runtimes.find((item) => item && item.usable && item.name === value && item.display_name) : null;
  return entry ? entry.display_name : value;
}

/** The empty state of a filter that matches nothing. */
export function filterEmptyText(value, rows, runtimes) {
  if (value === NOT_RECORDED) return "No agents without a recorded runtime.";
  if (value === OTHER) return "No agents on another runtime.";
  return `No agents on ${filterLabel(value, rows, runtimes)}.`;
}

/** The list's status line: "3 of 9 agents" when filtered, "9 agents" when not. */
export function countText(shown, total, filtered) {
  const noun = total === 1 ? "agent" : "agents";
  return filtered ? `${shown} of ${total} ${noun}` : `${total} ${noun}`;
}

/**
 * The Overview's Protocol row: "1 (v1 profile, before the kit)" for a runtime on the `v1` profile,
 * "1 (kit profile, kit 1.0.0)" for one on the kit. Words come from the manifest's fields only.
 */
export function protocolText(entry) {
  if (!entry || !Number.isInteger(entry.protocol)) return null;
  if (entry.profile === "v1") return `${entry.protocol} (v1 profile, before the kit)`;
  const kit = typeof entry.kit === "string" && entry.kit ? `, kit ${entry.kit}` : "";
  return `${entry.protocol} (${entry.profile} profile${kit})`;
}

/** The Overview's Capabilities row: "knowledge, mcp, notes", or "none". */
export function capabilitiesText(entry) {
  const list = entry && Array.isArray(entry.capabilities) ? entry.capabilities.filter((item) => typeof item === "string") : [];
  return list.length ? list.join(", ") : "none";
}
