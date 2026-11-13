// Jobs the console follows: the activity pill and dock read this store. No DOM: the shell
// subscribes and re-renders. Several jobs can run (one per project, cluster jobs one per
// machine), so every job has its own poll loop and its own append-only line list.
//
// - `/api/activity` is polled for running jobs (also ones started in another tab or before a
//   reload); each new one is followed.
// - A followed job's lines are fetched with `?after=<last seq>` and appended, never replaced, so
//   the log view only adds rows (#811 item 8: the log appended instead of rebuilding).
// - When a job ends, its entry collapses to a one-line result and its project's version is
//   bumped, so that project's screens read their data again.
// - Cancel is one request (the server sends one SIGINT); after it the control is never offered
//   again for that job. "Stop now" is offered only when the server says `can_stop`.

export const MAX_LINES = 5000;
const RUNNING_POLL = 1500;
const ACTIVITY_POLL = 3000;
const IDLE_ACTIVITY_POLL = 10000;
const RETRY = 5000;

export function createJobs({ client, setTimer = setTimeout, clearTimer = clearTimeout, now = Date.now }) {
  const entries = new Map(); // id -> entry
  const versions = new Map(); // project id -> number
  const listeners = new Set();
  let running = [];
  let activityTimer = null;
  let stopped = false;
  let changed = 0;

  const emit = () => {
    changed += 1;
    for (const listener of listeners) listener(changed);
  };

  function entryFor(id, seed = {}) {
    let entry = entries.get(id);
    if (!entry) {
      entry = { id, kind: seed.kind || "job", project: seed.project || null, state: "running", exit_code: null, lines: [], lastSeq: 0, dropped: 0, collapsed: false, cancelAsked: false, cancelling: false, canStop: false, timer: null, started: now(), error: null };
      entries.set(id, entry);
    }
    return entry;
  }

  // One loop per job: `inflight` covers the request, `timer` the wait; a halt bumps `generation`,
  // so an answer from before the halt never schedules another round.
  let generation = 0;

  function schedule(entry, ms) {
    const gen = generation;
    entry.timer = setTimer(() => poll(entry.id, gen), ms);
  }

  async function poll(id, gen = generation) {
    const entry = entries.get(id);
    if (!entry || stopped || gen !== generation || entry.inflight) return;
    entry.timer = null;
    entry.inflight = true;
    let job;
    try {
      job = await client.get(`/api/jobs/${id}?after=${entry.lastSeq}`);
    } catch (error) {
      if (gen !== generation) return; // a newer loop owns `inflight` now
      entry.inflight = false;
      if (!entries.has(id) || stopped || (error && error.kind === "session")) return;
      entry.error = `Could not read the job, retrying: ${error && error.message ? error.message : "no answer"}`;
      emit();
      schedule(entry, RETRY);
      return;
    }
    if (gen !== generation) return; // a newer loop owns `inflight` now
    entry.inflight = false;
    if (!entries.has(id) || stopped) return;
    entry.error = null;
    for (const line of job.lines || []) {
      if (line.seq <= entry.lastSeq) continue;
      entry.lines.push({ seq: line.seq, text: line.text });
      entry.lastSeq = line.seq;
    }
    if (entry.lines.length > MAX_LINES) {
      const extra = entry.lines.length - MAX_LINES;
      entry.lines.splice(0, extra);
      entry.dropped += extra;
    }
    const wasRunning = entry.state === "running";
    entry.kind = job.kind || entry.kind;
    entry.project = job.project || entry.project;
    entry.state = job.state;
    entry.exit_code = job.exit_code ?? null;
    entry.cancelling = Boolean(job.cancelling);
    entry.canStop = Boolean(job.can_stop);
    if (job.state === "running") {
      schedule(entry, RUNNING_POLL);
    } else if (wasRunning) {
      entry.collapsed = true;
      if (entry.project) versions.set(entry.project, (versions.get(entry.project) || 0) + 1);
    }
    emit();
  }

  let activityInflight = false;

  async function pollActivity(gen = generation) {
    activityTimer = null;
    if (stopped || gen !== generation || activityInflight) return;
    activityInflight = true;
    try {
      const activity = await client.get("/api/activity");
      if (gen !== generation) return;
      activityInflight = false;
      if (stopped) return;
      running = activity.running || [];
      for (const job of running) {
        if (!entries.has(job.id)) {
          entryFor(job.id, job);
          poll(job.id);
        }
      }
      emit();
    } catch (error) {
      if (gen !== generation) return;
      activityInflight = false;
      if (error && error.kind === "session") return;
    }
    if (!stopped && gen === generation) {
      activityTimer = setTimer(() => pollActivity(gen), running.length ? ACTIVITY_POLL : IDLE_ACTIVITY_POLL);
    }
  }

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    /** Start polling activity (once, at sign-in). */
    begin() {
      stopped = false;
      if (!activityTimer) pollActivity();
      // After a halt (the console was unreachable), running jobs resume their own loops.
      for (const entry of entries.values()) {
        if (entry.state === "running" && !entry.timer) poll(entry.id);
      }
    },
    /** Stop every poll loop (session ended, console unreachable). */
    halt() {
      stopped = true;
      generation += 1;
      activityInflight = false;
      if (activityTimer) clearTimer(activityTimer);
      activityTimer = null;
      for (const entry of entries.values()) {
        if (entry.timer) clearTimer(entry.timer);
        entry.timer = null;
        entry.inflight = false; // an answer to that request is dropped by its generation
      }
    },
    /** Start a job in a project; resolves to its id, or throws the ApiError (409 BUSY names the job holding the lock). */
    async start(project, kind, options = {}, keys) {
      const body = { kind, options };
      if (keys) body.keys = keys;
      let reply;
      try {
        reply = await client.post(`/api/projects/${project}/jobs`, body);
      } catch (error) {
        if (error && error.data && error.data.job) this.follow(error.data.job, { project });
        throw error;
      }
      this.follow(reply.job, { kind, project });
      return reply.job;
    },
    follow(id, seed = {}) {
      const entry = entryFor(id, seed);
      entry.collapsed = false;
      if (!entry.timer && entry.state === "running") poll(id);
      emit();
    },
    async cancel(id) {
      const entry = entries.get(id);
      if (!entry || entry.cancelAsked) return;
      entry.cancelAsked = true; // sticky once sent: a poll answered before it landed keeps it hidden
      emit();
      try {
        await client.post(`/api/jobs/${id}/cancel`, {});
      } catch (error) {
        if (error && error.kind === "session") return;
        // Not delivered: offer Cancel again, with the reason.
        entry.cancelAsked = false;
        entry.error = `Cancel was not sent: ${error && error.message ? error.message : "no answer"}`;
        emit();
      }
    },
    async stop(id) {
      try {
        await client.post(`/api/jobs/${id}/stop`, {});
      } catch {
        /* the next poll shows the state */
      }
    },
    toggle(id) {
      const entry = entries.get(id);
      if (entry) entry.collapsed = !entry.collapsed;
      emit();
    },
    dismiss(id) {
      const entry = entries.get(id);
      if (!entry || entry.state === "running") return;
      if (entry.timer) clearTimer(entry.timer);
      entries.delete(id);
      emit();
    },
    /** Followed jobs, running first, then newest. */
    list() {
      return [...entries.values()].sort((a, b) => {
        const runningFirst = (b.state === "running") - (a.state === "running");
        return runningFirst || b.started - a.started;
      });
    },
    running: () => [...entries.values()].filter((entry) => entry.state === "running"),
    runningIn: (project) => [...entries.values()].find((entry) => entry.state === "running" && entry.project === project) || null,
    version: (project) => versions.get(project) || 0,
  };
}
