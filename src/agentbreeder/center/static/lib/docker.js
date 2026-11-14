// Docker reachability for the page blocker (M6 #822, console §8.5): pure, node-tested. The shell
// asks `GET /api/docker` every DOCKER_EVERY ms while the tab is visible; the server answers from
// `doctor --json`'s Docker check, one check shared by every tab.

export const DOCKER_EVERY = 5000; // the acceptance: the blocker within 15 s of Docker stopping

export const DOCKER_UNKNOWN = Object.freeze({ reachable: null, message: "", problem: null });

/**
 * The next state from one `/api/docker` envelope. `reachable` is true, false or null (not known
 * yet, or doctor reported no Docker check: no blocker). A failed check (the doctor read itself
 * failed or timed out) keeps the last known answer and records the problem, so one slow read
 * neither raises nor clears the blocker.
 */
export function dockerFrom(reply, previous = DOCKER_UNKNOWN) {
  const data = reply && reply.data;
  if (!reply || reply.error || !data || typeof data !== "object") {
    const problem = (reply && reply.error && reply.error.message) || "The Docker check failed.";
    return { reachable: previous.reachable, message: previous.message, problem };
  }
  const reachable = data.reachable === true ? true : data.reachable === false ? false : null;
  return { reachable, message: typeof data.message === "string" ? data.message : "", problem: null };
}

/** True when Docker went from reachable or unknown to down, or came back: re-read what depends on it. */
export function dockerChanged(previous, next) {
  return (previous.reachable === false) !== (next.reachable === false);
}
