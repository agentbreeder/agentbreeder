# `runtime/claude-agent-sdk@1.0.0`

**Status:** on `main`, not in a published preview. **Not run end to end, not supported.** The
runtime, its two-uid deploy contract and the Northwind port are built and unit-tested (macOS
x86_64, no Docker); no image has been built, no turn has run against a real CLI or gateway, and
none of the multi-framework spec's §15 acceptance steps has run. What ran and what did not is in
[the F7 record](../superpowers/verification/F7-claude-agent-sdk.md).

A runtime for the [Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/) on AgentBreeder's
runtime kit (#1089, multi-framework spec §8.4). An agent picks it with one line, and everything
else in `agent.yaml` means what it means on `runtime/adk@1.3.0`:

```yaml
runtime: runtime/claude-agent-sdk@1.0.0
```

## AgentBreeder does not ship it: you supply it (owner decision D8)

Every platform wheel of `claude-agent-sdk` bundles the Claude Code CLI as a native binary
(0.2.177 bundles CLI 2.1.317). The SDK is MIT; the CLI is "© Anthropic PBC. All rights reserved"
and governed by Anthropic's legal agreements (Commercial Terms for API users). So:

- this runtime is **not** in AgentBreeder's built-in registry, and AgentBreeder publishes no image
  containing the CLI;
- the runtime directory is an example in `examples/northwind-claude-agent-sdk/registry/`. You copy
  `registry/runtime/claude-agent-sdk/1.0.0/` into **your own project's** `registry/` (the same
  directory layout), lock it with `agentbreeder build`, and your build downloads the SDK wheel,
  with the CLI inside it, from PyPI on your machine, pinned by its hash in
  `runtime/requirements.lock`;
- **running it means accepting Anthropic's terms for Claude Code.** AgentBreeder makes no claim
  about using the CLI with a non-Anthropic model behind the gateway (Northwind's port uses
  `support-model`, a Gemini alias, D6).

## The two-uid contract (owner decision D15)

The CLI is a general-purpose coding agent. F0 found that, without `verbatim_prompts`, an `@path`
mention in a customer message made it read a decoy of the gateway key file with every tool off.
So the CLI never runs as the uid that can read the agent's secrets:

- the runtime manifest says `isolation: two-uid`, and `build` writes `runtime.isolation: two-uid`
  into the deploy manifest;
- **local Docker:** deploy starts a second container from the agent's own image as uid 65533 with
  `AGENTBREEDER_ROLE=cli-host`, **no secret mount, no network** (`--network none`), no environment
  but its role, and the agent container's other flags (read-only root, all capabilities dropped,
  `no-new-privileges`, 512 MB, 1 CPU, 128 pids, a 64 MB `noexec` `/tmp`). The two share one 1 MB
  tmpfs volume at `/run/agentbreeder-cli` that only the agent's uid can write. Deploy verifies the
  CLI container (uid, no network, no capability, no bind mount, the agent's image, only the
  shared volume) before the deployment is ready, and removes it and the volume on teardown;
- **`kind`:** the pod gets a second container `cli` (uid 65533, no secret volume, its own
  memory-backed `/tmp`, the shared directory as a 1 Mi emptyDir). Pod Security `restricted`
  still holds. In a pod both containers share the network namespace, so the CLI container has the
  pod's network (still no credential);
- in the shared directory only the kit listens. Every connection is checked by the kernel's peer
  credentials: the kit accepts the CLI host's uid only, the host accepts the kit's uid only. The
  kit accepts one host at a time, and each CLI's stdin/stdout connection needs a one-time nonce
  sent over the host's connection only. The host makes itself non-dumpable;
- the CLI host refuses to start as root or as the kit's uid, with a `*_FILE` or secret variable
  in its environment, with a non-empty secret mount, or with a CLI version other than 2.1.317.

What it does **not** isolate (the `/security` review's residuals, recorded in the F7 record):

- **Concurrent turns share the CLI uid.** A compromised CLI could read another turn's transcript,
  environment (its turn token) and command line (its system prompt, with the knowledge block
  and notes) in the same container. Set `max_concurrent_turns` to 1 where customers must not
  share a container; per-turn uids need privileges the deploy does not grant.
- **`kind` is weaker than local Docker.** In a pod the CLI container shares the network
  namespace: it can reach the agent's unauthenticated `/invoke` on loopback and whatever the
  NetworkPolicy allows (no credential for the gateway or the database). The pod's `fsGroup`
  also gives it the agent's group; only the missing mount keeps the secret volume from it (a
  test pins that). The shared directory is mounted read-only in the CLI container on both
  targets.
- **A CLI process that leaves its process group** survives the group kill until the container
  stops; the host, as PID 1, reaps exited processes.
- **The deploy manifest's `runtime` fields are trusted** as written by `build` (they are in the
  digest, not checked against the image's label).

## How a turn runs

1. The kit prepares the turn (knowledge block, notes, history from `turns`) as for every kit runtime.
2. The adapter asks the CLI host for a **lease**: a per-turn directory `/tmp/turns/<random>/` in
   the CLI container (`home/` as HOME and CLAUDE_CONFIG_DIR, `tmp/` as TMPDIR, an empty `work/`),
   with the conversation's user and assistant text written as the CLI's transcript.
3. The SDK's `query()` runs in the kit's process with a custom transport: the host starts the CLI
   (in a new session, `setsid`) with that connection as its stdin and stdout and an environment
   built from scratch: PATH, the turn's directories, the model proxy, the turn token, the alias,
   the SDK's own variables, `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `DISABLE_AUTOUPDATER`,
   `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` and `CLAUDE_CODE_MAX_RETRIES=0`.
4. The host checks the command line keeps every control: `--tools ""` (no built-in tool),
   `--setting-sources=`, `--strict-mcp-config`, `--permission-mode default`, only the kit's
   in-process MCP server `agentbreeder` and its `mcp__agentbreeder__*` tools, no extra
   directories, plugins, settings or skipped permissions, and `--resume` only of the lease's
   session. The adapter also sets `verbatim_prompts=True` (no `@path` expansion, no slash
   commands), every built-in the CLI lists in `disallowed_tools`, and a deny-by-default
   `can_use_tool`.
5. Tools run in the kit's process, through the kit's wrapper (audit row, TOOL span).
6. At the turn's end, a timeout, a lost connection, the lease's 200 s deadline or SIGTERM, the
   host kills the CLI's process group and deletes the directory. It sweeps `/tmp/turns` at start.

## Models (D6, D17)

The CLI talks only to the kit's model proxy on the **Anthropic Messages route** (`/v1/messages`),
forwarded by the CLI host from a loopback port to the kit's socket. The proxy holds the gateway
key, rebuilds each request from an allow-list (dropping `metadata`, `context_management`,
`output_config` and every LiteLLM control field; capping `max_tokens`), rewrites any model name
to the agent's alias, never streams upstream (it synthesises the Anthropic events), and owns
retries, cost, coded errors and the step cap: the last allowed call goes with
`tool_choice: {type: none}`, and an answer that still calls a tool is withheld (the turn fails
`MODEL_STEP_LIMIT`), because Gemini ignores `none` (#1111).

The deployment's gateway key gets `/v1/messages` on top of the chat and embeddings routes. The
gate refuses this runtime wherever guardrails apply (an environment floor or agent
`guardrails:`), with `RUNTIME_GUARDRAILS_UNSUPPORTED`: output masking and the real Presidio images
on `/v1/messages` have not been run. `service/litellm@1.3.0`'s request filter refuses the
Messages call type (`GATEWAY_ROUTE_REFUSED`); `service/litellm@1.4.0` (#1187; unit-tested, its Docker
test run with stub providers only) serves it through the filter, with refused fields reviewed for Messages bodies, so run this runtime on 1.4.0. On
1.0.0 to 1.2.0 (no filter) the key's routes and aliases and the proxy's allow-list are the only
controls. Deploy does not yet refuse `anthropic-messages` on a gateway older than 1.4.0 (open,
owner decision pending). Northwind's shipped stack stays on 1.0.0 for its other agents; the
acceptance runs on a copy pinned to 1.4.0 (`tests/test_northwind_claude_agent_sdk_docker.py`).
`/v1/messages/count_tokens` is reachable with the key and unfiltered on 1.4.0 (open). The proxy also refuses
provider server-side tools and `url` content sources, and returns only the documented response
fields.

## Concurrent turns (D16)

`runtime/settings.json` caps concurrent turns (`max_concurrent_turns`, 2; a turn over the cap
waits for a slot within its own deadline) and leases (`max_leases`, 4). **The values are not
measured**: F0 saw 305 MB for 2 real turns and the 512 MB limit at 6 scripted turns in one
container; the CLIs now run in their own container. Change them in your copy of the runtime
(a new version, since registry versions are immutable).

## What it supports

| Part of `agent.yaml` | On this runtime |
|---|---|
| `model`, `prompt`, `tools` (up to 50), one `knowledge` entry, `memory` | As on the LangGraph runtime |
| `mcp`, `subagents`, `skills` | No (`build` refuses them) |
| `guardrails` | No (the gate refuses the deployment) |
| `model.reasoning` | **Ignored**: the Anthropic route sets no reasoning effort (the CLI sends its own `thinking`, clamped by the proxy). The port's `reasoning: low` therefore means less here than on ADK or LangGraph |

If the CLI container stops after start-up, `/health` stays 200 and every turn fails with a 500
(`HostUnavailable`) until deploy's verification or `reconcile` finds the container gone and fails
closed. A CLI killed during a turn (out of memory, the lease's deadline) fails that turn; it is
never an empty reply.

Traces: the kit's root `agent_turn` span, one TOOL span per tool call and one LLM span per model
call from the proxy (alias and token counts, no message text). The OpenInference Claude
instrumentor is not used (it adds SDK hooks and writes no LLM spans).

## Tests

- In the repo suite (no SDK): `tests/test_claude_runtime.py`, `tests/test_two_uid_deploy.py`,
  `tests/test_northwind_claude_agent_sdk.py`.
- With the SDK installed: `runtime_tests/` in the runtime directory.
- On a real stack (opt-in, written, not run): `tests/test_northwind_claude_agent_sdk_docker.py`.
