// Icons (design v2 §7, #1116): Lucide paths (lucide-static 0.460.0, ISC; the licence is
// static/LICENSE-lucide.txt), placed as SVG elements through htm, never as markup strings.
// 1.5 px stroke, currentColor, decorative (aria-hidden): a word beside each icon carries the meaning.

import { html } from "./preact.js";

const PATHS = {
  monitor: html`<rect width="20" height="14" x="2" y="3" rx="2" /><line x1="8" x2="16" y1="21" y2="21" /><line x1="12" x2="12" y1="17" y2="21" />`,
  sun: html`<circle cx="12" cy="12" r="4" /><path d="M12 2v2" /><path d="M12 20v2" /><path d="m4.93 4.93 1.41 1.41" /><path d="m17.66 17.66 1.41 1.41" /><path d="M2 12h2" /><path d="M20 12h2" /><path d="m6.34 17.66-1.41 1.41" /><path d="m19.07 4.93-1.41 1.41" />`,
  moon: html`<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />`,
};

export function Icon({ name, size = 16 }) {
  return html`<svg class="icon" width=${size} height=${size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${PATHS[name]}</svg>`;
}

/**
 * The gate line (design v2 §3): a 1.5 px --brand ring around a check (Lucide circle-check), for
 * "gate passed" and "healthy" only. Decorative: the word beside it says the state.
 */
export function GateCheck() {
  return html`<svg class="gate-check" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle class="gate-ring" cx="12" cy="12" r="10" /><path class="gate-tick" d="m9 12 2 2 4-4" /></svg>`;
}
