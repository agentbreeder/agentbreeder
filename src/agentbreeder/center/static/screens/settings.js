// Settings (M2 #818): change the password (needs the current one, S-28), the session length,
// sign out everywhere, the theme, and the console's recent security events (S-30). A forgotten
// password is reset only from the terminal (`agentbreeder up --reset-password`).

import { eventTime, eventWords, hoursLabel, MIN_LENGTH, newPasswordProblem } from "../lib/signin.js";
import { Action, Card, nextId, Notice, Table, Text, useConfirm, useConsole, ViewState } from "../ui/components.js";
import { useView } from "../ui/data.js";
import { html, useRef, useState } from "../ui/preact.js";
import { ThemeToggle } from "../ui/shell.js";
import { PasswordFacts } from "../ui/signin.js";

export function Settings() {
  const [view, reload] = useView("/api/settings", null);
  const data = view.data;
  return html`
    <${ViewState} view=${view} />
    ${data
      ? html`
          <${ChangePassword} onChanged=${reload} />
          <${Sessions} settings=${data} onSaved=${reload} />
          <${Card} title="Appearance">
            <p class="muted small">The theme is kept in this browser.</p>
            <${ThemeToggle} label="Theme (Settings)" />
          <//>
          <${Events} events=${data.events || []} />
          <${Card} title="What the password protects"><${PasswordFacts} /><//>
        `
      : null}
  `;
}

function ChangePassword({ onChanged }) {
  const { client, replaceSession } = useConsole();
  const ids = useRef({ current: nextId("current"), next: nextId("next"), again: nextId("again"), rules: nextId("rules") }).current;
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    if (busy) return;
    const local = !current ? "Enter your current password." : newPasswordProblem(next, again);
    if (local) {
      setNotice({ tone: "error", text: local });
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const data = await client.post("/api/password", { current, password: next });
      replaceSession(data);
      setCurrent("");
      setNext("");
      setAgain("");
      setNotice({ tone: "ok", text: "Password changed. Every other tab and browser was signed out; this tab stays signed in." });
      onChanged();
    } catch (error) {
      if (error.kind !== "session") setNotice({ tone: "error", text: error.message });
    } finally {
      setBusy(false);
    }
  };
  return html`<${Card} title="Password">
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    <form class="stack signin" onSubmit=${submit} noValidate>
      <label for=${ids.current}>Current password</label>
      <input id=${ids.current} type="password" autocomplete="current-password" value=${current} onInput=${(event) => setCurrent(event.currentTarget.value)} />
      <label for=${ids.next}>New password</label>
      <input id=${ids.next} type="password" autocomplete="new-password" aria-describedby=${ids.rules} value=${next} onInput=${(event) => setNext(event.currentTarget.value)} />
      <p class="muted small" id=${ids.rules}>At least ${MIN_LENGTH} characters. Common passwords are refused.</p>
      <label for=${ids.again}>Type the new password again</label>
      <input id=${ids.again} type="password" autocomplete="new-password" value=${again} onInput=${(event) => setAgain(event.currentTarget.value)} />
      <div class="row"><${Action} type="submit" disabled=${busy && "Changing the password…"}>Change password<//></div>
    </form>
    <p class="muted small">Forgot it? Run <code>agentbreeder up --reset-password</code> in a terminal; it ends every open session.</p>
  <//>`;
}

function Sessions({ settings, onSaved }) {
  const { client, signedOutEverywhere } = useConsole();
  const id = useRef(nextId("hours")).current;
  const [hours, setHours] = useState(settings.session_hours);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [dialog, confirm] = useConfirm();
  const save = async () => {
    setBusy(true);
    setNotice(null);
    try {
      await client.post("/api/settings", { session_hours: hours });
      setNotice({ tone: "ok", text: `Sessions now last ${hoursLabel(hours)}, open tabs included.` });
      onSaved();
    } catch (error) {
      if (error.kind !== "session") setNotice({ tone: "error", text: error.message });
    } finally {
      setBusy(false);
    }
  };
  const everywhere = async () => {
    const yes = await confirm({
      title: "Sign out everywhere?",
      text: "Every tab and browser signed in to this console, this one included, must sign in again. Running jobs keep running.",
      confirm: "Sign out everywhere",
    });
    if (!yes) return;
    try {
      await client.post("/api/signout-everywhere");
      signedOutEverywhere();
    } catch (error) {
      if (error.kind !== "session") setNotice({ tone: "error", text: error.message });
    }
  };
  const choices = settings.session_choices || [];
  return html`<${Card} title="Sessions">
    ${dialog}
    <${Notice} notice=${notice} onDismiss=${() => setNotice(null)} />
    <p>A tab's session ends after the session length, or after ${settings.idle_minutes} minutes without activity in the console (open logs and job polls do not count). Restarting <code>agentbreeder up</code> ends every session.</p>
    <div class="row">
      <label class="field" for=${id}><span>Session length</span>
        <select id=${id} onChange=${(event) => setHours(Number(event.currentTarget.value))}>
          ${choices.map((choice) => html`<option value=${choice} selected=${choice === hours}>${hoursLabel(choice)}</option>`)}
        </select>
      </label>
      <${Action} tone="secondary" onClick=${save} disabled=${busy ? "Saving…" : hours === settings.session_hours && "This is the current length."}>Save<//>
    </div>
    <h3>Sign out everywhere</h3>
    <p class="muted small">Ends every session of this console, in every tab and browser.</p>
    <${Action} tone="danger" onClick=${everywhere}>Sign out everywhere<//>
  <//>`;
}

function Events({ events }) {
  return html`<${Card} title="Recent sign-in activity">
    <p class="muted small">The console's last ${events.length} security events, newest first. They hold no passwords or links.</p>
    ${events.length
      ? html`<${Table} label="Security events" columns=${["Time", "Event", "Outcome", "From"]}>
          ${events.map((row, index) => {
            const words = eventWords(row);
            return html`<tr key=${index}><td>${eventTime(row.at)}</td><td><${Text} value=${words.event} /></td><td><${Text} value=${words.outcome} /></td><td><${Text} value=${words.source} /></td></tr>`;
          })}
        <//>`
      : html`<p class="muted">No events yet.</p>`}
  <//>`;
}
