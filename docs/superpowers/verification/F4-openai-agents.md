# #1083 F4 verification: `runtime/openai-agents@1.0.0`

Status: **the acceptance ran on Docker and `kind` (2026-11-20), except arm64.** Northwind on the
OpenAI Agents SDK released 61/61 locally and 61/61 again on `kind`. Conformance: 26 pass, 0 fail,
13 not run (the same as LangGraph). The run found no product bug in F4's files and needed no test
change. It found one bug outside them, in the shared local `remove`, which loses a turn's spans
when a deployment is removed within seconds of the turn (#1185). macOS 13 x86_64; arm64 was not run
(no arm64 host). The OpenAI Agents SDK is not called supported, and `agentspec.VERIFIED` and
`RUNTIMES_RUN` are unchanged: owner decision.

The first half of this record ("What ran") was written without Docker. The second half,
"Docker acceptance run", is the run.

## What ran (2026-11-19, macOS 13 x86_64, no Docker)

| What | Where | Result |
|---|---|---|
| The lock resolves to identical pins and hashes for `x86_64` and `aarch64` manylinux_2_28 (uv 0.11.6, `--exclude-newer 2026-11-05`, `--only-binary :all:`): 58 packages | `runtime/requirements.lock` | done; the two outputs were byte-identical |
| The locked versions installed into a Python 3.12 venv with `--require-hashes`, **except** `cryptography` 50.0.2 (and its `cffi`, `pycparser`), which has no macOS 13 x86_64 wheel; `cryptography` 48.0.1 was installed instead for the local run only. The Linux image installs the lock unchanged (CI job `runtime-openai-agents` runs on ubuntu) | | done |
| `runtime/modules.json` generated from that venv's `site-packages` | `scripts/runtime_modules.py` | written; `--check` passes |
| `runtime/openai-agents@1.0.0`'s own tests against the real SDK (`openai-agents` 0.24.0, `openai` 3.42.1, `openinference-instrumentation-openai-agents` 2.7.0), the kit's real loopback proxy, a scripted gateway (httpx `MockTransport`) and fake memory | `runtime_tests/test_openai_agents.py` | 17 passed |
| The Northwind port builds `--locked` from Northwind's registry, with the ADK file's `agent.config.json` (equal as JSON apart from the name) and artifacts (byte for byte); the deploy manifest carries `runtime: {ref, profile: kit}`; `mcp:` is refused; `agents`, `mcp`, `kit` and the adapter's module are refused as tool module names | `tests/test_northwind_openai_agents.py` | passed |
| The vendored kit is byte-identical to `src/agentbreeder/runtime_kit/` | `tests/test_runtime_kit_vendored.py` | passed |

What the runtime tests cover, each with the SDK making the real requests:

- A Northwind-shaped turn: Chat Completions with the system prompt (with notes) first, history as
  messages, the declared schema not made strict, the tool result, the audit rows in order, cost,
  tokens and `served_by` from the gateway's answer.
- **§16.1 (F4), at stub level: the main-loop test.** Every call the SDK made in a turn was
  classified main-loop by the proxy (`model_called.main_loop: true`): the SDK sends the
  instructions as the first `system` message and exactly the agent's tools. Not run on a real
  model.
- The step cap when the model ignores `tool_choice: none` (`MODEL_STEP_LIMIT`, the withheld
  tool never run), and the Nth answer used as the reply when the model honours it.
- A failing gateway: 3 tries by the proxy, none by the OpenAI client (`max_retries=0`).
- Two concurrent customers keep their own customer in tool calls.
- Bad and unparseable tool arguments: an `ok: false` row and an answer to the model.
- A call to a tool the agent lacks runs nothing and is answered to the model.
- With the model always calling a tool, the proxy's cap ends the turn before the SDK's
  `max_turns` backstop.
- The agent has only the kit's functions (no built-in tool, handoff or MCP server).
- The entry point removes every `OPENAI_*` variable.
- Knowledge before the call and `search_knowledge_base`.
- The instrumentor with `exclusive_processor=True` leaves no SDK batch exporter registered; its
  spans share the kit's trace, and its TOOL spans are exported as CHAIN (one TOOL span per call,
  the kit's).
- **§16.1 (F4), in a venv only: no `OPENAI_API_KEY` at all.** With every `OPENAI_*` variable
  removed and a Python audit hook on `socket.connect`, a turn and a flush of the SDK's trace
  processors connected only to `127.0.0.1` (the kit's proxy), both without the instrumentor (the
  SDK's exporter skips its upload with no key) and with it. This is not the conformance egress
  check on the deploy network, which waits for the network log (F1b).
- The same with a fake `OPENAI_API_KEY` set after import and the instrumentor installed: still only
  `127.0.0.1`, so the exclusive processor, not the missing key, keeps traces off OpenAI.

## The plan before the run (spec §15 and the §16.1 items marked F4)

As written before the run; each row's outcome is in "Docker acceptance run" below. The commands
are in `tests/test_northwind_openai_agents_docker.py` (opt-in; it needs Docker and
the Northwind stack up with real keys):

| Item | Command |
|---|---|
| `docker build` of the image, and `agentbreeder conformance` on it (§15.1) | `AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_OPENAI_AGENTS=1 python -m pytest -q tests/test_northwind_openai_agents_docker.py -k conformance` |
| The step cap on a real Gemini alias, through this SDK | `... -k step_cap` |
| Northwind 61/61 through `release` against the real gateway and model (§15.2), including the 7 multi-turn cases without tool replay (§7.5, D5) | `... -k release` |
| One invoke per Northwind tool (§15.3); Phoenix and the console's Traces view by hand | `... -k one_invoke` |
| No `OPENAI_*` variable in the deployed container (§16.1, in part) | `... -k openai_variable` |
| Cost against LiteLLM's spend log, to the cent (§15.4) | by hand, after `release` |
| `MODEL_ACCESS_DENIED` and `MODEL_BUDGET_EXCEEDED` against the real gateway (§15.5) | by hand |
| `promote` to `kind`, Kubernetes' memory-backed `/tmp`, one invoke on `kind` (§15.6) | `AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_OPENAI_AGENTS=1 AGENTBREEDER_KIND_TEST=1 python -m pytest -q tests/test_northwind_openai_agents_docker.py -k kind` |
| Egress on the deploy network, with no `OPENAI_API_KEY` (§15.7, §16.1) | with the conformance run, once its network log exists |
| The main-loop test on a real model (§16.1) | the `model_called` rows of the release run (`main_loop`) |
| **arm64**: install and run the image on `linux/arm64` (§16.1) | the build and release above, on an arm64 host |
| Image size, start-up time and memory against the engine's limits (F0: 73 MB, 4.1 s imports, 100 MB, with 48 packages; this lock has 58) | with the conformance run |

## Notes for the run

- The SDK 0.24.0 depends on `mcp` 2.3.0, which the lock resolves with `starlette`, `uvicorn`,
  `cryptography` and `pyjwt`, ten packages more than F0's 48. The runtime never starts an MCP client
  or server; `import agents` imports `mcp` (its server modules included).
- Northwind's `turns` grant (F3) already covers this runtime's history read.

## Docker acceptance run (2026-11-20)

### Setup

- Branch `feat/1086-f4-openai-agents` at `35972460` (`25d082c4` merged with `main` at `596f1f99`;
  the generated files still checked), exported with `git archive` and built with
  `python -m build --wheel` (`agentbreeder-3.0.0a3-py3-none-any.whl`, sha256 `06ef0848…`). The
  later commit changes only status text, this record and `_content.json`. Installed with `[eval]`
  and pytest into a fresh Python 3.12.8 venv.
- Every command ran under `env -i` with a fresh `HOME`, `TMPDIR` in the sandbox, `USER=demo`,
  `PIP_NO_CACHE_DIR=1`, and a Docker context created in the sandbox `HOME` that pointed at the
  current context's socket. `kind` came from `~/.local/bin`. A 0600 file held only `GEMINI_API_KEY`
  and `TAVILY_API_KEY` from `~/.env`. It was deleted at the end.
- macOS 13 x86_64, Docker Desktop engine 27.5.1. Resource Saver was off.
- `agentbreeder example northwind`, then `services up --environment dev --env-file <keys> --yes`
  (`service/litellm@1.0.0`, Northwind's pin). The test file ran from the sandbox with
  `AGENTBREEDER_NORTHWIND_DIR` set to that copy, unchanged from the branch.

### Results

| Item | Result |
|---|---|
| `docker build` (the normal build path, `build agent.yaml --stack ../northwind`) | **Built.** `sha256:17400c4a…`, linux/amd64, **359 MB** (LangGraph: 379 MB). The step-cap build and the probe build had the same image ID. |
| `agentbreeder conformance` (§15.1, `-k conformance`) | **`incomplete`: 26 pass, 0 fail, 13 not run, 0 n/a.** Build and conformance took 177 s (table below). |
| Step cap on a real Gemini alias (§16.1, `-k step_cap`, `max_model_calls: 1`) | **PASS.** Gemini called a tool despite `tool_choice: none` (#1111). The turn failed with **`MODEL_STEP_LIMIT`** and no tool ran. Its one `model_called` row: `step_limited: true`, `finish_reason: tool_calls`, `main_loop: true`, $0.001323. |
| Northwind 61/61 through `release` (§15.2, `-k release`), real gateway and Gemini | **PASS, 61/61 on the first run.** No flaky case, no failures, mean correctness 1.0, p50 3,945 ms. Released `agent/northwind-support-openai-agents@0.1.0`. |
| Multi-turn history without tool replay (§7.5, D5) | The multi-turn golden cases passed. **D5 (text-only replay) needs no change for Northwind on this SDK.** |
| The main-loop test on a real model (§16.1) | **PASS.** All **117** `model_called` rows of the session (step cap, release, tool invokes) have `main_loop: true`. The SDK made no call the proxy classified as hidden. |
| One invoke per Northwind tool (§15.3, `-k one_invoke`) | **PASS, first run.** All 7 tools ran: `lookup_orders`, `search_knowledge_base`, `web_search`, `escalate_to_human`, `save_customer_note`, `cancel_order` (two turns) and `get_product_catalog`. Each reply had `framework: openai-agents` and `runtime: runtime/openai-agents@1.0.0`, at $0.0030 to $0.0042 a turn. |
| No `OPENAI_*` in the deployed container (§16.1, `-k openai_variable`) | **PASS.** The released container's environment has no `OPENAI_*` variable. Its `AGENTBREEDER_*` names are `DEPLOYMENT_ID`, `GATEWAY_URL`, `SERVICE_TOOLS_PROXY_URL` and `SERVICE_TRACES_URL`. |
| Phoenix (§15.3) | **Seen.** Phoenix's database has the project `northwind-support-openai-agents`. Each turn has an `AGENT` root (`agent_turn`), a `RETRIEVER` (`retrieve_knowledge`), the SDK's agent spans and one `LLM` span (`generation`) per model call. **Exactly one `TOOL` span per tool call**: TOOL counts equal the `tool:*` audit rows (15, 8, 7, 3, 3, 2, 2). The SDK's own function span is relabelled `CHAIN`. Failed turns (403, 422) are traced with `ERROR` status. The SDK's agent span appears as two `AGENT` spans and one `CHAIN` per turn: noise, not a defect. The console's Traces view was **not opened**. One gap, below (#1185). |
| Cost against LiteLLM's spend log (§15.4) | **PASS, exactly, on all three keys.** Production key: 16 `support-model` spend rows, $0.027912; 16 `model_called` rows for the tool invokes, $0.027912. Release candidate: 100 rows, $0.162255 on both sides. Step-cap key: 1 row, $0.001323 on both sides. The `support-embedding` rows (for example 9 rows, $0.00002, on the production key) are not in `cost_usd`, as in 1.3.0. |
| `MODEL_ACCESS_DENIED` live (§15.5) | **PASS.** A probe deployment's own LiteLLM key changed to `models: [support-embedding]` (`/key/update`, master key from the stack's secret file, not printed) gave **502 `MODEL_ACCESS_DENIED`**. The audit row has gateway 403 and LiteLLM's model-access message. The key was then restored. |
| `MODEL_BUDGET_EXCEEDED` live (§15.5) | **PASS.** Probe with `budget_usd: 0.0001`. In the first turn, call 1 cost $0.001365 and ran `lookup_orders`, then call 2 was refused (LiteLLM 422 `Budget has been exceeded!`): **402 `MODEL_BUDGET_EXCEEDED`**, `retry_after_s: null`. The next three turns got 402 before any model call. Retrieval fell back to keyword-only, because the embedding call is refused too. Same behaviour as ADK and LangGraph. |
| Egress on the deploy network (§15.7, §16.1: no `OPENAI_API_KEY`) | **PASS (default deny), the same as LangGraph.** The probe (kit profile) sat on its `-int` network and the stack's `agents` network, both internal. From inside: `1.1.1.1:443` and `8.8.8.8:53` network unreachable. `example.com`, **`api.openai.com`**, `generativelanguage.googleapis.com` and `host.docker.internal` not resolved. `phoenix` and `ops` not resolved. `litellm:4000` and `postgres:5432` connected. `proxy` resolved, but port 8080 was refused. |
| `promote` to `kind` (§15.6, `-k kind`) | **PASS.** `cluster up --stack . --environment dev --yes`, then `promote agent.yaml --to kubernetes`: pushed to `localhost:5001`, **cluster evals 61/61**, no flaky case, p50 4,386 ms, 217 s. |
| Kubernetes `/tmp` (§16.1) | **PASS.** Pod spec: `emptyDir {medium: Memory, sizeLimit: 64Mi}` at `/tmp`. `/proc/mounts` shows `tmpfs /tmp … size=65536k`. Writing to `/tmp` worked. The root filesystem is read-only (`touch /probe`: Read-only file system). `allowPrivilegeEscalation: false`, all capabilities dropped, limits 1 CPU and 512 Mi. No `OPENAI_*` variable in the pod. |
| One invoke on `kind` (by hand) | **PASS.** `agentbreeder forward ID --port 18080`, then POST `/invoke`: 200, `lookup_orders`, `framework: openai-agents`, 2 model calls, $0.00363075, and its full trace in Phoenix. |
| Start-up and memory (Python 3.12 Linux image) | Idle after 5 turns: **113.5 MiB of the 512 MiB limit** (`docker stats`; LangGraph: 101.5 MiB). CPU limit 1, read-only root. Start-up time not measured. |
| **arm64** (§16.1) | **Not run**: this host is x86_64. The lock resolves to identical pins for aarch64 (first half). The image has not been built or run on arm64. |

### Conformance, against LangGraph (F3) and the ADK baseline (F2a)

| | pass | fail | not run | n/a |
|---|---|---|---|---|
| `runtime/openai-agents@1.0.0`, profile `kit` | 26 | 0 | 13 | 0 |
| `runtime/langgraph@1.0.0`, profile `kit` (`F3-langgraph.md`) | 26 | 0 | 13 | 0 |
| `runtime/adk@1.3.0` and `1.5.0`, profile `v1` (`F2a-adk-baseline.md`) | 22 | 4 | 8 | 5 |

- The same 26 checks pass as on LangGraph. These include the four that ADK fails:
  `sessions.busy`, `traces.llm_span`, `model_errors.text_logs` and `model_errors.text_spans`.
- **Not run (13), the same set as LangGraph:**
  - `knowledge.sources`, `knowledge.extra_searches`, `tools.customer`, `tools.audit`,
    `sessions.notes` and `traces.tool_span` (no fixture agent);
  - `cost.model_called_rows` (no audit-row read);
  - `hygiene.egress` (no network log);
  - the 5 kit-profile checks, which are not built: `serving.readiness`, `serving.sigterm_drain`,
    `step_cap.enforced`, `model_errors.swallowed` and `proxy.requests`.

  The run above covers some of these by hand (step cap, egress, one TOOL span per call,
  `model_called` rows). That is not the check passing.

### What the run found: a lost trace on `remove` (#1185, not F4's files)

The step-cap turn's audit rows are in `audit_events`, but its trace (`9c1161cf…`) has **no spans
in Phoenix**. Reproduced on the probe: a turn, then `remove` within seconds, then a 10 s wait left
0 spans. Without the remove, the same turns are exported, failed ones included.

Cause: local `remove` runs `docker container rm --force --volumes` (`src/agentbreeder/runtime.py`).
That is a SIGKILL, so the `BatchSpanProcessor`'s queue (up to its 5 s delay) is never flushed, and
the kit's SIGTERM drain never runs on a local remove. The code is shared, so this probably applies
to every runtime. It is not a bug in F4's files and it blocks no acceptance item, so it is recorded
here and filed as #1185, with no fix in this PR. One consequence: `serving.sigterm_drain` can only
be tested by a check that sends SIGTERM itself, not through `remove`.

### Spend

LiteLLM's spend log for the session, read before `services down`: **$0.4824 over 510 rows**. That
is 500 `success` rows and 10 `failure` rows at $0, the probe's refused calls. By alias:

| Alias | Rows | Spend |
|---|---|---|
| `support-model` | 226 | $0.360205 |
| `support-judge` | 122 | $0.121673 |
| `support-embedding` | 162 | $0.000550 |

By key:

| Key | Spend |
|---|---|
| Release candidate | $0.162436 |
| `kind` deployment (cluster evals plus one invoke) | $0.167533 |
| Two judge runs | $0.060989 and $0.060685 |
| Production (tool invokes) | $0.027932 |
| Step cap | $0.001487 |
| Probe | $0.001367 |

The conformance run uses fakes and made no spend.

### Leak scan and teardown

- **Leak scan:** the saved outputs, the sandbox `HOME` and the worktree's `docs/`, `tests/` and
  examples were scanned for the two keys' values: 0 hits.
  - The LiteLLM master key was read into a shell variable for the two `/key/update` calls and
    never printed.
  - A gateway error in a `turn_failed` audit row includes the key alias and a masked key
    (`sk-[redacted]`), as for ADK and LangGraph.
- **Removed, in order:**
  1. The `kind` deployment (`remove --cluster`), then `cluster down --stack .`.
  2. The production deployment and the probe (`remove --local`).
  3. `services down --purge --yes`.
  4. The `kind` network, created 17:20 UTC, after the snapshot.
  5. The 32 image IDs absent from the pre-run snapshot (taken 16:52 UTC with `docker images -a`),
     by ID. Each was either built in this run (09:02 to 09:06 PST) or a base image pulled for it
     (pgvector, LiteLLM, Phoenix, python, kindest/node, registry).
- **Compared with the snapshot after teardown:**
  - Image IDs (with `-a`), volumes, networks (by ID) and containers equal it.
  - The default bridge ID is unchanged.
  - Nothing was pruned. The build cache grew (11.69 GB to 14.09 GB private).
  - `docker system df` reports 20.16 GB for the same 5 images, against 18.07 GB before. The image
    IDs are identical, and the cause (containerd snapshotter accounting) was not established.
- **Another session's container:** `cloud-hmac-pg` (`postgres:16-alpine`, an image in the
  snapshot) was created by another session at 16:59 UTC, during this run. It was gone before
  teardown. This run did not touch it.

### Recommendation (owner decision)

Every §15 item that can run on this host passed on the first run, with no product fix and no test
change. Not covered: arm64; the 13 unbuilt conformance checks (5 of them the kit profile's); the
console's Traces view; start-up time.

Recommendation: **merge #1175**. Flip `VERIFIED` and `RUNTIMES_RUN` for the OpenAI Agents SDK
only together with LangGraph, if the owner accepts conformance at 26/39 with the by-hand coverage
above, or once the kit-profile checks are built. Otherwise keep it "built, run end to end on macOS
x86_64, not verified". arm64 needs an arm64 host either way. #1185 is independent of this
decision.

The runtime's own `manifest.yaml` description still ends "Not run end to end on a real stack
yet." It was left unchanged on purpose: it is part of the immutable `runtime/openai-agents@1.0.0`
content (`builtin_hashes.json`), and editing it would change the runtime this run built and tested.
A later runtime version can drop the sentence.
