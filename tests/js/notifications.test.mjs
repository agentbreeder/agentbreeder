// Notifications (#827): the bell's number and words, and which events become failure toasts.
import assert from "node:assert/strict";
import { test } from "node:test";

import { bell, conditionsOf, eventTitle, newFailures, reportCommand } from "../../src/agentbreeder/center/static/lib/notifications.js";

const guide = (conditions, extra = {}) => ({ data: { conditions }, error: null, stale: false, ...extra });
const ev = (id, read = false, kind = "container_died") => ({ id, read, kind, message: "m", project_id: "p" });

test("the bell adds conditions and unread events, with the split in words", () => {
  const b = bell({ scoped: true, guide: guide([{ code: "SERVICES_UNHEALTHY", message: "x" }, { code: "STEP_BLOCKED", message: "y" }]), events: { events: [ev(1), ev(2, true)] } });
  assert.deepEqual(b, { count: 3, label: "Notifications: 2 need attention, 1 unread event" });
  assert.equal(bell({ scoped: true, guide: guide([{ code: "A_B_C", message: "x" }]), events: { events: [] } }).label, "Notifications: 1 needs attention, no unread events");
  assert.deepEqual(bell({ scoped: true, guide: guide([]), events: { events: [] } }), { count: 0, label: "Notifications: nothing needs attention, no unread events" });
});

test("under All projects the bell counts unread events only", () => {
  const b = bell({ scoped: false, guide: guide([{ code: "X_Y_Z", message: "x" }]), events: { events: [ev(1), ev(2)] } });
  assert.deepEqual(b, { count: 2, label: "Notifications: 2 unread events" });
});

test("an unknown or stale guide is never read as zero", () => {
  assert.match(bell({ scoped: true, guide: { data: null, error: { code: "X" } }, events: null }).label, /not known yet/);
  assert.match(bell({ scoped: true, guide: guide([{ code: "A_B_C", message: "x" }], { stale: true }), events: null }).label, /\(last known\)/);
  assert.match(bell({ scoped: false, guide: null, events: { events: null, error: { code: "EVENTS_INVALID" } } }).label, /events could not be read/);
  assert.match(bell({ scoped: false, guide: null, events: { events: null, error: null } }).label, /unread events are not known yet/);
  assert.match(bell({ scoped: false, guide: null, events: { events: [ev(1)], error: { code: "X" } } }).label, /1 unread event \(last known\)/);
});

test("conditions without a code and message are dropped", () => {
  assert.deepEqual(conditionsOf(guide([{ code: "A_B_C", message: "m" }, { code: 1 }, null])), [{ code: "A_B_C", message: "m" }]);
  assert.equal(conditionsOf(null), null);
});

test("toasts: only unread failures stored after the first read, each once", () => {
  let state = { baseline: null, toasted: new Set() };
  let result = newFailures({ events: [ev(5), ev(4)] }, state);
  assert.deepEqual(result.toast, []); // the first read sets the baseline: nothing already stored is toasted
  state = result.state;
  result = newFailures({ events: [ev(8, true), ev(7), ev(6), ev(5)] }, state);
  assert.deepEqual(result.toast.map((e) => e.id), [6, 7]); // oldest first; a read one is not toasted
  state = result.state;
  result = newFailures({ events: [ev(7), ev(6)] }, state);
  assert.deepEqual(result.toast, []); // a dismissed toast does not come back
  result = newFailures({ events: [ev(2)] }, result.state);
  assert.deepEqual(result.toast, []); // the store was deleted: the baseline resets, no toast
  assert.equal(result.state.baseline, 2);
  assert.deepEqual(newFailures({ events: null }, result.state).toast, []);
});

test("titles and the report command", () => {
  assert.equal(eventTitle(ev(1, false, "gate_failed")), "Eval gate failed");
  assert.equal(eventTitle(ev(1, false, "job_failed")), "Job failed");
  assert.equal(eventTitle(ev(1, false, "container_unhealthy")), "Container unhealthy");
  // A report in the project's deploy/ is read by the console; no terminal command (and no path
  // pasted into one).
  assert.equal(reportCommand({ gate: { report: "/p/deploy/s.eval-20261113-213200-1a2b3c.json", report_run: "20261113-213200-1a2b3c", report_agent: "s" } }), "");
  assert.match(reportCommand({ gate: { report: "/x/$(rm -rf ~)/y.json", report_run: null } }), /^agentbreeder evals show RUN --output DIR/);
  assert.doesNotMatch(reportCommand({ gate: { report: "/x/$(rm -rf ~)/y.json", report_run: null } }), /rm -rf/);
  assert.equal(reportCommand({ gate: { report: null } }), "");
});
