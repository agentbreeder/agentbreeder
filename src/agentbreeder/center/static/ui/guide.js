// The guided path (#820): the Home checklist, the step's fix (button, command, note, link), the
// "Next" pill and a finished job's result row, all from `next --json` (lib/guide.js). Every text
// from the guide goes through <Text>; commands are shown to copy, never run.

import { actionFor, currentStep, linkHost, pillText, resultText, stateWord } from "../lib/guide.js";
import { Action, Card, Notice, Skeleton, Text, useConsole } from "./components.js";
import { html, useState } from "./preact.js";

const ICONS = { done: "✓", next: "→", blocked: "!", todo: "○" };

/** Runs a step's console move. Returns [notice element, run(step)]. */
function useStepAction(project) {
  const { jobs, navigate, addProject, setScope } = useConsole();
  const [notice, setNotice] = useState(null);
  const run = async (step) => {
    const action = actionFor(step);
    if (!action) return;
    setNotice(null);
    try {
      if (action.job) await jobs.start(project.id, action.job);
      else if (action.route) navigate(action.route);
      else if (action.starter) {
        navigate("/");
        requestAnimationFrame(() => document.getElementById("starter-name")?.focus());
      } else if (action.example) {
        const added = await addProject({ example: "northwind" });
        setScope(added.id, "/");
      }
    } catch (error) {
      if (error.kind !== "session") setNotice({ tone: "error", text: error.message });
    }
  };
  return [html`<${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />`, run];
}

/** A step's fix: its console button (if any), then the command, note and link as text. */
function StepFix({ step, project, run }) {
  const { busyReason } = useConsole();
  const fix = step.fix;
  if (!fix) return null;
  const action = actionFor(step);
  const host = fix.url ? linkHost(fix.url) : null;
  const busy = action && action.job ? busyReason(project.id) : false;
  return html`<div class="step-fix">
    ${action ? html`<${Action} small tone=${step.state === "done" ? "secondary" : "primary"} disabled=${busy} onClick=${() => run(step)}>${action.label}<//>` : null}
    ${fix.command
      ? html`<p class="muted small">${action ? "Or in a terminal:" : step.id === "try" ? "Send it a message from a terminal:" : "Run this in a terminal:"}</p>
          <${Text} value=${fix.command} block copy label=${`${step.title} command`} />`
      : null}
    ${fix.note ? html`<p class="muted small"><${Text} value=${fix.note} /></p>` : null}
    ${host ? html`<p class="small"><a href=${fix.url} target="_blank" rel="noopener noreferrer">Open the provider's key page</a> <span class="muted">(${host})</span></p>` : null}
    ${step.id === "try" && step.state === "done" ? html`<p class="small"><a href="#/deployments">See it in Deployments</a></p>` : null}
  </div>`;
}

/** Why the guide is missing or last known: its own words, not the job-centred ViewState text. */
function GuideState({ guide }) {
  if (guide.loading && !guide.data) return html`<${Skeleton} />`;
  if (!guide.error) return null;
  const why = guide.error.message || "The checklist could not be read.";
  if (guide.stale) return html`<p class="muted small">Showing the last answer; checking again failed: <${Text} value=${why} /></p>`;
  return html`<p class="muted"><${Text} value=${why} /></p>`;
}

/** The Home checklist for one project. */
export function Checklist({ project }) {
  const { guide, reloadGuide, busyReason } = useConsole();
  const [notice, run] = useStepAction(project);
  const steps = (guide.data && guide.data.steps) || [];
  const current = currentStep(guide.data);
  const busy = busyReason(project.id);
  return html`<${Card} title="Guided path" busy=${guide.loading}>
    ${notice}
    <p class="muted small guide-status" role="status">${busy && guide.data ? "A job is running in this project. This list is checked again when it ends." : ""}</p>
    <${GuideState} guide=${guide} />
    ${steps.length
      ? html`<ol class="checklist">
          ${steps.map((step) => {
            const here = step === current;
            const showFix = here || (step.id === "try" && step.state === "done");
            return html`<li key=${step.id} class=${`step ${step.state}`} aria-current=${here ? "step" : undefined}>
              <div class="step-head">
                <span class="step-state"><span aria-hidden="true">${ICONS[step.state] || "?"}</span> ${stateWord(step.state)}</span>
                <strong><${Text} value=${step.title} /></strong>
                ${step.required ? null : html`<span class="muted small">(optional)</span>`}
              </div>
              ${step.reason && step.state !== "done" ? html`<p class="muted small step-reason"><${Text} value=${step.reason.message} /></p>` : null}
              ${showFix ? html`<${StepFix} step=${step} project=${project} run=${run} />` : null}
            </li>`;
          })}
        </ol>`
      : null}
    <div class="row">
      <button type="button" class="action ghost small" disabled=${guide.loading} onClick=${reloadGuide}>Check again</button>
      <span class="muted small">Also checked when a job ends and when you come back to this window.</span>
    </div>
  <//>`;
}

/** The step the guide points at, as one primary button with its reason: an empty state's action. */
export function NextStep({ project }) {
  const { guide, busyReason } = useConsole();
  const [notice, run] = useStepAction(project);
  const step = currentStep(guide.data);
  if (!step) return null;
  const action = actionFor(step);
  const busy = action && action.job ? busyReason(project.id) : false;
  return html`<div class="next-step">
    ${notice}
    <p><strong>Next: <${Text} value=${step.title} /></strong>${step.reason ? html`. <${Text} value=${step.reason.message} />` : null}</p>
    ${action ? html`<${Action} disabled=${busy} onClick=${() => run(step)}>${action.label}<//>` : html`<a href="#/">See the guided path on Home</a>`}
  </div>`;
}

/** A finished job's result row: where the guided path stands now. */
export function JobResult() {
  const { guide } = useConsole();
  if (guide.loading) return html`<p class="muted small" role="status">Checking the next step…</p>`;
  const text = resultText(guide.data);
  if (!text) return null;
  return html`<p class="small result-row" role="status"><${Text} value=${text} /> <a href="#/">Guided path</a></p>`;
}

/** The top bar's "Next" pill for the project in scope; it opens the Home checklist. */
export function NextPill() {
  const { project, guide } = useConsole();
  const text = project ? pillText(guide.data) : null;
  if (!text) return null;
  const done = !currentStep(guide.data);
  return html`<a id="next-pill" class=${`pill next${done ? " done" : ""}`} href="#/" title=${guide.stale ? "Last known; checked again when the running job ends" : undefined}>
    <${Text} value=${text} />${guide.stale ? html`<span class="visually-hidden"> (last known)</span>` : null}
  </a>`;
}
