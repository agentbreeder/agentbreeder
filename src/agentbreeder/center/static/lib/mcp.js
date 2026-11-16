// The MCP screen (#831, M15 console half) as pure functions: the project's MCP servers, what
// stack.lock pinned for each, each agent's grants (the `mcp` view: stack.yaml and stack.lock, read
// in process) and each relay's state and upstream drift (`services status --json`). Every value
// is untrusted text; the screen renders it through <Text>. Nothing here touches the DOM.
//
// Stable exports: RELAY_STATES, TOOL_STATES, serverRows, grantRows, relayLogsCommand, repinCommand, shellWord,
// shortHash; for grant and revoke (part 2): grantProblem, grantWriteProblem, grantChoices, choiceNote, grantedTools,
// grantSteps; for the repin review (#964): pinsCommand, pinsProblem, repinServers.

/** `services status --json` `mcp.<server>.state`, in words. */
export const RELAY_STATES = {
  ok: "Pins match upstream",
  drift: "Upstream changed",
  unverified: "Could not check upstream",
  absent: "Relay not running",
};

/** A pinned tool's state against what the relay last saw upstream. */
export const TOOL_STATES = {
  pinned: "Pinned",
  changed: "Changed upstream; hidden until repinned",
  missing: "Missing upstream; the relay refuses it",
  unknown: "Pinned (upstream not checked)",
};

import { CAUSE_WORDS } from "./format.js";

const CODE = /^[A-Z][A-Z0-9_]{2,39}$/;
const text = (value) => (typeof value === "string" ? value : "");
const names = (value) => (Array.isArray(value) ? value.filter((item) => typeof item === "string") : []);
const object = (value) => (value && typeof value === "object" && !Array.isArray(value) ? value : null);
/** An own property only: untrusted keys never reach Object.prototype. */
const own = (record, key) => (record && Object.hasOwn(record, key) ? record[key] : undefined);

/** `sha256:` plus 16 hex digits, as the CLI's grant text shows a pin. */
export function shortHash(digest) {
  const value = text(digest);
  return value.startsWith("sha256:") ? value.slice(7, 23) : "";
}

/** One shell word: as it is when plainly safe, else in single quotes. */
export function shellWord(value) {
  const word = text(value);
  if (/^[A-Za-z0-9_./:@%+=,-]+$/.test(word)) return word;
  return `'${word.replaceAll("'", "'\\''")}'`;
}

/** The terminal read of one relay's log (#1042), as the CLI's probe failure names it. */
export function relayLogsCommand(path, relay) {
  return `agentbreeder services logs ${text(path) ? shellWord(path) : "DIR"} ${shellWord(relay)}`;
}

/** The terminal command that reviews changed tool definitions (#964 brings it to the console). */
export function repinCommand(path, environment) {
  const dir = text(path) ? shellWord(path) : "DIR";
  const env = text(environment) ? shellWord(environment) : "ENV";
  return `agentbreeder services up ${dir} --environment ${env} --repin`;
}

/**
 * One row per `mcp:` server: {name, ref, relay, origin, upstream, lockCurrent, relayState,
 * state, stateText, error, cause, causeText, unpinned, tools: [{name, hash, state, stateText}]}. `files` is the
 * `mcp` view's data, `status` the `services` view's data (either may be null).
 */
export function serverRows(files, status) {
  const servers = Array.isArray(files && files.servers) ? files.servers : [];
  const services = object(status && status.services) || {};
  const drift = object(status && status.mcp) || {};
  return servers.filter(object).map((server) => {
    const name = text(server.name);
    const locked = object(server.locked);
    const relay = object(own(services, text(server.relay)));
    const live = object(own(drift, name));
    // Only a known state is shown; without an `mcp` entry, "absent" only when the relay is.
    const state = live && Object.hasOwn(RELAY_STATES, live.state) ? live.state : !live && relay && relay.state === "absent" ? "absent" : "";
    const changed = new Set(names(live && live.changed));
    const missing = new Set(names(live && live.missing));
    const checked = state === "ok" || state === "drift";
    const pins = object(server.pins) || {};
    const tools = Object.keys(pins).sort().map((tool) => {
      const toolState = changed.has(tool) ? "changed" : missing.has(tool) ? "missing" : checked ? "pinned" : "unknown";
      return { name: tool, hash: shortHash(pins[tool]), state: toolState, stateText: TOOL_STATES[toolState] };
    });
    return {
      name,
      ref: text(server.ref),
      relay: text(server.relay),
      // The console's log route takes service names of at most 31 characters (`SERVICE`): a
      // longer relay name (a server name of 28+) is read with `services logs` in a terminal.
      relayLogs: /^[a-z][a-z0-9-]{0,30}$/.test(text(server.relay)),
      origin: text(locked && locked.origin),
      upstream: text(locked && locked.upstream),
      lockCurrent: server.lock_current === true,
      relayState: relay ? text(relay.state) || "unknown" : "",
      state,
      stateText: state ? RELAY_STATES[state] : "",
      error: text(live && live.error),
      // #1042: why the relay's upstream check failed (`services status --json` `cause`), its
      // meaning in fixed words, and the terminal read of the relay's log.
      cause: CODE.test(text(live && live.cause)) ? live.cause : "",
      causeText: CODE.test(text(live && live.cause)) && Object.hasOwn(CAUSE_WORDS, live.cause) ? CAUSE_WORDS[live.cause] : "",
      unpinned: names(live && live.unpinned),
      tools,
    };
  });
}

/**
 * One row per agent and granted server: {agent, server, tools: [{name, pinned}]}. An agent
 * without grants gets one row with server "" and no tools, so the table still lists it.
 */
export function grantRows(files) {
  const agents = Array.isArray(files && files.agents) ? files.agents : [];
  const pinned = new Map();
  for (const server of Array.isArray(files && files.servers) ? files.servers : []) {
    if (object(server)) pinned.set(text(server.name), new Set(Object.keys(object(server.pins) || {})));
  }
  const rows = [];
  for (const agent of agents.filter(object)) {
    const grants = object(agent.grants) || {};
    const servers = Object.keys(grants).sort();
    if (!servers.length) rows.push({ agent: text(agent.name), server: "", tools: [] });
    for (const server of servers) {
      const pins = pinned.get(server) || new Set();
      rows.push({
        agent: text(agent.name),
        server,
        tools: names(grants[server]).map((name) => ({ name, pinned: pins.has(name) })),
      });
    }
  }
  return rows;
}

// -- grant and revoke (`mcp grant|revoke --json`, #831 part 2) -------------------------------------

/** Refusals a person can act on, in fixed words (others show the CLI's own message). */
const GRANT_WORDS = {
  MCP_AGENT_UNKNOWN: "stack.yaml has no such agent under environment.agents. Add the agent (with its sponsor and review date) by hand first.",
  MCP_UNKNOWN: "stack.yaml has no such MCP server under mcp:.",
  MCP_TOOL_UNDECLARED: "The server's version doesn't declare that tool, so it can't be granted.",
  MCP_TOOL_INVALID: "That is not a valid tool name.",
  STACK_EDIT_AMBIGUOUS: "The grant can't be rewritten without changing more of stack.yaml. Edit stack.yaml by hand.",
  STACK_TOO_LARGE: "The change would take stack.yaml over the 64 KiB limit every command reads. Shorten it first.",
  BASE_MISMATCH: "stack.yaml changed after your review. Review the change again.",
  WRITE_BUSY: null, // the server's own message says what holds it
};

/** A grant or revoke refusal as {code, text, review}: `review` when it must be reviewed again. */
export function grantProblem(problem) {
  if (!problem) return null;
  const code = typeof problem.code === "string" ? problem.code : null;
  const words = code && Object.hasOwn(GRANT_WORDS, code) ? GRANT_WORDS[code] : null;
  return { code, text: words || text(problem.message) || "The request failed.", review: code === "BASE_MISMATCH" };
}

/** Other refusals `mcp grant|revoke` makes before it writes (mcpgrant.change). */
const BEFORE_WRITE = ["STACK_MISSING", "STACK_INVALID", "ENTRY_UNSAFE", "DIGEST_INVALID", "PROJECT_UNSAFE"];

/**
 * A write's problem: `uncertain` unless it is a refusal the CLI makes before writing (the codes
 * above); a timeout or an answer that fails its check may come after stack.yaml changed.
 */
export function grantWriteProblem(problem) {
  const shown = grantProblem(problem);
  if (!shown) return null;
  const known = shown.code !== null && (Object.hasOwn(GRANT_WORDS, shown.code) || BEFORE_WRITE.includes(shown.code));
  if (known) return { ...shown, uncertain: false };
  return { ...shown, uncertain: true, text: `${shown.text} The write may have happened: check the grants above (or git diff stack.yaml) before trying again.` };
}

/**
 * The tools a form offers, each {name, effect, declared}: to grant, the declared tools not granted
 * yet; to revoke, the granted tools (also one a newer version no longer declares). `declared` is
 * [{name, effect}] or null (not read yet, or unreadable: nothing to offer for a grant, and a
 * revoke's `declared` is null, unknown, rather than false, #1043).
 */
export function grantChoices(verb, declared, granted) {
  const held = names(granted);
  const known = Array.isArray(declared);
  if (verb === "revoke") {
    return held.map((name) => {
      const hit = known ? declared.find((tool) => object(tool) && tool.name === name) : null;
      return { name, effect: hit ? text(hit.effect) : "", declared: known ? Boolean(hit) : null };
    });
  }
  return (known ? declared : [])
    .filter((tool) => object(tool) && typeof tool.name === "string" && !held.includes(tool.name))
    .map((tool) => ({ name: tool.name, effect: text(tool.effect), declared: true }));
}

/**
 * The note beside an offered tool: its effect; for a revoke, "no longer declared" only once the
 * declared tools were read and lack it, else a loading or unreadable note (#1043).
 */
export function choiceNote(tool, loading) {
  if (!object(tool)) return "";
  if (tool.declared === false) return "no longer declared";
  if (tool.declared === null) return loading ? "checking the declared tools…" : "declared tools unavailable";
  return text(tool.effect);
}

/** One agent's current grant on one server, from the `mcp` view's data. */
export function grantedTools(files, agent, server) {
  const row = (Array.isArray(files && files.agents) ? files.agents : []).find((item) => object(item) && item.name === agent);
  return names(own(object(row && row.grants), server));
}

/**
 * What follows a reviewed or written change, as sentences and commands: a change reaches the relay
 * at the next `services up`. A revoke still held by an active deployment applies there too
 * (#1072): the relay refuses the tool from then on (a sentence, no command). An unreadable grant
 * file blocks `services up` until it is reconciled.
 */
export function grantSteps(summary, path, environment) {
  const dir = text(path) ? shellWord(path) : "DIR";
  const env = text(environment) ? shellWord(environment) : "ENV";
  const steps = [];
  for (const item of Array.isArray(summary && summary.in_use) ? summary.in_use : []) {
    if (!object(item)) continue;
    if (typeof item.deployment === "string") {
      steps.push({
        text: `Deployment ${item.deployment} (${text(item.environment)}) still holds ${names(item.tools).join(", ")}. The next services up takes it away: the relay refuses it from then on (TOOL_NOT_GRANTED). Release the agent without it, or it can't be deployed again.`,
        command: null,
      });
    } else {
      steps.push({
        text: `An unreadable grant file in ${text(item.environment)} blocks services up until it is cleared:`,
        command: `agentbreeder reconcile --stack ${dir} --environment ${shellWord(item.environment)}`,
      });
    }
  }
  steps.push({ text: "The relay gets the change at the next services up:", command: `agentbreeder services up ${dir} --environment ${env}` });
  return steps;
}

// -- starting services for a stack with MCP servers (#831 part 3) ---------------------------------

/**
 * The MCP servers of a `services plan` answer (`grant.mcp`), each {server, ref, origin, upstream,
 * credentials: [{variable, destination}], builtIn, sendsCredential}: what the services review
 * shows. `sendsCredential` mirrors the server's check (a remote upstream, auth other than none).
 */
export function planServers(plan) {
  const inner = object(plan && plan.grant);
  const servers = Array.isArray(inner && inner.mcp) ? inner.mcp : [];
  return servers.filter(object).map((item) => ({
    server: text(item.server),
    ref: text(item.ref),
    origin: text(item.origin),
    upstream: text(item.upstream),
    credentials: (Array.isArray(item.credentials) ? item.credentials : []).filter(object).map((c) => ({ variable: text(c.variable), destination: text(c.destination) })),
    builtIn: item.origin === "built-in",
    sendsCredential: item.egress === "yes" && item.auth !== "none",
    // oauth_refresh: the refresh credential goes to the token host (`credentials`), and the access
    // token it mints goes to the upstream; consent names both.
    accessTokenTo: item.auth === "oauth_refresh" ? text(item.upstream) : "",
  }));
}

/** The names a start sends as `allow_project_mcp`: the ticked servers that are not built in. */
export function consentNames(servers, ticked) {
  const chosen = ticked instanceof Set ? ticked : new Set();
  return servers.filter((row) => !row.builtIn && chosen.has(row.server)).map((row) => row.server).sort();
}

/** Why a start is not ready yet: the first server that is not built in and not ticked, or "". */
export function consentMissing(servers, ticked) {
  const chosen = ticked instanceof Set ? ticked : new Set();
  const row = servers.find((item) => !item.builtIn && !chosen.has(item.server));
  return row ? `Allow mcp.${row.server} (${row.origin} code) first.` : "";
}

// -- the repin review (`services pins --json`, checked and rendered by the server, #964) ----------

/** The terminal read that shows the same review (`services pins`). */
export function pinsCommand(path) {
  return `agentbreeder services pins ${text(path) ? shellWord(path) : "DIR"}`;
}

/** Refusals of the review read a person can act on, in fixed words (others: the CLI's message). */
const PINS_WORDS = {
  MCP_PINS_NEED_UP: "A server has tools to pin for the first time (or a changed version). Start services first, then review the repin.",
  MCP_RELAY_NOT_RUNNING: "A relay of this stack is not running. Start services first, then review the repin.",
  PINS_REVIEW_STALE: "This repin review is no longer current. Review it again.",
};

/** A review read's problem as text. */
export function pinsProblem(problem) {
  if (!problem) return "";
  const code = typeof problem.code === "string" ? problem.code : "";
  const words = code && Object.hasOwn(PINS_WORDS, code) ? PINS_WORDS[code] : "";
  return words || text(problem.message) || "The review could not be read.";
}

/**
 * The server's checked review as rows to show: {name, origin, builtIn, state, tools: [{name,
 * heading, text}], drop, missing}. The server rendered every line; nothing here parses or
 * re-serializes a definition.
 */
export function repinServers(summary) {
  const servers = Array.isArray(summary && summary.servers) ? summary.servers : [];
  return servers.filter(object).map((server) => ({
    name: text(server.name),
    origin: text(server.origin),
    builtIn: server.built_in === true,
    state: text(server.state),
    tools: (Array.isArray(server.tools) ? server.tools : []).filter(object).map((tool) => ({
      name: text(tool.name),
      heading: tool.change === "changed"
        ? `changed ${text(tool.name)}  ${text(tool.pinned)} -> ${text(tool.sha256)}`
        : `new ${text(tool.name)}  ${text(tool.sha256)}`,
      text: names(tool.lines).join("\n"),
    })),
    drop: names(server.drop),
    missing: names(server.missing),
  }));
}

/** The servers `services status` reports with changed or missing pinned tools. */
export function driftedServers(status) {
  const drift = object(status && status.mcp) || {};
  return Object.keys(drift).filter((name) => object(drift[name]) && drift[name].state === "drift").sort();
}
