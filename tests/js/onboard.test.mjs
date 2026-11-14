import assert from "node:assert/strict";
import { test } from "node:test";

import {
  PROVIDERS, codeRefs, confirmProblem, formProblem, previewBody, rootText, shortDigest, writeBody,
} from "../../src/agentbreeder/center/static/lib/onboard.js";

const form = { path: "/p/support/agent.yaml", name: "support", model: "gemini", modelId: "" };
const DIGEST = `sha256:${"b".repeat(64)}`;
const ref = (kind, root_kind, runs_code) => ({ ref: `${kind}/x@1.0.0`, kind, root_kind, runs_code });

test("the providers are new agent's", () => {
  assert.deepEqual(PROVIDERS, ["anthropic", "gemini", "openai", "openrouter"]);
});

test("the form says what is missing, in order", () => {
  assert.equal(formProblem(form), null);
  assert.match(formProblem({ ...form, path: " " }), /absolute path/);
  assert.match(formProblem({ ...form, path: "p/agent.yaml" }), /must be absolute/);
  assert.match(formProblem({ ...form, name: "" }), /name: in agent.yaml/);
  assert.match(formProblem({ ...form, name: "Support" }), /lowercase letter first/);
  assert.match(formProblem({ ...form, model: "mistral" }), /provider/);
  assert.match(formProblem({ ...form, model: "openrouter" }), /OpenRouter needs a model id/);
  assert.equal(formProblem({ ...form, model: "openrouter", modelId: "openrouter/a/b" }), null);
});

test("bodies are trimmed and carry the model id only when given", () => {
  assert.deepEqual(previewBody({ ...form, path: " /p/agent.yaml ", name: " support " }), {
    path: "/p/agent.yaml", name: "support", model: "gemini",
  });
  assert.equal(previewBody({ ...form, modelId: " openai/x " }).model_id, "openai/x");
  const body = writeBody(form, { agent_sha256: DIGEST }, true);
  assert.deepEqual(body.stack_for, { ...previewBody(form), digest: DIGEST, code_confirmed: true });
  assert.equal(writeBody(form, { agent_sha256: DIGEST }, "yes").stack_for.code_confirmed, false);
});

test("only code from outside the built-ins needs the confirm", () => {
  const refs = [ref("runtime", "builtin", true), ref("prompt", "project", false), ref("eval", "project", true), ref("tool", "path", true)];
  assert.deepEqual(codeRefs({ refs }).map((r) => r.kind), ["eval", "tool"]);
  assert.deepEqual(codeRefs(null), []);
  assert.equal(confirmProblem(null, true), "Preview first.");
  assert.match(confirmProblem({ runs_code: true }, false), /runs in agents/);
  assert.equal(confirmProblem({ runs_code: true }, true), null);
  assert.equal(confirmProblem({ runs_code: false }, false), null);
});

test("roots and digests read as words", () => {
  assert.equal(rootText("project"), "This project's registry");
  assert.equal(rootText("path"), "AGENTBREEDER_REGISTRY_PATH");
  assert.equal(rootText("builtin"), "Built in");
  assert.equal(rootText("x"), "Unknown");
  assert.equal(shortDigest(DIGEST), `${"b".repeat(12)}…`);
  assert.equal(shortDigest("md5:x"), "");
  assert.equal(shortDigest(null), "");
});
