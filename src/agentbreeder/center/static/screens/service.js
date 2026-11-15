// One stack service (#822, M6): its configuration from `services status --json`, a link to its
// logs, Start / Stop / Restart (the Overview tab) and its usage (the Usage tab, part 4). Each button is one `services ACTION DIR NAME --json` short write
// on the server; a stop or restart while deployments use the stack asks first (SERVICE_IN_USE),
// then sends the request again with `yes`. Refusals (PORT_BUSY, SERVICES_BUSY, ...) are the CLI's
// message as it wrote it.

import { portsText } from "../lib/format.js";
import { href } from "../lib/router.js";
import { ACTIONS, actionReason, cancelledText, doneText, inUseConfirm } from "../lib/services.js";
import { Action, Actions, Card, Notice, Text, ViewState, useConfirm, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { LogsLink } from "./logs.js";
import { UsageTab } from "./usage.js";
import { html, useEffect, useRef, useState } from "../ui/preact.js";

function ServiceDetail({ project, name, tab }) {
  const { client, busyReason, docker } = useConsole();
  const base = `/api/projects/${project.id}`;
  const [services, reload] = useView(`${base}/services`, project.id);
  const [notice, setNotice] = useState(null);
  const [pending, setPending] = useState(null);
  const [dialog, confirm] = useConfirm();
  const controls = useRef(null);
  // {action, read}: the button that gets focus back once the state is read again (`read` turns
  // true when that re-read starts, so the focus never follows the state from before the action).
  const refocus = useRef(null);
  const all = (services.data && services.data.services) || {};
  const known = Object.prototype.hasOwnProperty.call(all, name);
  const info = known ? all[name] : null;
  // While the Docker blocker shows (#972), say why the buttons are off instead of letting the CLI
  // refuse with DOCKER_FAILED.
  const dockerDown = docker && docker.reachable === false ? "Docker is not reachable; start it, then use Retry at the top of the page." : false;
  const busy = pending ? `${ACTIONS.find(([action]) => action === pending)[1]} is running.` : dockerDown || busyReason(project.id);

  const send = (action, yes) => client.request(`${base}/services/${name}/${action}`, { body: yes ? { yes: true } : {} });
  const run = async (action) => {
    setNotice(null);
    setPending(action);
    try {
      let reply = await send(action, false);
      if (reply.error && reply.error.code === "SERVICE_IN_USE") {
        if (!(await confirm(inUseConfirm(action, name, reply.error.message)))) {
          setNotice({ tone: "info", text: cancelledText(action, name) });
          return;
        }
        reply = await send(action, true);
      }
      if (reply.error) setNotice({ tone: "error", text: reply.error.message });
      else setNotice({ tone: "ok", text: doneText(reply.data) });
    } catch (error) {
      if (error.kind !== "session") setNotice({ tone: "error", text: error.message });
    } finally {
      refocus.current = { action, read: false };
      setPending(null);
      reload();
    }
  };

  // The buttons are off while an action runs (and the confirm records a disabled opener), so the
  // browser drops focus. Once the new state is read, give it back to the button used, or to the
  // first one that applies now, unless the person has moved focus elsewhere meanwhile.
  useEffect(() => {
    const wanted = refocus.current;
    if (!wanted || pending) return;
    if (services.loading) {
      wanted.read = true;
      return;
    }
    if (!wanted.read || !controls.current) return;
    const index = ACTIONS.findIndex(([action]) => action === wanted.action);
    refocus.current = null;
    const box = controls.current;
    // A frame later, so it runs after the confirm's own focus restore (also a frame later).
    requestAnimationFrame(() => {
      const active = document.activeElement;
      const lost = !active || active === document.body || active.matches("main h1") || box.contains(active);
      if (!lost || !box.isConnected) return;
      const buttons = [...box.querySelectorAll("button")];
      const target = buttons[index] && !buttons[index].disabled ? buttons[index] : buttons.find((button) => !button.disabled);
      if (target) target.focus();
    });
  }, [pending, services]);

  if (services.data && !known) {
    return html`<p>This stack has no service "<${Text} value=${name} />". <a href=${href("/services")}>Back to Services</a>.</p>`;
  }
  return html`
    ${dialog}
    <p><a href=${href("/services")}>All services</a> · <${LogsLink} kind="service" name=${name} label=${`Logs of ${name}`} /></p>
    <${Tabs} name=${name} tab=${tab} />
    ${tab === "usage" ? html`<${UsageTab} project=${project} name=${name} info=${info} />` : null}
    ${tab === "overview" ? html`<${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    <${Card} title="Config" busy=${services.loading}>
      ${info
        ? html`<dl class="facts">
            <dt>State</dt><dd><${Text} value=${info.state} /></dd>
            <dt>Health</dt><dd><${Text} value=${info.health || "no health check"} /></dd>
            <dt>Host ports</dt><dd><${Text} value=${portsText(info)} />${portsText(info) === "internal only" ? html` <span class="muted small">(reached by agents on the stack network, not from this machine)</span>` : null}</dd>
            ${services.data.stack ? html`<dt>Stack</dt><dd><code><${Text} value=${services.data.stack} /></code></dd>` : null}
          </dl>`
        : null}
      <${ViewState} view=${services} />
    <//>
    <${Card} title="Controls">
      <p class="muted small">Acts on this container only: nothing is rebuilt, recreated or pulled. Stop and Restart ask first while deployments use the stack.</p>
      <div ref=${controls}><${Actions} disabled=${busy}>
        ${ACTIONS.map(([action, label]) => html`<${Action} key=${action} tone=${action === "start" ? "primary" : "secondary"} disabled=${info ? actionReason(info, action) : "Reading the service…"} onClick=${() => run(action)}>${label}<//>`)}
      <//></div>
    <//>` : null}
  `;
}

/** The detail's tabs: [key, label]. Overview is the service's own address. */
export const SERVICE_TABS = [
  ["overview", "Overview"],
  ["usage", "Usage"],
];

function tabHref(name, key) {
  return key === "overview" ? href("/services/:name", { name }) : href("/services/:name/:tab", { name, tab: key });
}

function Tabs({ name, tab }) {
  return html`<nav class="agent-tabs" aria-label="Service sections">
    <ul>${SERVICE_TABS.map(([key, label]) => html`<li key=${key}><a href=${tabHref(name, key)} aria-current=${key === tab ? "page" : undefined}>${label}</a></li>`)}</ul>
  </nav>`;
}

/**
 * Keyed by project and name, so another service starts with a fresh notice and usage. One screen for both tabs (the tab
 * is a route parameter), so switching tabs keeps focus on the tab link.
 */
function ServiceScreen({ project, params }) {
  const tab = params.tab || "overview";
  if (!SERVICE_TABS.some(([key]) => key === tab)) {
    return html`<p>A service has no "<${Text} value=${tab} />" tab. <a href=${href("/services/:name", { name: params.name })}>Open its overview</a>.</p>`;
  }
  return html`<${ServiceDetail} key=${`${project.id}:${params.name}`} project=${project} name=${params.name} tab=${tab} />`;
}

/** For screens/index.js: the detail sits under the Services entry in the sidebar. */
const DETAIL = { title: (params) => `Service ${params.name}`, section: "/services/:name", navAs: "/services", scoped: true, screen: ServiceScreen };
export const SERVICE_ROUTES = [
  { pattern: "/services/:name", ...DETAIL },
  { pattern: "/services/:name/:tab", ...DETAIL },
];
