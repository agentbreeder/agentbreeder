# #1089 F7 verification: `runtime/claude-agent-sdk@1.0.0` and the two-uid deploy contract

Status: **built and unit-tested on macOS 13 x86_64 without Docker. Nothing in spec §15 or the
§16.1 items marked F7 has run.** No image was built, no Claude Code CLI was started, no gateway
was called. The runtime is not supported, is not in `agentspec.VERIFIED` or `RUNTIMES_RUN`, and
is not on the website (D15: not before the two-uid contract has run; F9).

Owner decisions applied (2026-11-19): D6 (`support-model` through `/v1/messages`), D8 (not shipped;
the runtime is in the port's own project registry), D15 (two uids first; `verbatim_prompts` and a
scrubbed CLI environment kept), D16 (a configurable concurrent-turn cap, unmeasured), D17
(`/v1/messages` on keys of `model_api: anthropic-messages` deployments).

## Design decisions made in this slice (for review)

- **Two uids means two containers.** One container cannot hold two uids: the deploy runs with
  `--cap-drop ALL` and `no-new-privileges`, and a root launcher with `SETUID`/`SETGID` would be
  refused by the `kind` namespace's Pod Security `restricted` level, which the deployer requires
  (`KUBERNETES_NAMESPACE_UNRESTRICTED`). So the CLI runs in a second container from the same image
  (uid 65533, no secret mount; on Docker `--network none`), and the SDK reaches it through a
  custom `Transport` (`query(transport=...)`, public in the SDK): no CLI process, wrapper or
  inherited environment exists in the agent container. The spec's `env -i` wrapper is the CLI
  host's spawn with an environment built from scratch; `setsid` is `start_new_session`.
- **Kit 1.0.0 is unchanged.** The Anthropic Messages route is a subclass in the runtime
  (`anthropic_proxy.py`), so `runtime/langgraph@1.0.0` and the parallel F4/F5 runtimes keep kit
  1.0.0. A later kit version can take it in.
- **No OpenInference Claude instrumentor.** 0.1.24 injects SDK hooks (the spec sets none) and
  writes no LLM spans; the proxy writes one LLM span per call.
- **The CLI version is pinned twice:** the lock's wheel hash, and the CLI host refusing any
  bundled CLI but 2.1.317.

## What ran (macOS 13 x86_64, no Docker)

| What | Where | Result |
|---|---|---|
| The two-uid deploy pieces against a scripted Docker and kubectl: the CLI container's arguments (uid 65533, `--network none`, the agent's flags, only the shared volume, no secret, only its role variable), the tmpfs volume, the start order, verification failing closed on 14 kinds of drift, teardown of the volume, the `kind` pod's second container (PSS restricted kept), the manifest fields written only when declared (digests of other manifests unchanged), key routes, the gate's refusal under a floor and under agent guardrails | `tests/test_two_uid_deploy.py` | 36 passed (3.12) |
| The kit's link and the CLI host on real Unix sockets in one process: hello, one host at a time, nonces, leases and transcripts, a fake CLI started in its own process group with only the allow-listed environment and the data connection as stdin/stdout, `end` killing it and removing its directory, lease cap and deadline, losing the kit ending every turn, the forwarder; `check_argv` (16 broken command lines), `cli_env`, settings, start-up checks; the Anthropic route (allow-list, alias rewrite, `?beta=true`, 404s, tokens, peer refusal, SSE events, the step cap withholding a tool-calling Nth answer and refusing the (N+1)th unsent, coded failures that stick, retries, main-loop detection); the vendored kit byte-identical | `tests/test_claude_runtime.py` | 80 passed (3.12) |
| The Northwind port builds `--locked` with Northwind's config and artifacts byte for byte, the deploy manifest carries `runtime: {ref, profile: kit, model_api: anthropic-messages, isolation: two-uid}`, `mcp:` refused, not a standalone example, the stack names its identity | `tests/test_northwind_claude_agent_sdk.py` | 6 passed (3.12) |
| The adapter against the **real SDK 0.2.177 source**, installed from its sdist (no bundled CLI) with the lock's other pins: the SDK's own command line for the adapter's options passes the CLI host's checks (fresh and resumed), the options keep every control, a tool keeps its JSON Schema and runs through the kit, reply rules, a turn leases, runs and always ends | `runtime_tests/test_claude_adapter.py` | 8 passed (3.12, macOS) |
| The lock resolves to identical pins on x86_64 and aarch64 manylinux_2_28 (uv 0.11.6, `--exclude-newer 2026-11-05`, 48 packages) | | done |

`modules.json` was generated from the lock's other packages installed for linux x86_64 into a
scratch directory, with `claude-agent-sdk`'s import name taken from its sdist: the wheel with the
CLI binary was never downloaded (D8).

## Not run (each with its command)

All commands assume `services up` for Northwind with real keys (see the port's README).

| §15 / §16.1 item | Command |
|---|---|
| `docker build` and `agentbreeder conformance` (§15.1) | `AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_CLAUDE=1 python -m pytest -q tests/test_northwind_claude_agent_sdk_docker.py -k conformance` |
| Northwind 61/61 through `release` on `support-model` via `/v1/messages` (§15.2) | `... -k release` |
| The two-uid topology on a real engine: uid 65533, no network, no secret, no route out | `... -k topology` |
| `@<secret path>`, `@/etc/hosts`, slash commands and `!`, `#`, `@agent-…`, `@`-MCP, image paths, URLs reach the model as text, also replayed through a written transcript; the key never in a reply (§16.1) | `... -k literal` |
| One invoke per Northwind tool, no orphaned CLI and no turn directory left after each (§15.3) | `... -k tool` |
| A transcript written from `turns` gives the CLI a faithful history (§16.1) | covered by `-k release` (7 multi-turn cases) and `-k literal` |
| The CLI without `context_management` and `output_config` (§16.1) | covered by every turn above |
| Concurrent-turn memory at 2, 3 and 4 real turns, both containers, OOM checked (D16) | `... -k concurrent` |
| The group kill on SIGTERM, no orphan (§16.1) | `... -k sigterm` |
| The group kill at a turn timeout | not scripted; by hand: a turn held past 170 s, then the `-k tool` leftovers check |
| `kind`: promote and the CLI container in a PSS-restricted pod; Kubernetes `/tmp` (§15.6, §16.1) | `AGENTBREEDER_KIND_TEST=1 ... -k kind` |
| Cost to the cent against LiteLLM's spend log, `MODEL_ACCESS_DENIED`, `MODEL_BUDGET_EXCEEDED` (§15.4, §15.5) | by hand, as F3 |
| Real Presidio and output masking on `/v1/messages` (D17) | not until the runtime declares `guardrails`; the gate refuses it meanwhile |
| The main-loop test (system text contains the prompt, tool set) on the real CLI (§16.1) | the `model_called` rows of `-k release` (`main_loop`) |
| arm64 install and run | an arm64 host, the same commands |
| `session_store` receiving no entries (informational) | not investigated: this runtime does not use it |

| The touched and new test files (the above plus `test_runtime_profile`, `test_kit_egress`, `test_gateway_keys`, `test_manifest_v2`) | | 444 passed on 3.11 and on 3.13 |
| pip-audit of the runtime lock (`--no-deps`) and of the dev environment | | no known vulnerabilities |
| bandit on the runtime (kit excluded, reviewed with F3) | | 0 high; 2 medium: the CLI container's own `/tmp/turns` (a dedicated tmpfs) and the protocol port on 0.0.0.0, as `runtime/adk` |

## Review log

All read-only subagents on Sonnet (the coder ran on Opus); the head SHAs are in the PR body.

| Round | Found | Done |
|---|---|---|
| `/security` of the image and the uid boundary | No path from the CLI to the gateway key or database URL. Medium: on `kind` the CLI could replace the kit's socket directory (world-writable emptyDir); `kind` shares the pod network and gives the CLI the agent's group; concurrent turns share the CLI uid; uncapped data-socket threads and an accept loop an `OSError` could stop; `litellm@1.3.0` refuses `/v1/messages` and its filter is unreviewed for Messages bodies. Low: provider server-side tools and `url` sources passed through; a CLI leaving its group escapes the kill and zombies pile up; `check_argv` a deny-list (respelled flags); `_verify_cli` missed the volume options and `no-new-privileges`; manifest fields trusted | Fixed: read-only shared mount on both targets, socket-directory ownership check, umask instead of chmod, handler cap, guarded accept loop, content rules and a response allow-list, a PID-1 reaper and `chmod` before removal, an exact flag allow-list (with a 120 KiB per-argument cap), volume-option and `no-new-privileges` checks, a test that the CLI never mounts secrets. Documented as residuals: `kind`'s shared network and group, the shared CLI uid, trusted manifest fields, the gateway prerequisite |
| Code review | Medium: control-socket sends unlocked (concurrent large leases could interleave); a killed CLI read as an empty reply (fallback text, no `turn_failed`); `model.reasoning` ignored on the Anthropic route; SSE indices with gaps and `redacted_thinking` dropped. Low: a late data socket leaked, a too-long argument, a stale docstring, `/health` after the CLI container dies | Fixed: send lock, the transport raising on a CLI that ends without a result and `reply_text` raising (the proxy's state still decides first), emitted-block indices and `redacted_thinking` passed through, the leak, the argument cap, the docstring. Documented: `reasoning` ignored, `/health` after the CLI container dies |
| Re-review of the fixes | All OK. Low: a remaining leak window between the nonce pop and the socket hand-over; `remove_tree` chmod followed a planted symlink | Fixed both, with tests. Converged: nothing above low left |
