# `agentbreeder new`

Write a starter project (`new agent NAME`).

`new agent NAME --model gemini [--dir DIR] [--dry-run] [--json]` writes a starter project to `DIR` (default `./NAME`; it must not exist, or be an empty directory you own that others cannot write to, never a symlink): `agent.yaml` (`runtime/adk@1.0.6`, `traces: none`, `agent_events: none`, and a comment on the edit loop), `stack.yaml` (Postgres and LiteLLM, `chat` and `judge` aliases, a `DATABASE_URL` binding, `candidate: true`, `review_by` 180 days from today), `stack/litellm/config.yaml`, and the editable sources `src/prompt/` and `src/evals/` (the two eval cases run on 3.0.0a2, `retries: 1`); it publishes `prompt/NAME-prompt@0.1.0` and `eval/NAME-evals@0.1.0` into `DIR/registry` and writes `agent.lock`, so `build --locked` passes. Files are 0644 and directories 0755; on failure it removes what it wrote.

`--model` is required and only `gemini` is offered (the only provider run end to end). `--dry-run` writes nothing; `--json` prints the files (path, mode, hash) and the published refs, and on a refusal `{"schema_version": 1, "error": {"code", "message"}}` (exit 2; argparse usage errors stay plain text). A group-writable existing `DIR` is accepted only off macOS and only for your own group.

The template ran once end to end from a checkout on macOS (2026-11-12, `release` 2/2).

To change the prompt: edit `src/prompt/prompt.md`, bump `version:` in `src/prompt/manifest.yaml`, `registry publish src/prompt`, change the ref in `agent.yaml`, then `build` and `release`.

Flags: `agentbreeder new --help`.
