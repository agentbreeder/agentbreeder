// FrameworkBadge (#1085 S2, console spec §6): the framework an agent runs, as one neutral text
// badge on `.badge` (never the `.chip` status pill, never a logo, C4), with the ref beside it as
// visible text where the ref matters. `useRuntimes` reads `GET /api/runtimes` (a CLI child that
// hashes every runtime version), shared by every badge on the page and kept for a minute.

import { frameworkFor } from "../lib/framework.js";
import { Text, useConsole } from "./components.js";
import { html, useEffect, useState } from "./preact.js";

const KEEP_MS = 60000;
// {client, at, promise}: one read shared by every caller of one client for KEEP_MS. A stale or
// failed answer is not kept, so the next screen asks again.
let kept = null;

/** Forget the shared read (a test, or a page that must read again). */
export function forgetRuntimes() {
  kept = null;
}

/**
 * The resolved runtimes view: `{loading, runtimes, error, stale}`. `runtimes` is null until the
 * read answers and when it fails; a badge then shows the ref itself (`frameworkFor`). `stale`
 * marks the last good answer, kept by the server after a failed read.
 */
export function useRuntimes() {
  const { client } = useConsole();
  const [state, setState] = useState({ loading: true, runtimes: null, error: null, stale: false });
  useEffect(() => {
    let live = true;
    if (!kept || kept.client !== client || Date.now() - kept.at > KEEP_MS) {
      const promise = client.request("/api/runtimes").then(
        (reply) => ({ runtimes: Array.isArray(reply.data) ? reply.data : null, error: reply.error || null, stale: Boolean(reply.stale) }),
        (error) => ({ runtimes: null, error: { code: error.code, message: error.message }, stale: false }),
      );
      kept = { client, at: Date.now(), promise };
      promise.then((result) => {
        if ((!result.runtimes || result.error) && kept && kept.promise === promise) kept = null;
      });
    }
    kept.promise.then((result) => live && setState({ loading: false, ...result }));
    return () => {
      live = false;
    };
  }, []);
  return state;
}

/**
 * The badge: the framework's own name. `showRef` adds the ref beside it as text (not hover only);
 * a ref the registries do not hold is the badge's text already, so it is not repeated.
 */
export function FrameworkBadge({ runtime, runtimes, showRef = false }) {
  const shown = frameworkFor(runtime, runtimes);
  const badge = html`<span class="badge framework"><${Text} value=${shown.text} /></span>`;
  if (!showRef || !shown.ref || !shown.known) return badge;
  return html`<span class="framework-line">${badge} <code class="muted small"><${Text} value=${shown.ref} /></code></span>`;
}
