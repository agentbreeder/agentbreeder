// Try it (#823, M7): chat with a project's production deployment through `invoke NAME -`, one
// message in flight per tab, with a per-turn inspector (tools, sources, latency, tokens, trace).
//
// S-12: the message goes to the server as JSON and to `invoke` on stdin; the conversation lives
// only in this module's memory (gone on reload, never in localStorage). Agent output is text
// through <Text>: no Markdown, no auto-linking, no link built from a reply. A trace id is shown as
// text; a trace link waits for #907, and cost for #906.

import { href } from "../lib/router.js";
import { visible } from "../lib/text.js";
import {
  MAX_MESSAGE,
  addPending,
  customerProblem,
  emptyConversation,
  failureText,
  latencyText,
  messageProblem,
  namesText,
  pending,
  productionRow,
  settle,
  thrownEnvelope,
  tokensText,
  unavailable,
} from "../lib/tryit.js";
import { Card, CopyButton, Notice, Text, ViewState, nextId, useConsole } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useEffect, useRef, useState } from "../ui/preact.js";

// Conversations by project and agent, for this tab only: leaving the screen and coming back keeps
// them, and a reply that lands while the screen is closed is kept too.
const conversations = new Map();
const listeners = new Map();

function update(key, change) {
  const next = change(conversations.get(key) || emptyConversation());
  conversations.set(key, next);
  const listener = listeners.get(key);
  if (listener) listener(next);
  return next;
}

// One listener per key: there is one Try it route, so at most one screen shows a conversation.
function useConversation(key) {
  const [conversation, setConversation] = useState(() => conversations.get(key) || emptyConversation());
  useEffect(() => {
    setConversation(conversations.get(key) || emptyConversation());
    listeners.set(key, setConversation);
    return () => {
      if (listeners.get(key) === setConversation) listeners.delete(key);
    };
  }, [key]);
  return conversation;
}

export function TryIt({ project, params }) {
  const name = params.name;
  const [view] = useView(`/api/projects/${project.id}/deployments`, project.id);
  const row = productionRow(view.data, name);
  const why = view.data ? unavailable(row, name) : null;
  return html`
    <p class="try-agent"><span class="muted">Agent</span> <strong><${Text} value=${name} /></strong>${row ? html` <span class="muted">· production · <${Text} value=${row.target} /> · <${Text} value=${row.state} /></span>` : null}</p>
    <${Card} title="Before you send" id="try-notes">
      <ul class="try-notes">
        <li>Each message calls the model and costs money.</li>
        <li>The local endpoint is unauthenticated: anything on this machine that can reach its port can call it, and can name any customer or session ID (#780).</li>
        <li>Replies are shown as plain text. The conversation stays in this tab only; reloading the page clears it.</li>
      </ul>
    <//>
    ${view.data && why
      ? html`<${Card} title="Not available">
          <p><${Text} value=${why} /></p>
          <p class="row"><a class="action secondary small" href=${href("/release")}>Go to Build and release</a> <a class="action ghost small" href=${href("/deployments")}>See Deployments</a></p>
        <//>`
      : null}
    ${!view.data ? html`<${Card} title="Conversation" busy=${view.loading}><${ViewState} view=${view} /><//>` : null}
    ${view.data && !why ? html`<${Chat} project=${project} name=${name} />` : null}
  `;
}

function Chat({ project, name }) {
  const { client } = useConsole();
  const key = `${project.id}/${name}`;
  const conversation = useConversation(key);
  const [text, setText] = useState("");
  const [customer, setCustomer] = useState("");
  const [shown, setShown] = useState(null); // a problem with the form itself, after Send
  const [status, setStatus] = useState("");
  const waiting = pending(conversation);
  const customerWhy = customerProblem(customer);
  const messageWhy = useRef(nextId("why")).current;
  const customerHelp = useRef(nextId("help")).current;
  const transcript = useRef(null);
  const follow = useRef(true); // the transcript was at its end before this change
  const count = conversation.turns.length;
  useEffect(() => {
    // Keep the newest turn in view only when the reader was already at the end; someone reading
    // an earlier turn's details is not moved when a reply lands.
    const box = transcript.current;
    if (box && follow.current) box.scrollTop = box.scrollHeight;
  }, [count, waiting]);
  const onScroll = (event) => {
    const box = event.currentTarget;
    follow.current = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  };
  const announce = (text) => {
    // Cleared first, so the same words twice in a row are announced twice.
    setStatus("");
    requestAnimationFrame(() => setStatus(text));
  };
  const send = async (event) => {
    event.preventDefault();
    // Send stays the same enabled element while a turn is waiting, so focus never drops to the
    // page (#910 finding 1); a click then only says why nothing happened.
    if (waiting) {
      setShown("Wait for the reply to the last message first.");
      return;
    }
    const problem = messageProblem(text) || customerWhy;
    setShown(problem);
    if (problem) return;
    const message = text.trim();
    const session = conversation.session;
    let id;
    update(key, (current) => {
      const [next, added] = addPending(current, message);
      id = added;
      return next;
    });
    setText("");
    follow.current = true; // sending moves to the end
    announce("Message sent. Waiting for the reply.");
    let envelope;
    try {
      envelope = await client.request(`/api/projects/${project.id}/agents/${name}/try`, {
        body: { message, session, customer: customer.trim() || null },
      });
    } catch (error) {
      envelope = thrownEnvelope(error);
    }
    const next = update(key, (current) => settle(current, id, envelope));
    const settled = next.turns.find((turn) => turn.id === id);
    // The reply's opening words go to the live region, so a screen reader hears the answer (by
    // code point, so a pair is never cut). A failure is announced by its bubble's alert instead.
    if (settled && settled.state === "reply") announce(`Reply from ${visible(name)}: ${Array.from(visible(settled.turn.reply)).slice(0, 300).join("")}`);
    else announce("");
  };
  const reset = () => {
    update(key, () => emptyConversation());
    announce("New conversation started.");
  };
  const onKey = (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) send(event);
  };
  return html`
    <${Card} title="Conversation" busy=${waiting}>
      ${count
        ? html`<ol class="transcript" ref=${transcript} aria-label="Conversation turns" tabindex="0" onScroll=${onScroll}>
            ${conversation.turns.map((turn) => html`<${Turn} key=${turn.id} turn=${turn} name=${name} />`)}
          </ol>`
        : html`<p class="muted">No messages yet. Ask ${html`<${Text} value=${name} />`} something its evals cover.</p>`}
      <p class="visually-hidden" role="status">${status}</p>
      <form class="try-form" onSubmit=${send}>
        <label class="field wide">
          <span>Message</span>
          <textarea rows="3" value=${text} spellcheck="true" aria-describedby=${shown ? messageWhy : undefined} aria-invalid=${shown ? "true" : "false"} onInput=${(e) => setText(e.currentTarget.value)} onKeyDown=${onKey}></textarea>
        </label>
        <div class="row try-controls">
          <label class="field">
            <span>Customer ID (optional)</span>
            <input type="text" value=${customer} spellcheck="false" autocomplete="off" aria-describedby=${customerHelp} aria-invalid=${customerWhy ? "true" : "false"} onInput=${(e) => setCustomer(e.currentTarget.value)} />
          </label>
          <button type="submit" class="action primary" aria-describedby=${shown ? messageWhy : undefined}>${waiting ? "Waiting for reply…" : "Send"}</button>
          <button type="button" class="action ghost small" onClick=${reset} disabled=${waiting || !count}>New conversation</button>
        </div>
        <p class="muted small" id=${customerHelp}>${customerWhy || "Sent as the customer_id; not authenticated."} Up to ${MAX_MESSAGE} characters; Ctrl or Cmd + Enter sends.</p>
        ${shown ? html`<p class="reason" id=${messageWhy}>${shown}</p>` : null}
        ${conversation.session ? html`<p class="muted small">Session <code><${Text} value=${conversation.session} /></code>: later messages continue it.</p>` : null}
      </form>
    <//>
  `;
}

function Turn({ turn, name }) {
  return html`<li class=${`turn ${turn.state}`}>
    <div class="bubble you"><span class="who">You</span><p class="turn-text"><${Text} value=${turn.message} /></p></div>
    ${turn.state === "pending" ? html`<div class="bubble agent pending" aria-busy="true"><span class="who"><${Text} value=${name} /></span><p class="muted">Waiting for the reply (up to 3 minutes)…</p></div>` : null}
    ${turn.state === "failed" ? html`<div class="bubble agent failed"><span class="who"><${Text} value=${name} /></span><${Notice} notice=${{ tone: "error", text: failureText(turn.problem) }} /></div>` : null}
    ${turn.state === "reply"
      ? html`<div class="bubble agent">
          <span class="who"><${Text} value=${name} /></span>
          <p class="turn-text"><${Text} value=${turn.turn.reply} /></p>
          ${turn.turn.truncated ? html`<p class="muted small">The reply was longer than the console shows; the rest is cut.</p>` : null}
          <${Inspector} turn=${turn.turn} />
        </div>`
      : null}
  </li>`;
}

/** The turn's inspector: what the agent used and what the turn took. */
function Inspector({ turn }) {
  const trace = turn.trace || {};
  return html`<details class="inspector">
    <summary>Turn details <span class="muted small">${latencyText(turn.latency_ms)} · ${tokensText(turn.tokens)} tokens</span></summary>
    <dl class="facts">
      <dt>Tools</dt><dd><${Text} value=${namesText(turn.tools)} /></dd>
      <dt>Sources</dt><dd><${Text} value=${namesText(turn.sources)} /></dd>
      <dt>Latency</dt><dd>${latencyText(turn.latency_ms)} <span class="muted small">(console to reply, through invoke)</span></dd>
      <dt>Tokens</dt><dd>${tokensText(turn.tokens)}${Number.isInteger(turn.model_calls) ? html` <span class="muted small">over ${turn.model_calls} model call${turn.model_calls === 1 ? "" : "s"}</span>` : null}</dd>
      <dt>Cost</dt><dd class="muted">Not measured yet</dd>
      <dt>Trace</dt><dd>${trace.id ? html`<code><${Text} value=${trace.id} /></code> <${CopyButton} value=${trace.id} label="trace ID" /> <span class="muted small">Find it in Phoenix if this stack runs it; links come later.</span>` : html`<span class="muted">No trace for this turn</span>`}</dd>
      ${turn.model ? html`<dt>Model alias</dt><dd><${Text} value=${turn.model} /></dd>` : null}
    </dl>
  </details>`;
}
