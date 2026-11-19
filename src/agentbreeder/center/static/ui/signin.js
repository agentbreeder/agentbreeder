// Sign-in screens (M2 #818): first-run setup, the sign-in page, and the re-auth dialog that opens
// over the page when a session ends, so an open editor keeps its text (spec §8.2). The password
// only mints this tab's header token (sessionStorage); there is no cookie (§8.1 #1).

import { backoffSeconds, EMAIL, MIN_LENGTH, newPasswordProblem } from "../lib/signin.js";
import { nextId, Text } from "./components.js";
import { html, useEffect, useRef, useState } from "./preact.js";

const LINK_HELP = "In the terminal running agentbreeder up, press Enter for a one-time link.";

/** What the password protects, in two sentences (spec §8.2, S-02). */
export function PasswordFacts() {
  return html`<p class="muted small">
    The password keeps other users of this computer, web pages and local containers out of the
    console. It does not protect against programs running as your own account, which can read the
    console's files and run the CLI. Open only the address <code>agentbreeder up</code> prints.
  </p>`;
}

/**
 * Email and password. `client` is a client whose 401 does not end the page's session (there is
 * none yet). Calls `onSignedIn(data)` with the server's reply.
 */
export function SignInForm({ client, onSignedIn, autoFocus = true }) {
  const ids = useRef({ email: nextId("email"), password: nextId("password"), problem: nextId("problem") }).current;
  const [email, setEmail] = useState(EMAIL);
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useState(0);
  const field = useRef(null);
  useEffect(() => {
    if (autoFocus) field.current?.focus();
  }, []);
  useEffect(() => {
    if (!wait) return undefined;
    const timer = setTimeout(() => setWait((left) => Math.max(0, left - 1)), 1000);
    return () => clearTimeout(timer);
  }, [wait]);
  const submit = async (event) => {
    event.preventDefault();
    if (busy || wait) return;
    if (!password) {
      setProblem("Enter your password.");
      field.current?.focus();
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      const data = await client.post("/api/session", { email, password });
      setPassword("");
      onSignedIn(data);
    } catch (error) {
      setProblem(error.kind === "unreachable" ? "The console is not reachable at this address." : error.message);
      setWait(backoffSeconds(error));
      field.current?.focus();
    } finally {
      setBusy(false);
    }
  };
  const off = busy ? "Checking…" : wait ? `Wait ${wait} s, or use a one-time link from the terminal.` : false;
  return html`<form class="stack signin" onSubmit=${submit} noValidate>
    <label for=${ids.email}>Email</label>
    <input id=${ids.email} type="email" autocomplete="username" value=${email} onInput=${(event) => setEmail(event.currentTarget.value)} />
    <label for=${ids.password}>Password</label>
    <input id=${ids.password} type="password" autocomplete="current-password" ref=${field} value=${password}
      aria-invalid=${problem ? "true" : "false"} aria-describedby=${problem ? ids.problem : undefined}
      onInput=${(event) => setPassword(event.currentTarget.value)} />
    ${problem ? html`<p class="outcome error" role="alert" id=${ids.problem}><${Text} value=${problem} /></p>` : null}
    <div class="row">
      <button type="submit" class="action" disabled=${Boolean(off)} aria-describedby=${off ? `${ids.problem}-why` : undefined}>Sign in</button>
      ${off ? html`<span class="reason" id=${`${ids.problem}-why`} role="status">${off}</span>` : null}
    </div>
    <p class="muted small">Forgot it? Run <code>agentbreeder up --reset-password</code> in a terminal. ${LINK_HELP}</p>
  </form>`;
}

const REASONS = {
  ended: ["Session ended", "This tab's session ended: it was open longer than the session length or idle 1 hour, or the console was restarted, or you signed out everywhere."],
  used: ["Sign in", "This link was already used or has expired."],
  set: ["Sign in", `Password set for ${EMAIL}. Sign in with it.`],
  everywhere: ["Signed out", "You signed out everywhere: every tab and browser must sign in again."],
  none: ["Sign in", "There is no session in this tab."],
};

/**
 * Not signed in, or the session ended. With a password set it shows the sign-in form; before
 * the first password, only the terminal link can sign in (S-28). Signing in returns to the route.
 */
export function SignedOut({ reason, client, onSignedIn }) {
  const [state, setState] = useState(null); // {password_set} | {error}
  useEffect(() => {
    client.get("/api/auth").then(
      (data) => setState(data || {}),
      (error) => setState({ error: error.kind === "unreachable" ? "The console is not reachable at this address." : error.message }),
    );
  }, []);
  const [title, text] = REASONS[reason] || REASONS.none;
  const heading = state && !state.error && !state.password_set && reason !== "ended" ? "Set up the console" : title;
  return html`<main id="main" class="center-page">
    <h1 tabindex="-1">${heading}</h1>
    <p>${text}</p>
    ${!state
      ? html`<p class="muted" aria-busy="true">Checking the console…</p>`
      : state.error
        ? html`<p class="outcome error" role="alert"><${Text} value=${state.error} /></p>`
        : state.password_set
          ? html`<${SignInForm} client=${client} onSignedIn=${onSignedIn} />`
          : html`<p>No password is set yet. Open the one-time setup link that <code>agentbreeder up</code> printed. ${LINK_HELP} The link brings you back to this screen.</p>`}
    <${PasswordFacts} />
  </main>`;
}

/**
 * First run, or a 3.0.0a2 console with no password (S-28): reached only with a session from the
 * one-time terminal link. Setting the password ends that session; the next step is sign-in.
 */
export function Setup({ client, onDone }) {
  const ids = useRef({ password: nextId("new"), again: nextId("again"), problem: nextId("problem"), rules: nextId("rules") }).current;
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const field = useRef(null);
  const h1 = useRef(null);
  useEffect(() => {
    h1.current?.focus();
  }, []);
  const submit = async (event) => {
    event.preventDefault();
    if (busy) return;
    const local = newPasswordProblem(password, again);
    if (local) {
      setProblem(local);
      field.current?.focus();
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      await client.post("/api/setup", { password });
      setPassword("");
      setAgain("");
      onDone();
    } catch (error) {
      if (error.kind === "session") return; // the page's phase changes
      setProblem(error.message);
      field.current?.focus();
    } finally {
      setBusy(false);
    }
  };
  return html`<main id="main" class="center-page">
    <h1 tabindex="-1" ref=${h1}>Set up the console</h1>
    <p>Choose the console password for <strong>${EMAIL}</strong>. You sign in with it from now on; the one-time terminal link keeps working too.</p>
    <form class="stack signin" onSubmit=${submit} noValidate>
      <label for=${ids.password}>New password</label>
      <input id=${ids.password} type="password" autocomplete="new-password" ref=${field} value=${password}
        aria-describedby=${problem ? `${ids.rules} ${ids.problem}` : ids.rules} aria-invalid=${problem ? "true" : "false"}
        onInput=${(event) => setPassword(event.currentTarget.value)} />
      <p class="muted small" id=${ids.rules}>At least ${MIN_LENGTH} characters. Common passwords are refused.</p>
      <label for=${ids.again}>Type it again</label>
      <input id=${ids.again} type="password" autocomplete="new-password" value=${again} onInput=${(event) => setAgain(event.currentTarget.value)} />
      ${problem ? html`<p class="outcome error" role="alert" id=${ids.problem}><${Text} value=${problem} /></p>` : null}
      <div class="row"><button type="submit" class="action" disabled=${busy}>Set password</button></div>
    </form>
    <p class="muted small">Headless? <code>agentbreeder up --set-password</code> does the same in a terminal.</p>
    <${PasswordFacts} />
  </main>`;
}

/**
 * The re-auth dialog (spec §8.2): the page stays as it was under it, so an editor's text, the
 * route and scroll survive a 401. It cannot be dismissed; only a new sign-in closes it.
 */
export function Reauth({ client, onSignedIn }) {
  const ref = useRef(null);
  const titleId = useRef(nextId("reauth")).current;
  const opener = useRef(document.activeElement);
  const [state, setState] = useState(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog.showModal();
    const onCancel = (event) => event.preventDefault();
    dialog.addEventListener("cancel", onCancel);
    client.get("/api/auth").then(
      (data) => setState(data || {}),
      () => setState({ password_set: true }),
    );
    return () => {
      dialog.removeEventListener("cancel", onCancel);
      if (dialog.open) dialog.close();
      const back = opener.current;
      requestAnimationFrame(() => {
        if (back && back.isConnected && !back.disabled && typeof back.focus === "function") back.focus();
      });
    };
  }, []);
  return html`<dialog ref=${ref} aria-labelledby=${titleId} class="confirm reauth glass-strong">
    <h2 id=${titleId}>Session ended</h2>
    <p>Sign in again to carry on. This page and anything you typed stay as they are.</p>
    ${!state
      ? html`<p class="muted" aria-busy="true">Checking the console…</p>`
      : state.password_set
        ? html`<${SignInForm} client=${client} onSignedIn=${onSignedIn} />`
        : html`<p>${LINK_HELP} Open it in this tab; your drafts are kept in this tab.</p>`}
  </dialog>`;
}
