// Registry ▸ Runtimes and a runtime version's conformance (#1085 S3, console spec §6.3): pure
// functions over `GET /api/projects/ID/runtimes` (`registry runtimes --project DIR --json`) and
// `GET /api/projects/ID/conformance?ref=` (`registry conformance REF --json` and the project's
// built image). Nothing here touches the DOM or the network, and no framework's name is written
// here: names come from the runtime manifest.

/** A check's status in words; never colour alone. */
export const CHECK_WORDS = { pass: "passed", fail: "failed", not_run: "not run", not_applicable: "not applicable" };

/** A report's result in words. `incomplete` is not a pass: a check that applies did not run. */
export const RESULT_WORDS = { pass: "Passed", fail: "Failed", incomplete: "Incomplete: not a pass" };

/** "26 passed, 2 failed, 6 not run, 5 not applicable". */
export function countsText(counts) {
  if (!counts) return "";
  return ["pass", "fail", "not_run", "not_applicable"].map((key) => `${Number(counts[key]) || 0} ${CHECK_WORDS[key]}`).join(", ");
}

/**
 * The report's checks by group, in the report's order: `[{group, checks, failed, open}]`. A group
 * with a failed check is open (expanded), so failures are seen first; the others are closed.
 */
export function checkGroups(report) {
  const groups = [];
  for (const check of (report && Array.isArray(report.checks) && report.checks) || []) {
    let group = groups.find((item) => item.group === check.group);
    if (!group) groups.push((group = { group: check.group, checks: [], failed: 0 }));
    group.checks.push(check);
    if (check.status === "fail") group.failed += 1;
  }
  return groups.map((group) => ({ ...group, open: group.failed > 0 }));
}

/** A group's summary line: "Serving: 6 checks, 1 failed". */
export function groupText(group) {
  const failed = group.failed ? `, ${group.failed} failed` : "";
  return `${group.group}: ${group.checks.length} check${group.checks.length === 1 ? "" : "s"}${failed}`;
}

/** `sha256:` and 12 hex digits, for a table or a select. */
export function shortDigest(image) {
  const text = typeof image === "string" ? image : "";
  return text.startsWith("sha256:") ? `sha256:${text.slice(7, 19)}` : text.slice(0, 19);
}

/** The project's built images of this runtime version, for the image select. */
export function imagesFor(images, ref) {
  return (Array.isArray(images) ? images : []).filter((image) => image && image.runtime === ref);
}

/** An image option's label: "support, recorded 2026-11-18 10:00 UTC, sha256:abcdef012345" (when
 * the build record was last written, which a later build or release also changes). */
export function imageLabel(image) {
  const built = typeof image.built === "string" ? image.built.replace("T", " ").replace(/:\d\dZ$/, " UTC") : "time unknown";
  return `${image.agent}, recorded ${built}, ${shortDigest(image.image)}`;
}

/**
 * Whether the report is for another image than this project's newest build of the runtime
 * version: the report is then "not the current build". False when there is no build here.
 */
export function notCurrent(report, images) {
  if (!report || !Array.isArray(images) || !images.length) return false;
  const newest = [...images].sort((a, b) => String(b.built).localeCompare(String(a.built)))[0];
  return newest.image !== report.image;
}

/** Why Run conformance is off, or false. Docker and a running job are checked by the screen. */
export function runProblem(images, picked) {
  if (!images.length) return "No declared-mode image of this runtime version in this project. Build an agent from agent.yaml on it first.";
  if (!images.some((image) => image.image === picked)) return "Choose an image.";
  return false;
}

/** `{name, version}` of a version row's ref, for its detail link. */
export function versionOf(entry) {
  return { name: entry.name, version: entry.version };
}
