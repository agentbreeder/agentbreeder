// Onboard an agent.yaml alone (#823, M7). The three-tier confirm: (1) the server's preview of
// `new agent --stack-for --dry-run --json`, (2) a "this code runs in agents" checkbox when the
// project brings code from outside the built-in registry, (3) the confirm button. The server
// re-checks (2) and the previewed agent.yaml digest before it writes. Pure functions only.

import { starterNameProblem } from "./guide.js";

// `newagent.PROVIDERS` (a server test keeps them equal); gemini is the one run end to end.
export const PROVIDERS = ["anthropic", "gemini", "openai", "openrouter"];
export const VERIFIED = "gemini";

/** Why the form cannot be previewed yet, as a sentence, or null. */
export function formProblem({ path, name, model, modelId }) {
  if (!path.trim()) return "Type the absolute path of the agent.yaml first.";
  if (!path.trim().startsWith("/")) return "The path must be absolute (start with /).";
  const nameProblem = starterNameProblem(name.trim());
  if (nameProblem) return nameProblem === "Type a name first." ? "Type the agent's name (its name: in agent.yaml) first." : nameProblem;
  if (!PROVIDERS.includes(model)) return "Pick a model provider first.";
  if (model === "openrouter" && !modelId.trim()) return "OpenRouter needs a model id, such as openrouter/<vendor>/<model>.";
  return null;
}

/** The request body for the preview (and, with the digest, for the write). */
export function previewBody({ path, name, model, modelId }) {
  const body = { path: path.trim(), name: name.trim(), model };
  if (modelId.trim()) body.model_id = modelId.trim();
  return body;
}

export function writeBody(form, preview, codeConfirmed) {
  return { stack_for: { ...previewBody(form), digest: preview.agent_sha256, code_confirmed: codeConfirmed === true } };
}

/** The refs whose code runs in agents and does not ship with AgentBreeder. */
export function codeRefs(preview) {
  return ((preview && preview.refs) || []).filter((ref) => ref.runs_code && ref.root_kind !== "builtin");
}

/** Where a ref resolved, in words. */
export function rootText(rootKind) {
  return { project: "This project's registry", path: "AGENTBREEDER_REGISTRY_PATH", builtin: "Built in" }[rootKind] || "Unknown";
}

/** Why the confirm button is off, or null: the code checkbox when the project brings code. */
export function confirmProblem(preview, codeConfirmed) {
  if (!preview) return "Preview first.";
  if (preview.runs_code && !codeConfirmed) return "Confirm that this code runs in agents first.";
  return null;
}

/** A digest shortened for display; the full value stays in the title. */
export function shortDigest(digest) {
  return typeof digest === "string" && digest.startsWith("sha256:") ? `${digest.slice(7, 19)}…` : "";
}
