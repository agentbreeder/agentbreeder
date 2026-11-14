// The guided path in the console (#820): the Home checklist, the "Next" pill, empty-state buttons
// and finished-job result rows all come from `next --json` (docs/next.md), which the server has
// already checked against its models and the registered path. Pure functions: nothing here
// touches the DOM.
//
// The console never runs a command string from the guide. Each `fix.action` maps to a fixed
// console move below (a job kind the server builds its own argv for, or a screen); `command`,
// `note` and paths are display only, and `url` is only a link on a click.

/** Where each step's work happens in the console. */
export const STEP_ROUTES = {
  prerequisites: "/",
  project: "/",
  keys: "/services",
  services: "/services",
  build: "/release",
  release: "/release",
  try: "/deployments",
  kubernetes: "/targets/kubernetes",
};

// `fix.action` -> what its button does. `job`: start that console job kind (the server supplies
// the arguments). `route`: open the screen where the step is done (services-up needs the grant
// review there; a release costs model usage, which the Release screen states first). `starter`
// and `example`: the Home actions. `try`: the Try it screen of the project's agent (#823), named
// by the guide's own project step.
const ACTIONS = {
  "services-up": { label: "Review and start services", route: "/services" },
  "set-keys": { label: "Add the key", route: "/services" },
  build: { label: "Build", job: "build" },
  release: { label: "Go to release", route: "/release" },
  "cluster-up": { label: "Go to Kubernetes", route: "/targets/kubernetes" },
  promote: { label: "Go to Kubernetes", route: "/targets/kubernetes" },
  "new-agent": { label: "Create starter", starter: true },
  example: { label: "Onboard Northwind", example: true },
  invoke: { label: "Try it", try: true },
};

// LOCK_STALE (docs/next.md) has no `fix.action`: the guide assumes the console's build is
// `--locked`, which refuses a stale lock. The build job's `lock: "update"` option (#826) runs a
// plain `build`, which rewrites agent.lock to agent.yaml's refs, so the console offers that. The
// guide's note ("run it from the CLI") is then not shown.
const LOCK_UPDATE = { label: "Build and update agent.lock", job: "build", options: { lock: "update" }, hideNote: true };

/** The console move for a step's fix, or null (an unknown or null action: show the command). */
export function actionFor(step) {
  const action = step && step.fix && step.fix.action;
  if (action) return (Object.prototype.hasOwnProperty.call(ACTIONS, action) && ACTIONS[action]) || null;
  const stale = step && step.id === "build" && step.reason && step.reason.code === "LOCK_STALE";
  return stale && step.fix ? LOCK_UPDATE : null;
}

const STATE_WORDS = { done: "Done", next: "Next", blocked: "Blocked", todo: "To do" };

/** The Try it route for the guide's agent (its project step's `agent`), or null. */
export function tryRoute(guide) {
  const steps = (guide && Array.isArray(guide.steps) && guide.steps) || [];
  const project = steps.find((step) => step.id === "project");
  const agent = project && project.data && project.data.agent;
  return typeof agent === "string" && /^[a-z0-9]([a-z0-9-]{0,51}[a-z0-9])?$/.test(agent) && !agent.endsWith("-candidate") ? `/agents/${agent}/try` : null;
}

/** A step's state as a word (never colour alone). */
export function stateWord(state) {
  return STATE_WORDS[state] || "Unknown";
}

/** The step the guide points at: the `next` one, else the blocked one, else null. */
export function currentStep(guide) {
  const steps = (guide && Array.isArray(guide.steps) && guide.steps) || [];
  return steps.find((step) => step.state === "next") || steps.find((step) => step.state === "blocked") || null;
}

/** The pill's text: what to do next in one short phrase. */
export function pillText(guide) {
  if (!guide) return null;
  const step = currentStep(guide);
  if (step) return `${step.state === "blocked" ? "Blocked" : "Next"}: ${step.title}`;
  return guide.complete ? "All steps done" : null;
}

/** A finished job's result row: the step the guide now points at, in words. */
export function resultText(guide) {
  if (!guide) return null;
  const step = currentStep(guide);
  if (step) {
    const reason = step.reason && step.reason.message ? ` ${step.reason.message}.` : "";
    return `${step.state === "blocked" ? "Blocked at" : "Next step"}: ${step.title}.${reason}`;
  }
  return guide.complete ? "Every step of the guided path is done." : null;
}

/** The host of a fix link, shown beside it (only the CLI's fixed table produces one). */
export function linkHost(url) {
  try {
    const parsed = new URL(url);
    // The scheme without its colon: static files name no scheme-and-colon literal at all
    // (test_center_static's external-origin guard).
    return parsed.protocol.replace(/:$/, "") === "https" ? parsed.host : null;
  } catch {
    return null;
  }
}

// `new agent`'s name rule (`newagent.NAME_PATTERN`): checked before the request, and again by the
// server and by `new agent` itself.
const STARTER_NAME = /^[a-z]([a-z0-9-]{0,29}[a-z0-9])?$/;

/** Why a starter name is refused, as a sentence, or null when it is accepted. */
export function starterNameProblem(name) {
  if (!name) return "Type a name first.";
  if (STARTER_NAME.test(name)) return null;
  return "Use a lowercase letter first, then lowercase letters, digits and dashes (at most 31, not ending in a dash).";
}
