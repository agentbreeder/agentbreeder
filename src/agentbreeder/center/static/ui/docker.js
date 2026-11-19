// The Docker-down page blocker (M6 #822, console §8.5): shown on every signed-in screen while
// Docker is not reachable, with the `doctor` remedy and Retry; it clears by itself when Docker comes
// back, without a reload.

import { DOCKER_EVERY, DOCKER_UNKNOWN, dockerChanged, dockerFrom } from "../lib/docker.js";
import { Action, Text } from "./components.js";
import { html, useEffect, useRef, useState } from "./preact.js";

const IDLE = Object.freeze({ ...DOCKER_UNKNOWN, checking: false });

/**
 * Ask the server every DOCKER_EVERY ms while `active` (signed in) and the tab is visible; a tab
 * that comes back into view asks at once. `onChange` runs when Docker goes down or comes back
 * after the first answer (the overview read at sign-in already has that first state).
 * Returns `[state, retry]`; `state.checking` is true from a Retry until the answer that follows
 * it. Leaving the signed-in phase stops asking and forgets the answer, so a later sign-in never
 * shows a stale blocker.
 */
export function useDocker(client, active, onChange) {
  const [state, setState] = useState(IDLE);
  const retry = useRef(() => {});
  const changed = useRef(onChange);
  changed.current = onChange;
  useEffect(() => {
    if (!active) return undefined;
    let live = true;
    let timer = null;
    let inFlight = false;
    let again = false; // a Retry made while a check was in flight: ask once more after it
    let last = null; // no answer yet
    const schedule = () => {
      clearTimeout(timer);
      timer = setTimeout(run, DOCKER_EVERY);
    };
    async function run(manual = false) {
      if (manual) setState((previous) => ({ ...previous, checking: true }));
      if (inFlight) {
        again = again || manual;
        return;
      }
      if (!manual && document.hidden) return schedule();
      inFlight = true;
      clearTimeout(timer);
      let reply;
      try {
        // Retry asks for a new check; the timed ones may share one under 3 s old.
        reply = await client.request(manual ? "/api/docker?fresh=1" : "/api/docker");
      } catch (error) {
        // A session that ended or a console that stopped is handled by the app's phase,
        // which unmounts this (the cleanup resets the state).
        if (error.kind === "session" || error.kind === "unreachable") return;
        reply = { data: null, error: { message: error.message } };
      } finally {
        inFlight = false;
      }
      if (!live) return;
      const next = dockerFrom(reply, last || DOCKER_UNKNOWN);
      const flipped = last !== null && dockerChanged(last, next);
      last = next;
      if (flipped && changed.current) changed.current(next);
      if (again) {
        again = false;
        setState({ ...next, checking: true });
        run(true);
        return;
      }
      setState({ ...next, checking: false });
      schedule();
    }
    const onVisible = () => {
      if (!document.hidden) run();
    };
    retry.current = () => run(true);
    document.addEventListener("visibilitychange", onVisible);
    run();
    return () => {
      live = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      retry.current = () => {};
      setState(IDLE);
    };
  }, [active]);
  return [state, () => retry.current()];
}

/**
 * The blocker itself: nothing unless Docker is known to be down. Retry stays enabled while a
 * check runs (a disabled button would drop keyboard focus to the page); a click then asks once
 * more after the check in flight, and the status line says a check is running.
 */
export function DockerBlocker({ docker, onRetry }) {
  if (docker.reachable !== false) return null;
  return html`<div class="docker-blocker" role="region" aria-label="Docker status">
    <div class="notice error blocker" role="alert">
      <p><strong>Docker is not reachable.</strong> <${Text} value=${docker.message} /></p>
      <p>Start Docker, then run <code>agentbreeder doctor</code> in a terminal. Services, builds and releases need it. The console checks again every few seconds and clears this when Docker is back.</p>
      <div class="row">
        <${Action} small tone="secondary" onClick=${onRetry}>Retry<//>
        <span class="muted small" role="status">${docker.checking ? "Checking Docker…" : docker.problem ? html`The last check failed: <${Text} value=${docker.problem} />` : ""}</span>
      </div>
    </div>
  </div>`;
}
