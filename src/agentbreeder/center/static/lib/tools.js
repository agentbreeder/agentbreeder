// A tool version's detail (#828, M12 console half), as pure functions over the `registry show
// --json` answer: its functions (from tool.yaml, with the Python signature the CLI read with `ast`;
// nothing is imported) and the wording around them. Every value from tool.yaml is untrusted text;
// the screen renders it through <Text>. Nothing here touches the DOM.
//
// Stable exports: SIGNATURE_REASONS, signatureText, parameterRows, functionRows; and for the
// execution log (#828 part 2): LOG_WINDOWS, LOG_LIMITS, MAX_AGENTS, logPath, logRefusal, logRows,
// seenAgents, argsText.

/** Why `registry show` gave no signature (`signature_error`). */
export const SIGNATURE_REASONS = {
  too_large: "the module is over the size the CLI reads",
  syntax_error: "the module does not parse",
  not_found: "the module or the function was not found",
};

const text = (value) => (typeof value === "string" ? value : "");

/** The signature, or why there is none. */
export function signatureText(entry) {
  if (typeof entry.signature === "string" && entry.signature) return { signature: entry.signature, why: "" };
  const code = text(entry.signature_error);
  return { signature: "", why: `Signature not shown: ${SIGNATURE_REASONS[code] || code || "unknown reason"}.` };
}

/** tool.yaml's `parameters` (what the model sees) as rows: {name, type, required, description}. */
export function parameterRows(parameters) {
  const properties = parameters && typeof parameters === "object" && !Array.isArray(parameters) ? parameters.properties : null;
  if (!properties || typeof properties !== "object" || Array.isArray(properties)) return [];
  const required = Array.isArray(parameters.required) ? parameters.required.filter((name) => typeof name === "string") : [];
  return Object.entries(properties).map(([name, spec]) => {
    const one = spec && typeof spec === "object" && !Array.isArray(spec) ? spec : {};
    const type = Array.isArray(one.type) ? one.type.filter((t) => typeof t === "string").join(" | ") : text(one.type);
    return { name, type: type || "any", required: required.includes(name), description: text(one.description) };
  });
}

/**
 * The functions of a `registry show` answer: {error, rows} for a tool version, null for any other
 * kind. `error` is set when tool.yaml could not be read (`functions_error`).
 */
export function functionRows(data) {
  if (!data || data.kind !== "tool") return null;
  if (typeof data.functions_error === "string" && data.functions_error) return { error: data.functions_error, rows: [] };
  const rows = (Array.isArray(data.functions) ? data.functions : [])
    .filter((entry) => entry && typeof entry === "object")
    .map((entry, index) => ({
      key: `${index} ${text(entry.name)}`,
      name: text(entry.name),
      module: text(entry.module),
      function: text(entry.function),
      description: text(entry.description),
      ...signatureText(entry),
      parameters: parameterRows(entry.parameters),
    }));
  return { error: "", rows };
}

// -- the execution log (`tool log --json`, #828 part 2) ------------------------------------------

/** The windows offered (`--since`): the CLI reads at most 720 hours back. */
export const LOG_WINDOWS = [
  { value: "1h", label: "Last hour" },
  { value: "24h", label: "Last 24 hours" },
  { value: "168h", label: "Last 7 days" },
  { value: "720h", label: "Last 30 days" },
];
export const LOG_LIMITS = [50, 100, 500, 1000];

const TOOL_REF = /^tool\/[a-z0-9][a-z0-9-]{0,62}(@(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5}))?$/;
const AGENT = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

/**
 * The API path of one log read, or null for a ref or agent the server would refuse. `content`
 * asks for arguments and error text (customer data); it is never put in the page's own address.
 */
export function logPath(projectId, { ref, since = "24h", agent = "", limit = 100, content = false } = {}) {
  if (typeof ref !== "string" || !TOOL_REF.test(ref)) return null;
  if (agent && !AGENT.test(agent)) return null;
  const query = new URLSearchParams({ ref, since, limit: String(limit) });
  if (agent) query.set("agent", agent);
  if (content) query.set("content", "1");
  return `/api/projects/${projectId}/tools/log?${query}`;
}

const SERVICES_UP = "run agentbreeder services up";
// M12 spec delta §4.4 "Console shows", plus the next step from the CLI's own message.
const REFUSALS = {
  CONSOLE_ROLE_MISSING: {
    reason: "the execution log needs service/postgres-pgvector@1.2.0 with console_database: DB (the log tables belong to the database owner, and this stack has no console role)",
    next: "set them in stack.yaml, with the database owner's grants, then run agentbreeder services up --update",
  },
  CONSOLE_ROLE_NO_ACCESS: {
    reason: "the database owner's init_sql has not granted the console role",
    next: "add the grants (Northwind's stack/db/commerce.sql shows them), then run agentbreeder services up --update",
  },
  DB_LOGIN_FAILED: { reason: "", next: `${SERVICES_UP} to refresh the stack's credentials` },
  SERVICES_STALE: { reason: "the stack's state predates the credentials this reads", next: SERVICES_UP },
  SERVICES_DOWN: { reason: "", next: "start the services (Services, or agentbreeder services up)" },
  TOOL_LOG_TIMEOUT: { reason: "the query timed out", next: "narrow the window (--since)" },
  AGENT_NOT_FOUND: { reason: "", next: "choose Every agent, or an agent in the stack's environment.agents" },
  DEPENDENCY_MISSING: { reason: "", next: 'pip install "agentbreeder[eval]"' },
  TOOL_LOG_UNSUPPORTED: { reason: "", next: "" },
};

/**
 * What to show for a failed read: {empty, reason, next}. `empty` is true for "no calls recorded
 * yet" (not a refusal). Otherwise `reason` follows "Unavailable: " and `next` is the step to take;
 * a code without its own wording shows the CLI's message and the terminal command.
 */
export function logRefusal(error, ref) {
  const code = error && typeof error.code === "string" ? error.code : "";
  const message = error && typeof error.message === "string" ? error.message : "";
  if (code === "TOOL_LOG_NO_TABLES") return { empty: true, reason: "", next: "" };
  const known = REFUSALS[code];
  const reason = (known && known.reason) || message || "the log could not be read";
  const next = known ? known.next : `run agentbreeder tool log ${ref} --project DIR in a terminal`;
  return { empty: false, reason, next };
}

const show = (value) => (typeof value === "string" && value ? value : "-");

/** The calls as rows, newest first as read; every unknown value is "-". */
export function logRows(data) {
  const calls = data && Array.isArray(data.calls) ? data.calls : [];
  return calls
    .filter((call) => call && typeof call === "object")
    .map((call, index) => {
      const tool = typeof call.tool === "string" ? call.tool : "";
      const role = typeof call.db_role === "string" ? call.db_role : "";
      return {
        key: `${index} ${call.at || ""}`,
        at: show(call.at),
        agent: show(call.agent),
        version: tool.includes("@") ? tool.split("@")[1] : "-",
        function: show(call.function),
        status: call.status === "ok" || call.status === "error" ? call.status : "-",
        latency: Number.isInteger(call.latency_ms) ? `${call.latency_ms} ms` : "-",
        turn: show(call.turn_id),
        trace: show(call.trace_id),
        role: role ? (role === "agentbreeder_console" ? `${role} (console)` : role) : "-",
        args: call.args,
        error: typeof call.error === "string" ? call.error : null,
      };
    });
}

export const MAX_AGENTS = 50;

/** The agent names offered by the filter: those known plus those in `rows`, sorted, at most
 * MAX_AGENTS (rows are what the runtime claims; a role can write any name). */
export function seenAgents(known, rows) {
  const names = new Set(Array.isArray(known) ? known : []);
  for (const row of Array.isArray(rows) ? rows : []) {
    if (names.size >= MAX_AGENTS) break;
    if (row && typeof row.agent === "string" && AGENT.test(row.agent)) names.add(row.agent);
  }
  return [...names].sort().slice(0, MAX_AGENTS);
}

/** One call's arguments as indented JSON text, or why there are none. */
export function argsText(args) {
  if (args === null || args === undefined) return "(not shown: too large, or not an object)";
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return "(unreadable)";
  }
}
