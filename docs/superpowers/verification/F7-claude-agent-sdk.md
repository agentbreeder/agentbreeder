# #1089 F7 verification: `runtime/claude-agent-sdk@1.0.0` and the two-uid deploy contract

Status: **the acceptance ran on Docker and `kind` (2026-11-20), except conformance and arm64.**
Northwind on the Claude Agent SDK released 61/61 through the gateway's `/v1/messages` route,
locally (three times) and on `kind`, on a copy of Northwind's stack pinned to
`service/litellm@1.4.0`. The two-uid topology held on a real engine and in a PSS-restricted pod.
`agentbreeder conformance` cannot run this runtime: it starts no CLI container, so the agent
answers 503 (#1199). The run found and fixed two tracing bugs and set the D16 cap from a
measurement (below). macOS 13 x86_64; arm64 was not run. The runtime is not called supported,
and `agentspec.VERIFIED` and `RUNTIMES_RUN` are unchanged (owner decision).

The first half of this record ("Design decisions" to "Review log") was written without Docker.
The second half, "Docker acceptance run", is the run.

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

## The plan before the run (each with its command)

As written before the run; each row's outcome is in "Docker acceptance run" below. All commands assume `services up` for Northwind with real keys (see the port's README).

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

## Docker acceptance run (2026-11-20)

### Setup

- Branch `feat/1089-f7-claude-agent-sdk` at `a197faf6`, merged with `main` at `60b93f46` (CrewAI F6 and two
  console fixes; conflicts in `CLAUDE.md`, `example.py`, Northwind's `stack.yaml`,
  `test_center_agents.py` and `_content.json`, resolved by keeping both sides). Each run's tree
  was exported with `git stash create` + `git archive` and built with `python -m build --wheel`:
  the first run from `9936aba2` (wheel sha256 `4d23ac10…`), a second with the tracing fixes and
  a cap of 3, and the last from `8efff13e` (wheel `790bb421…`), whose runtime bytes are this
  commit's. Installed with `[eval]` and pytest into a fresh
  Python 3.12.8 venv.
- Every command ran under `env -i` with a fresh `HOME`, `TMPDIR` in the sandbox, `USER=demo`,
  `PIP_NO_CACHE_DIR=1`, and a Docker context created in the sandbox `HOME` pointing at the current
  context's socket. `kind` came from `~/.local/bin`. A 0600 file held only `GEMINI_API_KEY` and
  `TAVILY_API_KEY` from `~/.env`; no Anthropic key was used (`support-model` is Gemini, reached
  through LiteLLM's `/v1/messages` translation). The file was deleted at the end.
- macOS 13 x86_64, Docker Desktop engine 27.5.1 (kernel 6.12.5-linuxkit). Resource Saver was off.
- `agentbreeder example northwind`, the gateway pinned to `service/litellm@1.4.0` in the copy
  (`sed`), then `services up --update --environment dev --env-file <keys> --yes`. **The 1.4.0
  gateway accepted Northwind's `stack/litellm/config.yaml`**; no call by the CLI or the evals was
  refused (`GATEWAY_PARAM_REFUSED` / `GATEWAY_ROUTE_REFUSED`: none). Northwind's shipped stack was
  not changed.
- The port is not in the wheel (D8), so it was copied from the exported tree beside the Northwind
  copy; the test file ran from the sandbox, outside the repository, with
  `AGENTBREEDER_NORTHWIND_DIR` set to the copy.

### Results

| Item | Result |
|---|---|
| `docker build` | **Built.** linux/amd64, **226 MB** (the SDK wheel with its bundled CLI 2.1.317). |
| `agentbreeder conformance` (§15.1) | **Cannot run this runtime (#1199).** Result `fail`: 4 pass, 20 fail, 15 not run; every failure is a 503, because the harness starts only the agent container and the agent waits for a CLI host that never connects. The same image deploys and passes 61/61. The test is now `xfail(strict=True)` pointing at #1199. No per-check comparison with F3/F4/F5 is possible. |
| Northwind 61/61 through `release` on `support-model` via `/v1/messages` (§15.2, D6, D17) | **PASS, 61/61, four times**: three local releases (one flaky case each, passed on retry: `cancel-other-customers-order` twice, `order-unknown-id` once; mean correctness 1.0) and the `kind` cluster evals (one flaky case). **p50 latency 13.4 to 19.6 s** (LangGraph and Deep Agents about 3.5 s): every turn starts a CLI process. |
| Multi-turn history from a written transcript (§16.1) | The multi-turn golden cases passed in every release. |
| The two-uid topology on a real engine (D15, `-k topology`) | **PASS.** CLI container: user `65533:65533`, `NetworkMode: none`, `CapDrop: [ALL]`, no bind mount, no `/run/secrets`, no `*_FILE` variable, `1.1.1.1:443` unreachable. |
| Literal input (§8.4, §16.1, `-k literal`) | **PASS.** 14 inputs (`@/run/secrets/llm-gateway-key`, `@/etc/hosts`, `/config`, `/mcp`, `/model opus`, `/compact`, `/clear`, `/deep-research`, `!cat /etc/hosts`, `# remember…`, `@agent-…`, `@agentbreeder:orders`, an image path, a URL), each then replayed from a written transcript: all 28 turns answered by the model (every turn has ≥1 `model_called` row, none failed), so none was dispatched by the CLI; no reply held the key or `/etc/hosts`. Gemini declined the off-topic ones under Northwind's prompt, so the test shows no expansion and no leak, not what the model was shown. |
| One invoke per Northwind tool, no leftovers (§15.3, `-k tool`) | **PASS** in all three runs: all 7 tools ran, each reply `framework: claude-agent-sdk`, `runtime: runtime/claude-agent-sdk@1.0.0`, and after each turn no `claude` process and an empty `/tmp/turns` in the CLI container. |
| The main-loop test on the real CLI (§16.1) | **PASS.** Every `model_called` row of the session is `main_loop: true` (690 of 690): the CLI made no hidden calls (no title or "small fast" model calls) and the proxy's classifier recognised every main-loop call. |
| The CLI without `context_management` and `output_config` (§16.1) | Every turn above ran with them dropped. |
| SIGTERM group kill (§16.1, `-k sigterm`) | **PASS.** The test as first written stopped the agent before any CLI started (it proved nothing); it now waits until a `claude` process exists. By hand on a probe: one CLI and its turn directory during the turn, **none 3 s after `docker stop`**; the turn answered 500. |
| The timeout kill (by hand) | **Not reached; the coded path ran instead.** With the gateway paused (`docker pause`), the proxy's own timeouts and retries ended the turn at 154 s, before the 170 s turn deadline, with **502 `MODEL_UNAVAILABLE`** (gateway 408); the CLI process and turn directory were gone afterwards. The deadline's group kill itself was not observed on a real engine (unit-tested). |
| Step cap on a real Gemini alias (by hand, probe `max_model_calls: 1`) | **PASS.** **502 `MODEL_STEP_LIMIT`**; its one `model_called` row has `step_limited: true`, `finish_reason: tool_use`, `main_loop: true`, $0.00226275, and no `tool:` row: the withheld tool never ran. |
| `MODEL_ACCESS_DENIED` live (§15.5) | **PASS.** The probe's key changed to `models: [support-embedding]` (`/key/update`, master key from the stack's secret file, never printed): **502 `MODEL_ACCESS_DENIED`**, then the key restored. The key's `allowed_routes` included `/v1/messages` (D17). |
| `MODEL_BUDGET_EXCEEDED` live (§15.5) | **PASS.** Probe `budget_usd: 0.0001`: after the step-cap turn spent $0.0023, the next two turns got **402 `MODEL_BUDGET_EXCEEDED`** (LiteLLM 422 `Budget has been exceeded!`, key masked `sk-[redacted]` in the row). |
| Cost to the cent (§15.4) | **PASS, exactly, row by row.** All 690 `model_called` rows of the session match a LiteLLM `support-model` success row with the same cost within 15 s ($2.5673705). LiteLLM has 4 more rows ($0.01599525), each a call in flight when its agent was stopped (the two SIGTERM tests and the two kill probes), completed by the gateway after the turn had ended: billed, never used. |
| Phoenix (§15.3) | **Two bugs found and fixed** (below). On the fixed runtime, from 01:47 UTC to the end, `kind` included: **302 turns, 302 traces, 302 `agent_turn` roots; 472 `model_called` rows, 472 LLM spans, all under `agent_turn`; 189 `tool:*` rows, 189 TOOL spans**; no other spans. |
| Egress (§15.7) | **PASS (default deny).** The agent sat on its `-int` network and the stack's `agents` network, both `internal: true`; from inside, `1.1.1.1:443` and `8.8.8.8:53` network unreachable, `api.anthropic.com`, `generativelanguage.googleapis.com`, `example.com` unresolvable. The CLI container: `--network none`. |
| D16 memory (owner: measure, then cap) | **Measured, cap set to 2 turns and 3 leases** (below). |
| `promote` to `kind` (§15.6, `-k kind`) | **PASS.** `cluster up`, then `promote`: pushed to `localhost:5001`, **cluster evals 61/61** (one flaky case), p50 19.6 s. |
| The CLI container on `kind` (§16.1) | Pod in the `restricted` namespace; pod `runAsUser 65532`, `fsGroup 65532`, seccomp `RuntimeDefault`. `agent` container: secrets mounted, `/tmp` a 64 Mi memory `emptyDir`, the shared 1 Mi directory read-write. `cli` container: uid/gid 65533 with supplementary group 65532 (the pod's `fsGroup`: decision 2), no secret and no `*_FILE`, its own 64 Mi memory `/tmp`, the shared directory read-only, read-only root, all capabilities dropped, 512 Mi limit. From the CLI: `1.1.1.1:443` timed out and `api.anthropic.com` unreachable (NetworkPolicy), but **the agent's `127.0.0.1:8080` is open** (the shared pod network: decision 2's residual, as documented). |
| One invoke on `kind` (by hand) | **PASS.** `forward` then POST `/invoke`: `lookup_orders`, 2 model calls, $0.00741075, `framework: claude-agent-sdk`. |
| Memory at rest | After the run: agent 68.8 MiB, CLI container 13.5 MiB idle (`docker stats`), each of 512 MiB. |
| **arm64** | **Not run**: this host is x86_64. |

### What the run found and fixed

1. **Every LLM span was a trace of its own.** The proxy serves each CLI request in its own
   connection task, which does not carry the turn's context, so all 222 LLM spans of the first
   runs were roots (Phoenix: 363 traces for 141 turns). Fix (runtime-local, kit unchanged):
   `AnthropicProxy.bind_trace(state)` stores the caller's context per turn, the adapter calls it
   inside `agent_turn`, `messages()` attaches it around the LLM span, and `close_turn` drops it.
   Test: `test_llm_spans_nest_under_the_turn_that_bound_its_trace` (fails without the fix).
2. **Two TOOL spans per tool call and three protocol spans per turn.** `mcp` 2.3.0 installs an
   `OpenTelemetryMiddleware` on every server; its `tools/call <name>` span has
   `gen_ai.operation.name: execute_tool` and no OpenInference kind, so the kit's relabelling did
   not apply and Phoenix read it as TOOL. Fix: the adapter removes that middleware from the
   in-process server (`without_tracing`). Test: `runtime_tests`
   `test_the_mcp_servers_own_tracing_is_removed`, which also asserts the SDK still installs it
   (a tripwire for upgrades).
3. **The D16 cap.** See below. `settings.json` is now `max_concurrent_turns: 2`, `max_leases: 3`
   (was 2 and 4).
4. **Test file fixes:** `_containers()` read `list --json` as a mapping (it is a list without
   the container name; the store names it `agentbreeder-<id>`); the SIGTERM test now waits for a
   running CLI; the conformance test is `xfail` (#1199); the docstrings say the port must be
   copied beside the Northwind copy when running from a wheel.

The fixes changed the runtime's bytes, so its hash in the port's `agent.lock` changed and every
Docker item above was run again on the final bytes (release 61/61, topology, literal, tools,
concurrency, SIGTERM, `kind`). The by-hand probes (kill, step cap, access, budget) ran on the
earlier bytes; the fixes do not touch those paths.

Review: a read-only Sonnet delta review of the fixes (security and correctness lenses) returned
**APPROVED** with low findings only. Applied: the test's exporter is shut down after use. Left,
low: `close()` does not clear the stored contexts (a no-op at process exit); a turn without a
bound context fails open to root spans; the adapter imports `mcp.server._otel` (private; pinned
in the lock, and the tripwire test fails loudly on drift).

### D16: concurrent-turn memory

A probe deployment of the port with `max_concurrent_turns: 4` and `max_leases: 4` sampled
`memory.current` of both containers and counted `claude` processes every 0.2 s while N turns ran
at once (Northwind's `lookup_orders` question, two runs per N). The release image at the shipped
caps added two more figures.

| Concurrent turns | CLI container peak (MiB), of 512 | Most `claude` processes | Turn time (s) |
|---|---|---|---|
| idle | 17.1 | 0 | |
| 1 | 117.2, 136.3 | 1 | 19 to 23 |
| 2 | 235.3, 240.8; 271.3 at the shipped caps | 2 | 17 to 22 |
| 3 | 323.8, 323.9; **387.6** at cap 3 | 3 | 19 to 45 |
| 4 | 400.1, 416.7 | 4 (5 seen once: a CLI child process) | 22 to 28 |

The agent container stayed at 75 to 80 MiB throughout, and no container was OOM-killed. The worst
cost per CLI is about 127 MiB. **Set: `max_concurrent_turns: 2`** (the most turns whose highest
measured peak, 271 MiB, keeps a quarter of the 512 MiB limit free; 3 reached 388 MiB, 76 %) and
**`max_leases: 3`** (the most CLIs that fit under the limit at 127 MiB each; 4 would be about
525 MiB). A first choice of 3 and 4, from the probe's figures alone, was reversed when a later
run reached 388 MiB at 3. `test_the_concurrent_turn_cap_is_set_from_the_measurement` holds the
table and both rules. The turns were short Northwind turns; longer conversations may use more.

### Spend

LiteLLM's spend log for the session, read before `services down`: **$2.840703 over 1,412 rows**
(1,407 success; 5 failures at $0: 3 `support-model`, the access-denied and budget probes, and 2
`support-embedding`, the budget probe's retrieval). By alias:

| Alias | Rows | Spend |
|---|---|---|
| `support-model` (all through `/v1/messages`, `anthropic_messages`) | 697 | $2.583366 |
| `support-judge` | 248 | $0.256099 |
| `support-embedding` | 467 | $0.001239 |

By key: three release candidates $0.413098, $0.406763 and $0.413886; the `kind` deployment
(cluster evals and one invoke) $0.417576;
four judge runs $0.063431, $0.066017, $0.063928, $0.062724; three production deployments
$0.254734, $0.253552, $0.253645; the probes $0.171349. The conformance run uses fakes and made no
spend.

### Leak scan and teardown

- **Leak scan:** the sandbox (`HOME`, work and test directories, saved outputs), the session's
  task logs and this worktree were scanned for the two keys' values and their last 12 characters
  (22,877 files): **0 hits.** The LiteLLM master key was read inside a script for the
  `/key/update` calls and never printed.
- **Removed, in order:** the `kind` deployment (`remove --cluster`), the local production
  deployment (`remove --local`; each probe had been removed after its check), `cluster down`,
  `services down --purge`, the sandbox Docker context, the keys file and the kubeconfig. Then the
  `kind` network (left by `kind`) and the 54 image IDs not in the pre-run snapshot, each by exact
  ID; nothing was pruned.
- **Snapshot comparison** (00:49 UTC before, 02:37 UTC after): images (5, same IDs), volumes (1),
  networks (3, same IDs), containers (0), the default bridge's ID, and `docker system df`
  (images 20.16 GB, volume 66.26 MB, build cache 99 entries / 14.09 GB) are **identical**.

### For the owner

- Conformance for two-uid runtimes (#1199): needed before conformance can say anything about this
  runtime.
- Turn latency: p50 13 to 20 s against about 3.5 s for the kit runtimes, from a CLI process per
  turn. Recorded, not investigated.
- The D16 cap: 2 turns and 3 leases (above). Decision 3 (shared CLI uid) is unchanged.
- Still open from the merge report: deploy refusing `anthropic-messages` on gateways older than
  1.4.0, the chat `provider_specific_header` rule, `/v1/messages/count_tokens` unfiltered on 1.4.0,
  and decisions 1, 2, 3, 5, 6 and 7. `VERIFIED` / `RUNTIMES_RUN` not flipped.
