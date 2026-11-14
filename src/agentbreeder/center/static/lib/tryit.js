// Try it (#823, M7): the conversation model and the inspector's words. Pure functions: nothing
// here touches the DOM or the network.
//
// A conversation lives only in this tab's memory (S-12a): never in localStorage, never on the
// server. Each turn is {id, message, state: "pending" | "reply" | "failed", turn?, problem?}.
// Replies stay text (S-12b): nothing here builds a link from agent output.

export const MAX_MESSAGE = 4000; // `invoke.MAX_MESSAGE`; the server checks it again
export const MAX_TURNS = 50; // turns kept per conversation; the oldest go first
const ID = /^[A-Za-z0-9_-]{1,64}$/; // the runtime's session and customer rule

/** A new, empty conversation. */
export function emptyConversation() {
  return { turns: [], session: null, nextId: 1 };
}

/**
 * The production row of `name` among `list --json` rows, or null (candidates never match). A name
 * can be active on two targets at once; the local row is the one `invoke` calls, so it wins.
 */
export function productionRow(rows, name) {
  if (!Array.isArray(rows) || typeof name !== "string" || name.endsWith("-candidate")) return null;
  const named = rows.filter((row) => row && row.name === name);
  return named.find((row) => row.target === "local") || named[0] || null;
}

/**
 * The envelope for a request that threw (`ApiError`). A 4xx is the console refusing the request
 * before any child ran (a bad message, TRY_BUSY, an unknown project): not sent. No answer at all,
 * or a 5xx, leaves it unknown, which counts as sent.
 */
export function thrownEnvelope(error) {
  const refused = error && error.kind === "http" && error.status >= 400 && error.status < 500;
  return {
    data: refused ? { sent: false } : null,
    error: { code: (error && error.code) || "UNREACHABLE", message: (error && error.message) || "The console did not answer." },
  };
}

/**
 * Why `row` cannot take a message, as a sentence, or null when it can. Only a ready local
 * deployment is called (`invoke` refuses the rest the same way).
 */
export function unavailable(row, name) {
  if (!row) return `${name} has no production deployment in this project. Release it first.`;
  if (row.target !== "local") return `${name} runs on ${row.target}. Try it calls local deployments only; open a port with agentbreeder forward in a terminal.`;
  if (row.state !== "ready") return `${name} is ${row.state}, not ready.`;
  return null;
}

/** Can this Deployments row offer Try it? Production, local and ready. */
export function canTry(row) {
  return Boolean(row && typeof row.name === "string" && !row.name.endsWith("-candidate") && row.target === "local" && row.state === "ready");
}

/** Why a typed message cannot be sent, or null. */
export function messageProblem(text) {
  const message = (text || "").trim();
  if (!message) return "Type a message first.";
  if (message.length > MAX_MESSAGE) return `Messages are limited to ${MAX_MESSAGE} characters (this one has ${message.length}).`;
  return null;
}

/** Why a typed customer ID is refused, or null (empty is allowed: no customer sent). */
export function customerProblem(text) {
  const value = (text || "").trim();
  if (!value || ID.test(value)) return null;
  return "A customer ID is 1 to 64 letters, digits, _ and -.";
}

/** The conversation with a pending turn for `message` appended; returns [conversation, id]. */
export function addPending(conversation, message) {
  const id = conversation.nextId;
  const turns = [...conversation.turns, { id, message, state: "pending" }].slice(-MAX_TURNS);
  return [{ ...conversation, turns, nextId: id + 1 }, id];
}

/**
 * The conversation with turn `id` settled from the server's envelope `{data, error}`. A reply's
 * session id continues the conversation; a failure keeps the session as it was.
 */
export function settle(conversation, id, envelope) {
  const error = envelope && envelope.error;
  const data = (envelope && envelope.data) || null;
  const turns = conversation.turns.map((turn) => {
    if (turn.id !== id) return turn;
    if (error || !data || typeof data.reply !== "string") {
      return { ...turn, state: "failed", problem: problemOf(error, data) };
    }
    return { ...turn, state: "reply", turn: data };
  });
  const session = !error && data && typeof data.session === "string" && ID.test(data.session) ? data.session : conversation.session;
  return { ...conversation, turns, session };
}

function problemOf(error, data) {
  const code = (error && error.code) || "TRY_INVALID";
  const message = (error && error.message) || "The answer does not match its contract.";
  // `sent` false means nothing reached the agent; true or unknown means it may have run the turn.
  const sent = !(data && data.sent === false);
  return { code, message, sent };
}

/** A failed turn's explanation in words. */
export function failureText(problem) {
  const why = `${problem.message} [${problem.code}]`;
  return problem.sent
    ? `No usable reply: ${why}. The agent may have run this turn; sending it again is a second turn.`
    : `Not sent: ${why}.`;
}

/** Is any turn still waiting? (One message in flight per tab.) */
export function pending(conversation) {
  return conversation.turns.some((turn) => turn.state === "pending");
}

/** Latency in words: "850 ms", "2.4 s", "1 min 5 s". */
export function latencyText(ms) {
  if (!Number.isFinite(ms) || ms < 0) return "unknown";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

const count = (value) => (Number.isInteger(value) && value >= 0 ? value.toLocaleString("en-US") : null);

/** Tokens in words: "812 in, 64 out"; a side the runtime did not report says so. */
export function tokensText(tokens) {
  const prompt = count(tokens && tokens.prompt);
  const output = count(tokens && tokens.output);
  if (prompt === null && output === null) return "not reported";
  return `${prompt ?? "?"} in, ${output ?? "?"} out`;
}

/** A list of tool names or source titles in words; empty says "none". */
export function namesText(names) {
  return Array.isArray(names) && names.length ? names.join(", ") : "none";
}
