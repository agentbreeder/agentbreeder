// Onboard an agent.yaml alone (#823, M7). The three-tier confirm: (1) the server's preview of
// `new agent --stack-for --dry-run --json`, (2) a "this code runs in agents" checkbox when the
// project brings code from outside the built-in registry, (3) the confirm button. The server
// re-checks (2) and the previewed agent.yaml digest before it writes. Pure functions only.

import { starterNameProblem } from "./guide.js";

// `newagent.PROVIDERS` and the ones run end to end with their default model (`verified`; a
// server test keeps both equal). A preview's own `verified` is the CLI's answer and wins.
export const PROVIDERS = ["anthropic", "gemini", "openai", "openrouter"];
export const VERIFIED = ["gemini", "openai", "openrouter"];
// The default model each verified provider ran with (`newagent.PROVIDERS[...].model`).
export const DEFAULT_MODEL = {"gemini": "gemini/gemini-3.8-flash", "openai": "openai/gpt-5.6", "openrouter": "openrouter/google/gemini-3.8-flash"};

/**
 * What `verified` does not cover, or null: a verified provider ran end to end with its default
 * model only, so another model id may still fail the eval gate on its answers.
 */
export function modelIdNote(verified, model, modelId) {
  if (verified !== true || typeof modelId !== "string" || !modelId.trim()) return null;
  if (modelId.trim() === DEFAULT_MODEL[model]) return null;
  return "This provider has been run end to end with its default model, not with this model id: the eval gate may still fail on its answers.";
}

/** Why the form cannot be previewed yet, as a sentence, or null. */
export function formProblem({ path, name, model, modelId }) {
  if (!path.trim()) return "Type the absolute path of the agent.yaml first.";
  if (!path.trim().startsWith("/")) return "The path must be absolute (start with /).";
  const nameProblem = starterNameProblem(name.trim());
  if (nameProblem) return nameProblem === "Type a name first." ? "Type the agent's name (its name: in agent.yaml) first." : nameProblem;
  if (!PROVIDERS.includes(model)) return "Pick a model provider first.";
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
