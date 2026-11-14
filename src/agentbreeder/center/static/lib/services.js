// Service controls (#822, M6): which of Start / Stop / Restart apply to one stack service, and the
// words around them. Pure, so `node --test` covers it. The console never reads Docker's text: the
// state comes from `services status --json`, refusals are the CLI's coded messages.

import { serviceUp } from "./format.js";

/** [action, button label, past tense] in button order. */
export const ACTIONS = [
  ["start", "Start", "started"],
  ["stop", "Stop", "stopped"],
  ["restart", "Restart", "restarted"],
];

const PAST = Object.fromEntries(ACTIONS.map(([action, , past]) => [action, past]));
const LABEL = Object.fromEntries(ACTIONS.map(([action, label]) => [action, label]));

/**
 * Why `action` is off for a service in this state, or null when it applies. A service with no
 * container needs `services up` (the CLI says SERVICE_NOT_CREATED); a running one cannot be
 * started, a stopped one cannot be stopped or restarted (`compose restart` would start it).
 */
export function actionReason(info, action) {
  if (!info || !info.state || info.state === "absent") return "It has no container yet: start the stack's services on the Services screen.";
  const up = serviceUp(info);
  if (action === "start") return up ? "It is already running." : null;
  return up ? null : "It is not running.";
}

/**
 * The deployments a SERVICE_IN_USE refusal names. The CLI's message is
 * `these deployments use the stack: A, B; add --yes to stop NAME anyway`; anything else (a
 * truncated message, say) gives [], and the confirm then says only that deployments use it: the
 * CLI's `--yes` advice is not for the console.
 */
export function inUseNames(message) {
  const match = /use the stack: (.+?); add --yes\b/.exec(String(message || ""));
  if (!match) return [];
  return match[1].split(", ").map((name) => name.trim()).filter(Boolean);
}

/** The confirm shown after SERVICE_IN_USE: what will stop working, named. */
export function inUseConfirm(action, name, message) {
  const names = inUseNames(message);
  const who = names.length
    ? `${names.length === 1 ? "This deployment uses" : "These deployments use"} the stack: ${names.join(", ")}.`
    : "Deployments use this stack.";
  const effect = action === "stop"
    ? `They cannot reach ${name} until it is started again.`
    : `They cannot reach ${name} while it restarts.`;
  return {
    title: `${LABEL[action]} ${name} anyway?`,
    text: `${who} ${effect}`,
    confirm: `${LABEL[action]} ${name}`,
  };
}

/** The sentence after a done action, from the CLI's answer (state read after the action). */
export function doneText(answer) {
  if (!answer) return "";
  const state = `${answer.state}${answer.health ? ` (${answer.health})` : ""}`;
  const settling = answer.health === "starting" ? " Its health check has not passed yet." : "";
  return `${answer.service} ${PAST[answer.action] || answer.action}: now ${state}.${settling}`;
}

/** The sentence when the person cancels the in-use confirm: nothing was sent. */
export function cancelledText(action, name) {
  return `Nothing was done: ${name} was not ${PAST[action] || action}.`;
}
