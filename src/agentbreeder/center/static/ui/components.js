// Shared pieces every screen uses. All untrusted text goes through <Text> (S-35): Preact places it
// as text nodes, and control, bidi and invisible characters show as visible escapes.

import { segments, visible } from "../lib/text.js";
import { createContext, html, useCallback, useContext, useEffect, useRef, useState } from "./preact.js";

export const Console = createContext(null);
export const useConsole = () => useContext(Console);

let uid = 0;
export const nextId = (prefix) => `${prefix}-${(uid += 1)}`;

/**
 * Untrusted text. Escapes are styled apart from the text around them, and a literal "\x1b" typed
 * in the source stays plain, so a line cannot fake an escape. `copy` adds a Copy button that copies
 * the escaped form, never a raw control character.
 */
export function Text({ value, block = false, copy = false, label = "text" }) {
  const runs = segments(value);
  const body = runs.map((run) => (run.escaped ? html`<span class="esc" title="A control or invisible character, shown escaped">${run.text}</span>` : run.text));
  const content = block ? html`<pre class="text" tabindex="0" aria-label=${label}>${body}</pre>` : html`<span class="text">${body}</span>`;
  if (!copy) return content;
  return html`<div class="copyable">${content}<${CopyButton} value=${visible(value)} label=${label} /></div>`;
}

export function CopyButton({ value, label = "text" }) {
  const [state, setState] = useState("");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setState("Copied");
    } catch {
      setState("Copy failed: select the text instead");
    }
  };
  return html`<span class="copy"><button type="button" class="action ghost small" onClick=${copy} aria-label=${`Copy ${label}`}>Copy</button><span role="status" class="muted small">${state}</span></span>`;
}

const Group = createContext(null);

/**
 * A button that says why it is off (#811: a reason under every disabled control). `disabled` is
 * false or the reason, as a sentence. The reason is linked with aria-describedby. Inside
 * <Actions disabled=...>, the group's reason applies to every button and is shown once.
 */
export function Action({ onClick, disabled = false, tone = "primary", children, type = "button", small = false }) {
  const id = useRef(nextId("why")).current;
  const group = useContext(Group);
  const own = typeof disabled === "string" && disabled ? disabled : null;
  const shared = !own && !disabled && group && group.reason ? group : null;
  const off = Boolean(disabled) || Boolean(shared);
  const button = html`<button type=${type} class=${`action ${tone}${small ? " small" : ""}`} disabled=${off} aria-describedby=${own ? id : shared ? shared.id : undefined} onClick=${onClick}>${children}</button>`;
  if (!own) return button;
  return html`<span class="action-wrap">${button}<span class="reason" id=${id}>${own}</span></span>`;
}

/**
 * Actions that share one reason for being off (a running job, say), shown once after them.
 * `block` keeps the children's own layout (a table with a button per row).
 */
export function Actions({ disabled = false, block = false, children }) {
  const id = useRef(nextId("why")).current;
  const reason = typeof disabled === "string" && disabled ? disabled : null;
  return html`<${Group.Provider} value=${reason ? { reason, id } : null}>
    <div class="actions">
      ${block ? children : html`<div class="row">${children}</div>`}
      ${reason ? html`<p class="reason" id=${id}>${reason}</p>` : null}
    </div>
  <//>`;
}

/** A screen-scoped notice: it lives in the screen's own state, so leaving the screen clears it. */
export function Notice({ notice, onDismiss }) {
  if (!notice) return null;
  const role = notice.tone === "error" ? "alert" : "status";
  return html`<div class=${`notice ${notice.tone || "info"}`} role=${role}>
    <span><${Text} value=${notice.text} /></span>
    ${onDismiss ? html`<button type="button" class="action ghost small" onClick=${onDismiss} aria-label="Dismiss">Dismiss</button>` : null}
  </div>`;
}

export function Card({ title, children, busy = false, id }) {
  const heading = useRef(nextId("card")).current;
  return html`<section class="card" aria-labelledby=${title ? heading : undefined} aria-busy=${busy ? "true" : "false"} id=${id}>
    ${title ? html`<h2 id=${heading}>${title}</h2>` : null}
    ${children}
  </section>`;
}

export function Skeleton({ lines = 3 }) {
  return html`<div class="skeleton" aria-hidden="true">${Array.from({ length: lines }, (_, i) => html`<span key=${i}></span>`)}</div>`;
}

/** A read's state: loading, its error (with last-known data marked stale), or nothing. */
export function ViewState({ view, empty }) {
  if (view.loading && !view.data) return html`<${Skeleton} />`;
  if (view.error && !view.stale) return html`<p class="muted"><${Text} value=${view.error.message || "Could not read this."} /></p>`;
  if (view.stale) return html`<p class="muted">Showing last-known data. It refreshes when the running job ends.</p>`;
  if (empty) return html`<p class="muted">${empty}</p>`;
  return null;
}

/**
 * A confirm dialog. It focuses Cancel when it opens and gives focus back to the control that
 * opened it when it closes (#811 walkthrough). Returns [dialog element, ask(options) -> Promise].
 */
export function useConfirm() {
  const [request, setRequest] = useState(null);
  const ask = useCallback(
    (options) => new Promise((resolve) => setRequest({ ...options, resolve, opener: document.activeElement })),
    [],
  );
  const done = (answer) => {
    const { resolve, opener } = request;
    setRequest(null);
    resolve(answer);
    requestAnimationFrame(() => {
      if (opener && opener.isConnected && !opener.disabled && typeof opener.focus === "function") opener.focus();
      else document.querySelector("main h1")?.focus();
    });
  };
  const dialog = request ? html`<${ConfirmDialog} ...${request} onDone=${done} />` : null;
  return [dialog, ask];
}

function ConfirmDialog({ title, text, confirm = "Confirm", tone = "danger", onDone }) {
  const ref = useRef(null);
  const cancel = useRef(null);
  const titleId = useRef(nextId("dialog")).current;
  useEffect(() => {
    const dialog = ref.current;
    dialog.showModal();
    cancel.current.focus();
    const onCancel = (event) => {
      event.preventDefault();
      onDone(false);
    };
    dialog.addEventListener("cancel", onCancel);
    return () => {
      dialog.removeEventListener("cancel", onCancel);
      if (dialog.open) dialog.close();
    };
  }, []);
  return html`<dialog ref=${ref} aria-labelledby=${titleId} class="confirm">
    <h2 id=${titleId}>${title}</h2>
    <p>${text}</p>
    <div class="row end">
      <button type="button" class="action secondary" ref=${cancel} onClick=${() => onDone(false)}>Cancel</button>
      <button type="button" class=${`action ${tone}`} onClick=${() => onDone(true)}>${confirm}</button>
    </div>
  </dialog>`;
}

/**
 * A labelled, scrollable table; `columns` are header strings (an empty one is an actions column,
 * named for screen readers only).
 */
export function Table({ label, columns, children }) {
  return html`<div class="table-wrap" role="region" aria-label=${`${label} table`} tabindex="0">
    <table aria-label=${label}>
      <thead><tr>${columns.map((column) => html`<th scope="col">${column || html`<span class="visually-hidden">Actions</span>`}</th>`)}</tr></thead>
      <tbody>${children}</tbody>
    </table>
  </div>`;
}
