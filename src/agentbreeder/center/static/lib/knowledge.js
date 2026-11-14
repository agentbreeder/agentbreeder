// A knowledge version's index status (#830, M14 console half), as pure functions over the
// `knowledge status --json` answer the server checked (docs/knowledge.md): the state, the chunk
// and document counts, the index times and the embedding. Every string from the answer is
// untrusted text; the screen renders it through <Text>. Nothing here touches the DOM.
//
// Stable exports: STATE_LABELS, statusPath, statusRefusal, statusFacts, stateText.

export const STATE_LABELS = { not_indexed: "Not indexed", fresh: "Fresh", stale: "Stale" };

const NAME = "[a-z0-9][a-z0-9-]{0,62}";
const VERSION = "(0|[1-9][0-9]{0,5})\\.(0|[1-9][0-9]{0,5})\\.(0|[1-9][0-9]{0,5})";
const REF = new RegExp(`^knowledge/${NAME}(@${VERSION})?$`);

/** The API path of one status read, or null for a ref the server would refuse. */
export function statusPath(projectId, ref) {
  if (typeof ref !== "string" || !REF.test(ref)) return null;
  return `/api/projects/${projectId}/knowledge/status?${new URLSearchParams({ ref })}`;
}

const SERVICES_UP = "run agentbreeder services up";
// docs/knowledge.md "Refusals", with the step a person can take.
const REFUSALS = {
  SERVICES_DOWN: { reason: "the stack's services are not running", next: "start the services (Services, or agentbreeder services up)" },
  SERVICES_STALE: { reason: "the stack's state predates the agent's database credential", next: SERVICES_UP },
  DB_LOGIN_FAILED: { reason: "the agent's database credential was refused", next: `${SERVICES_UP} to refresh it` },
  KNOWLEDGE_STATUS_UNSUPPORTED: {
    reason: "the agent's DATABASE_URL is not bound to a service/postgres-pgvector database or role",
    next: "bind it in stack.yaml, then run agentbreeder services up --update",
  },
  KNOWLEDGE_STATUS_TIMEOUT: { reason: "the database query ran over 5 seconds", next: "try again" },
  KNOWLEDGE_STATUS_BUSY: { reason: "", next: "try again when another status read ends" },
  DEPENDENCY_MISSING: { reason: "", next: 'pip install "agentbreeder[eval]"' },
};

/**
 * What to show for a failed read: {reason, next}. `reason` follows "Unavailable: "; a code
 * without its own wording shows the CLI's message and the terminal command.
 */
export function statusRefusal(error, ref) {
  const code = error && typeof error.code === "string" ? error.code : "";
  const message = error && typeof error.message === "string" ? error.message : "";
  const known = REFUSALS[code];
  const reason = (known && known.reason) || message || "the index status could not be read";
  const next = known ? known.next : `run agentbreeder knowledge status ${ref} --project DIR in a terminal`;
  return { reason, next };
}

const count = (value) => (Number.isInteger(value) && value >= 0 ? String(value) : "-");
const part = (value) => (value && typeof value === "object" ? value : {});
const text = (value) => (typeof value === "string" && value ? value : "");

/** One sentence on what the state means and what changes it. */
export function stateText(version) {
  const state = version && version.state;
  if (state === "fresh") return "Every indexed chunk matches the documents and the embedding dimensions agree.";
  if (state === "not_indexed") return "No table yet: the runtime indexes this version at the agent's first knowledge search, which release's eval does.";
  if (state === "stale") return "Out of date: the runtime re-indexes at its next process start and first search, which release's eval does.";
  return "";
}

/** The facts of one version as [label, value] pairs; every unknown value is "-". */
export function statusFacts(version) {
  const v = part(version);
  const documents = part(v.documents);
  const chunks = part(v.chunks);
  const updated = part(v.updated);
  const embedding = part(v.embedding);
  const table = text(v.table);
  const schema = text(v.schema);
  let where = "-";
  if (schema && table) where = `${schema}.${table}`;
  else if (table) where = v.state === "not_indexed" ? `${table} (not created yet)` : table;
  const oldest = text(updated.oldest);
  const newest = text(updated.newest);
  const dims = Number.isInteger(embedding.dimensions) ? `${embedding.dimensions} dimensions` : "dimensions unknown";
  const tableDims = Number.isInteger(embedding.table_dimensions) && embedding.table_dimensions !== embedding.dimensions
    ? ` (the table has ${embedding.table_dimensions})`
    : "";
  return [
    ["Table", where],
    ["Chunks", `${count(chunks.indexed)} indexed of ${count(chunks.expected)} (fresh ${count(chunks.fresh)}, stale ${count(chunks.stale)}, missing ${count(chunks.missing)}, extra ${count(chunks.extra)})`],
    ["Documents", `${count(documents.indexed)} indexed of ${count(documents.expected)}`],
    ["Indexed", oldest || newest ? `${oldest || "-"} to ${newest || "-"}` : "-"],
    ["Embedding", `${text(embedding.alias) || "-"}, ${dims}${tableDims}`],
  ];
}
