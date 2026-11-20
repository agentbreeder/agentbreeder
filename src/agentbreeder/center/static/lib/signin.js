// Sign-in helpers (M2 #818). Pure functions: the screens in ui/signin.js and screens/settings.js
// own the DOM. The server is the authority on the password policy; these checks only save a
// round trip for the two mistakes a form can see (too short, the two fields differ).

export const MIN_LENGTH = 12;
export const EMAIL = "admin@localhost";
export const DRAFT_PREFIX = "console-draft:";

/** A problem with a new password the form can see, as a sentence, or null. */
export function newPasswordProblem(password, again) {
  if (!password) return "Enter a password.";
  if (password.length < MIN_LENGTH) return `Use at least ${MIN_LENGTH} characters.`;
  if (password !== again) return "The two passwords differ.";
  return null;
}

/** Seconds to wait from a refused sign-in (429 BACKOFF), or 0. */
export function backoffSeconds(error) {
  if (!error || error.code !== "BACKOFF") return 0;
  const seconds = Number(error.data && error.data.retry_after);
  return Number.isInteger(seconds) && seconds > 0 ? Math.min(seconds, 300) : 30;
}

const EVENTS = {
  "sign-in": "Sign-in with the password",
  link: "Sign-in with a one-time link",
  "password-set": "Password set",
  "password-change": "Password changed",
  "password-change-check": "Current password checked (Settings)",
  "password-reset": "Password reset",
  "sign-out": "Signed out of one tab",
  "sign-out-everywhere": "Signed out everywhere",
  "session-length": "Session length changed",
};
const OUTCOMES = { ok: "Succeeded", failed: "Wrong password", backoff: "Refused: too many attempts" };
const SOURCES = { browser: "Browser", terminal: "Terminal", "terminal-link": "Terminal link" };

/** A security event row as words: {event, outcome, source}. Unknown values show as they are. */
export function eventWords(row) {
  const pick = (table, value) => (Object.hasOwn(table, String(value)) ? table[value] : String(value ?? ""));
  return { event: pick(EVENTS, row.event), outcome: pick(OUTCOMES, row.outcome), source: pick(SOURCES, row.source) };
}

/** A Unix time in seconds as local date and time; "" when it is not a number. */
export function eventTime(at, locale) {
  const seconds = Number(at);
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  return new Date(seconds * 1000).toLocaleString(locale);
}

/** A session length choice in words. */
export function hoursLabel(hours) {
  return hours === 1 ? "1 hour" : `${hours} hours`;
}

/**
 * Editor drafts in per-tab storage (S-29). `storage` is {get, set} over sessionStorage. A draft is
 * read only by an editor, and editors mount only once this tab is signed in to the console it is
 * talking to, so a draft is never restored before sign-in succeeds.
 */
export function createDrafts(storage) {
  const key = (name) => `${DRAFT_PREFIX}${name}`;
  return {
    get: (name) => storage.get(key(name)),
    set: (name, value) => storage.set(key(name), value),
    clear: (name) => storage.set(key(name), null),
    /** Every draft (sign-out). `storage.keys()` lists the tab's keys, when it can. */
    clearAll() {
      for (const name of storage.keys ? storage.keys() : []) if (name.startsWith(DRAFT_PREFIX)) storage.set(name, null);
    },
  };
}
