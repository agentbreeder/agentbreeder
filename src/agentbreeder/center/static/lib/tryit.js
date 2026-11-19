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

// A failed model call's code (runtime/adk@1.3.0; MODEL_BLOCKED from 1.4.0; MODEL_STEP_LIMIT from a
// kit runtime, agent protocol "Profiles"): `tryit.MODEL_CODES`.
export const MODEL_CODES = ["MODEL_BLOCKED", "MODEL_BUDGET_EXCEEDED", "MODEL_RATE_LIMITED", "MODEL_ACCESS_DENIED", "MODEL_REQUEST_REFUSED", "MODEL_UNAVAILABLE", "MODEL_STEP_LIMIT"];
const HEX32 = /^[0-9a-f]{32}$/;
// The codes whose next step is to send the message again (the others say it won't help).
const RETRYABLE = ["MODEL_RATE_LIMITED", "MODEL_UNAVAILABLE"];

function problemOf(error, data) {
  const code = (error && error.code) || "TRY_INVALID";
  const message = (error && error.message) || "The answer does not match its contract.";
  // `sent` false means nothing reached the agent; true or unknown means it may have run the turn.
  const sent = !(data && data.sent === false);
  const problem = { code, message, sent };
  // A failed model call (#1062): the turn id the agent's log line carries, and a rate limit's wait.
  if (MODEL_CODES.includes(code) && data) {
    if (typeof data.correlation_id === "string" && HEX32.test(data.correlation_id)) problem.correlation = data.correlation_id;
    if (Number.isInteger(data.retry_after_s) && data.retry_after_s > 0) problem.retryAfter = data.retry_after_s;
  }
  return problem;
}

/** A failed turn's explanation in words. A failed model call's message is the runtime's, with
 * its next step; the rest says whether the turn may have run. */
export function failureText(problem) {
  const why = `${problem.message} [${problem.code}]`;
  if (problem.code === "MODEL_STEP_LIMIT") {
    // Not a failed call: the agent made as many model calls as its limit allows and the model was
    // still asking for tools (#1085 S2). Tools ran on the way, so sending it again is a second turn.
    return `The agent reached its limit on model calls for one turn while the model still asked for tools, so there is no reply: ${why}. Tools ran in this turn, so sending it again is a second turn; a shorter request may fit in the limit.`;
  }
  if (MODEL_CODES.includes(problem.code)) {
    // The runtime's 1.3.0 message already names the wait ("retry in 7 s"); say it only if not.
    const named = Number.isInteger(problem.retryAfter) && problem.message.includes(`retry in ${problem.retryAfter} s`);
    const wait = Number.isInteger(problem.retryAfter) && !named ? ` The gateway asks to wait ${problem.retryAfter} s before sending again.` : "";
    // A turn can make several model calls with tool calls between them, so a failure after the
    // first may come after tools ran: for the codes whose next step is to send again, say so.
    const again = RETRYABLE.includes(problem.code) ? " Tools may already have run in this turn, so sending it again is a second turn." : "";
    return `The model call failed, so there is no reply: ${why}.${wait}${again}`;
  }
  return problem.sent
    ? `No usable reply: ${why}. The agent may have run this turn; sending it again is a second turn.`
    : `Not sent: ${why}.`;
}

/** Where a failed model call's detail is, in words, or null without a correlation id. */
export function correlationText(problem) {
  if (!problem || typeof problem.correlation !== "string") return null;
  return "The agent's log has the detail under this correlation ID: open the agent's Logs tab, or run agentbreeder logs for its deployment in a terminal.";
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

/** Tokens in words: "812 in, 64 out", or "812 in (600 cached), 64 out" when the runtime reported
 * cached prompt tokens (runtime/adk@1.4.0); a side the runtime did not report says so. */
export function tokensText(tokens) {
  const prompt = count(tokens && tokens.prompt);
  const output = count(tokens && tokens.output);
  if (prompt === null && output === null) return "not reported";
  const cached = count(tokens && tokens.cached);
  const part = cached !== null && tokens.cached > 0 ? ` (${cached} cached)` : "";
  return `${prompt ?? "?"} in${part}, ${output ?? "?"} out`;
}

const strings = (value) => (Array.isArray(value) ? value.filter((item) => typeof item === "string" && item) : []);

/** Which gateway fallback served the turn (runtime/adk@1.3.0), in words, or null when none did. */
export function fallbackText(fallbacks, servedBy) {
  if (!Number.isInteger(fallbacks) || fallbacks <= 0) return null;
  const aliases = strings(servedBy);
  return `A gateway fallback served this turn${aliases.length ? ` (served by ${aliases.join(", ")})` : ""}.`;
}

/** Tool names the model asked for that the agent does not have (runtime/adk@1.4.0), or null. */
export function unknownToolsText(names) {
  const shown = strings(names);
  if (!shown.length) return null;
  return `The model asked for ${shown.length === 1 ? "a tool" : "tools"} this agent does not have (${shown.join(", ")}); the runtime refused ${shown.length === 1 ? "it, so it is not a tool call" : "them, so they are not tool calls"}.`;
}

/** A gateway guardrail block (runtime/adk@1.4.0, from the trace's root span), in words, or null. */
export function guardrailText(guardrail) {
  if (!guardrail || guardrail.action !== "blocked") return null;
  const name = typeof guardrail.name === "string" && guardrail.name ? `guardrail ${guardrail.name}` : "a guardrail";
  const entity = typeof guardrail.entity === "string" && guardrail.entity ? ` (${guardrail.entity})` : "";
  return `Blocked by ${name}${entity}.`;
}

/** A trace's root-span facts in words (fallback, unknown tools, guardrail), in that order. */
export function traceFlags(trace) {
  return [fallbackText(trace && trace.fallbacks, trace && trace.served_by), unknownToolsText(trace && trace.unknown_tools), guardrailText(trace && trace.guardrail)].filter(Boolean);
}

/** A trace's tokens with the cached part ("321 in (300 cached), 70 out"), or null when none. */
export function traceTokensText(trace) {
  const tokens = (trace && trace.tokens) || {};
  return spanTokensText({ prompt: tokens.prompt, completion: tokens.completion, cached: trace && trace.cached_prompt_tokens });
}

/** A turn's cost (USD, LiteLLM's estimate) in words: "$0.000444", "$0.0444"; null when the
 * runtime did not report it (runtimes before adk 1.2.0, or a call with no cost header). */
export function costText(usd) {
  if (typeof usd !== "number" || !Number.isFinite(usd) || usd < 0) return null;
  if (usd === 0) return "$0";
  if (usd < 0.000001) return "under $0.000001";
  return `$${usd.toFixed(usd < 0.01 ? 6 : 4)}`;
}

/** A list of tool names or source titles in words; empty says "none". */
export function namesText(names) {
  return Array.isArray(names) && names.length ? names.join(", ") : "none";
}

// -- a turn's trace (#907) ------------------------------------------------------------------------

const TRACE_ID = /^[0-9a-f]{32}$/;

/** The read path of a turn's trace, or null when the turn has no usable trace id. */
export function tracePath(projectId, traceId) {
  return typeof traceId === "string" && TRACE_ID.test(traceId) ? `/api/projects/${projectId}/traces/${traceId}` : null;
}

/**
 * The span tree from `traces`' flat list (in start order): `[{span, children}]`. A span whose
 * parent is not in the list is a root; a parent link that would form a cycle is ignored, so every
 * span appears exactly once.
 */
export function spanTree(spans) {
  const list = Array.isArray(spans) ? spans.filter((span) => span && typeof span.span_id === "string") : [];
  const nodes = new Map();
  for (const span of list) if (!nodes.has(span.span_id)) nodes.set(span.span_id, { span, children: [] });
  const roots = [];
  for (const node of nodes.values()) {
    const parent = node.span.parent_id;
    // Walk up from the parent: reaching this node again means a cycle.
    let cycle = false;
    for (let at = parent, steps = 0; at && nodes.has(at) && steps <= nodes.size; steps += 1) {
      if (at === node.span.span_id) {
        cycle = true;
        break;
      }
      at = nodes.get(at).span.parent_id;
    }
    if (parent && nodes.has(parent) && !cycle) nodes.get(parent).children.push(node);
    else roots.push(node);
  }
  return roots;
}

/** Nesting stops at this depth: deeper spans are listed, in order, under the span at it. */
export const MAX_SPAN_DEPTH = 8;

/** Every span under `node`, depth first, as nodes with no children (iterative: no deep recursion). */
export function descendants(node) {
  const out = [];
  const stack = [...node.children].reverse();
  while (stack.length) {
    const next = stack.pop();
    out.push({ span: next.span, children: [] });
    for (let i = next.children.length - 1; i >= 0; i -= 1) stack.push(next.children[i]);
  }
  return out;
}

/** A span's tokens in words ("321 in, 70 out"; with `cached`, "321 in (300 cached), 70 out"),
 * or null when it reported none. */
export function spanTokensText(tokens) {
  const prompt = count(tokens && tokens.prompt);
  const completion = count(tokens && tokens.completion);
  if (prompt === null && completion === null) return null;
  const cached = count(tokens && tokens.cached);
  const part = cached !== null && tokens.cached > 0 ? ` (${cached} cached)` : "";
  return `${prompt ?? "?"} in${part}, ${completion ?? "?"} out`;
}

/** One span's facts after its name: kind, status, latency, model, tool and tokens. */
export function spanFacts(span) {
  const parts = [typeof span.kind === "string" ? span.kind.toLowerCase() : "unknown"];
  if (span.status === "error") parts.push("error");
  if (Number.isFinite(span.latency_ms)) parts.push(latencyText(span.latency_ms));
  if (typeof span.model === "string" && span.model) parts.push(`model ${span.model}`);
  if (typeof span.tool === "string" && span.tool && span.tool !== span.name) parts.push(`tool ${span.tool}`);
  const tokens = spanTokensText(span.tokens);
  // #1009: a model call's counts can sit on nested spans; only the innermost is in the total.
  if (tokens) parts.push(span.tokens_counted === false ? `${tokens} tokens, counted in a span under it` : `${tokens} tokens`);
  return parts.join(" · ");
}

/** The trace in one line: status, latency, spans, models, tokens and tool calls. */
export function traceSummary(trace) {
  const spans = Array.isArray(trace.spans) ? trace.spans.length : 0;
  const parts = [trace.status === "error" ? "Error" : trace.status === "ok" ? "OK" : "Status unknown"];
  if (Number.isFinite(trace.latency_ms)) parts.push(latencyText(trace.latency_ms));
  parts.push(`${spans} span${spans === 1 ? "" : "s"}${trace.incomplete ? " so far" : ""}${trace.truncated ? " (Phoenix had more; the rest are not shown)" : ""}`);
  const models = Array.isArray(trace.models) ? trace.models.filter((m) => typeof m === "string") : [];
  if (models.length) parts.push(`model${models.length === 1 ? "" : "s"} ${models.join(", ")}`);
  const total = count(trace.tokens && trace.tokens.total);
  const cached = count(trace.cached_prompt_tokens);
  if (total !== null) parts.push(`${total} tokens${cached !== null && trace.cached_prompt_tokens > 0 ? ` (${cached} cached)` : ""}`);
  const tools = Array.isArray(trace.tool_calls) ? trace.tool_calls.length : 0;
  parts.push(`${tools} tool call${tools === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

/** The words for a trace Phoenix has only part of: its root span is not in yet (#1010). */
export const ARRIVING_TEXT =
  "Still arriving: Phoenix does not have this trace's root span yet, so its spans and tokens are partial and which deployment ran it is not known yet. Read it again in a few seconds.";

/** Which deployment the trace was attributed to, in words. */
export function attributionText(trace) {
  // A trace with no root yet is never "matched by time": the root may carry the deployment.
  if (trace.incomplete && trace.attribution !== "exact") return ARRIVING_TEXT;
  if (trace.attribution === "exact") return "Recorded by the agent's runtime for this deployment.";
  if (trace.attribution === "window") return "Matched by time: this runtime does not record its deployment on its traces.";
  return "Not attributed to a deployment.";
}

/** A failed trace read, in words; a trace Phoenix does not have yet says to try again. */
export function traceProblemText(problem) {
  const code = (problem && problem.code) || "TRACE_FAILED";
  const message = (problem && problem.message) || "The trace could not be read";
  if (code === "TRACES_NOT_FOUND") return `Phoenix does not have this trace yet; it can take a few seconds to arrive. [${code}]`;
  if (code === "TRACES_NO_PHOENIX") return `This stack does not run Phoenix (service/phoenix), so there are no traces. [${code}]`;
  return `${message} [${code}]`;
}
