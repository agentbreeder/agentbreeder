// Usage pieces shared by Home and a service's Usage tab (#822, M6): the poll of a project's usage
// ring, a sparkline with its words, and a service's status chip.

import { SPARK_HEIGHT, SPARK_WIDTH, USAGE_EVERY, sparkLabel, sparkLines, statusChip } from "../lib/usage.js";
import { Text, useConsole } from "./components.js";
import { html, useEffect, useState } from "./preact.js";

const NONE = Object.freeze({ data: null, error: null });

/**
 * Ask for `/api/projects/ID/usage` every USAGE_EVERY ms while `active` and the tab is visible; a
 * tab that comes back into view asks at once. The route is a poll (it does not keep the session
 * alive), and the server stops the project's child about 15 s after the last ask, so a hidden or
 * closed page costs nothing. Returns the last envelope ({data, error}); a failed request keeps the
 * last data.
 */
export function useUsage(projectId, active) {
  return usePoll(projectId ? `/api/projects/${projectId}/usage` : null, active, USAGE_EVERY);
}

/**
 * Ask for a polled route (`poll=True` on the server: usage, watch) every `every` ms while `active`
 * and the tab is visible, as `useUsage` does. A new path starts from nothing, so one project's
 * answer never shows under another.
 */
export function usePoll(path, active, every) {
  const { client } = useConsole();
  const [state, setState] = useState(NONE);
  useEffect(() => {
    setState(NONE);
    if (!active || !path) return undefined;
    let live = true;
    let timer = null;
    let inFlight = false;
    const schedule = () => {
      clearTimeout(timer);
      timer = setTimeout(run, every);
    };
    async function run() {
      if (inFlight || !live) return;
      if (document.hidden) return schedule();
      inFlight = true;
      clearTimeout(timer);
      try {
        const reply = await client.request(path);
        if (live) setState({ data: reply.data, error: reply.error });
      } catch (error) {
        // A session that ended or a console that stopped is the app's phase to show; it unmounts
        // the signed-in screens, and the next sign-in mounts this again, so polling stops here.
        if (error.kind === "session" || error.kind === "unreachable") return;
        if (live) setState((previous) => ({ data: previous.data, error: { code: error.code, message: error.message } }));
      } finally {
        inFlight = false;
      }
      if (live) schedule();
    }
    const onVisible = () => {
      if (!document.hidden) run();
    };
    document.addEventListener("visibilitychange", onVisible);
    run();
    return () => {
      live = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [path, active, every]);
  return state;
}

/**
 * A sparkline: an SVG image whose aria-label carries the low, high and newest value, so the
 * shape is never the only cue. The text value is shown next to it by the caller.
 */
export function Spark({ what, points, format, window, floor = 0 }) {
  const lines = sparkLines(points, { window, floor });
  const label = sparkLabel(what, points, format, window);
  return html`<svg class="spark" role="img" aria-label=${label} viewBox=${`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`} width=${SPARK_WIDTH} height=${SPARK_HEIGHT} preserveAspectRatio="none" focusable="false">
    <line class="spark-base" x1="0" y1=${SPARK_HEIGHT - 0.5} x2=${SPARK_WIDTH} y2=${SPARK_HEIGHT - 0.5} />
    ${lines.map((pointsText, i) => html`<polyline key=${i} points=${pointsText} />`)}
  </svg>`;
}

/** A service's status: icon, word and colour (the word alone carries the meaning). */
export function StatusChip({ info }) {
  const chip = statusChip(info);
  return html`<span class=${`chip ${chip.tone}`}><span class="chip-icon" aria-hidden="true">${chip.icon}</span> <${Text} value=${chip.word} /></span>`;
}
