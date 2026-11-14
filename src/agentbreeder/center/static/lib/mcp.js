// The MCP screen (#831, M15 console half) as pure functions: the project's MCP servers, what
// stack.lock pinned for each, each agent's grants (the `mcp` view: stack.yaml and stack.lock, read
// in process) and each relay's state and upstream drift (`services status --json`). Every value
// is untrusted text; the screen renders it through <Text>. Nothing here touches the DOM.
//
// Stable exports: RELAY_STATES, TOOL_STATES, serverRows, grantRows, repinCommand, shellWord,
// shortHash.

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

/** The terminal command that reviews changed tool definitions (#964 brings it to the console). */
export function repinCommand(path, environment) {
  const dir = text(path) ? shellWord(path) : "DIR";
  const env = text(environment) ? shellWord(environment) : "ENV";
  return `agentbreeder services up ${dir} --environment ${env} --repin`;
}

/**
 * One row per `mcp:` server: {name, ref, relay, origin, upstream, lockCurrent, relayState,
 * state, stateText, error, unpinned, tools: [{name, hash, state, stateText}]}. `files` is the
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
      origin: text(locked && locked.origin),
      upstream: text(locked && locked.upstream),
      lockCurrent: server.lock_current === true,
      relayState: relay ? text(relay.state) || "unknown" : "",
      state,
      stateText: state ? RELAY_STATES[state] : "",
      error: text(live && live.error),
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
