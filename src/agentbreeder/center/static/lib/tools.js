// A tool version's detail (#828, M12 console half), as pure functions over the `registry show
// --json` answer: its functions (from tool.yaml, with the Python signature the CLI read with `ast`;
// nothing is imported) and the wording around them. Every value from tool.yaml is untrusted text;
// the screen renders it through <Text>. Nothing here touches the DOM.
//
// Stable exports: SIGNATURE_REASONS, signatureText, parameterRows, functionRows.

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
