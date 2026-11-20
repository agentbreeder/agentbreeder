# #1083 F5 verification: `runtime/deepagents@1.0.0` and Northwind on Deep Agents

Status: **the acceptance ran on Docker and `kind` (2026-11-20), except arm64.** Northwind on
Deep Agents released 61/61 locally (twice; the second with two flaky cases that passed on their
retry) and 61/61 on `kind`. Conformance: 26 pass, 0 fail, 13 not run. Six of the test file's
seven tests passed on its first run (the `kind` test passed after `release` was re-run, below);
no product fix and no change to test logic were needed.
On real Gemini, the `task` sub-agent's calls counted against the turn's cap (including a cap
reached inside the sub-agent), the built-in file tools saw only the turn's state, and the four
unused vendor SDKs in the image could not reach out. macOS 13 x86_64; arm64 was not run (no
arm64 host). Deep Agents is not called supported, and `agentspec.VERIFIED` and `RUNTIMES_RUN` are
unchanged: owner decision.

The first half of this record ("What ran" to "The plan before the run") was written without
Docker. The second half, "Docker acceptance run", is the run.

Host: macOS 13 x86_64, Python 3.12.8. Issue #1088; spec
`docs/superpowers/specs/2026-11-16-multi-framework-agents-design.md` §8.3, §15, §16.

## What ran

| What | Where | Result |
|---|---|---|
| `runtime/deepagents@1.0.0`'s own tests against real Deep Agents 0.7.32 (LangGraph 1.2.19, LangChain 1.4.8, langchain-core 1.7.4, langchain-openai 1.7.3; macOS wheels of the locked versions, with `cryptography` 48.0.1 in place of the lock's 50.0.2, which has no macOS 13 x86_64 wheel), a scripted gateway behind the kit's real proxy, and fake memory | `runtime_tests/test_deepagents.py` | **16 passed** |
| Of those: a Northwind-shaped turn (system message is exactly the agent's prompt plus notes; tools offered are the agent's plus exactly the eight built-ins; both calls `main_loop: true`) | `test_a_northwind_shaped_turn` | passed |
| The `task` sub-agent (D10): 4 calls (main, sub, sub, main) in one turn's `model_calls`; rows `main_loop` true, false, false, true; the sub-agent offered no `task` and not given the agent's prompt or notes; its `lookup_order` call audited once with the server's customer | `test_the_task_sub_agent_counts_against_the_cap_and_its_tools_are_audited` | passed |
| The cap reached inside the sub-agent (`max_model_calls: 3`): the 3rd call (the sub-agent's) goes with `tool_choice: none`, the main agent's 4th is refused by the proxy and never sent, the turn fails `MODEL_STEP_LIMIT` | `test_a_cap_reached_inside_the_sub_agent_fails_the_turn` | passed |
| The step cap when the model ignores `tool_choice: none` (#1111): the Nth answer's tool never runs, `MODEL_STEP_LIMIT`, `step_limited: true` on step 2 | `test_the_step_cap_holds_when_the_model_ignores_tool_choice_none` | passed |
| A main-loop Nth answer in text is the reply | `test_a_main_loop_nth_answer_is_the_reply` | passed |
| Framework surface (§8.3), scripted model: no `execute` tool offered; `read_file /etc/passwd`, `ls /`, `glob /etc/*`, `grep root /etc` return no host content; `write_file` to a host path leaves no file | `test_file_tools_act_on_the_turns_state_only` | passed |
| A file written in one turn is not there in the next turn of the session | `test_files_written_in_one_turn_are_gone_in_the_next` | passed |
| A failing gateway: `MODEL_UNAVAILABLE` after the proxy's 3 tries, LangChain's retries off | `test_a_failing_gateway_is_a_coded_error_and_not_retried_by_langchain` | passed |
| Two concurrent customers keep their own customer in tool calls | `test_concurrent_sessions_keep_their_own_customer_and_notes_prompt` | passed |
| Bad tool arguments, an unparseable tool call, LangSmith variables removed, the manifest's reserved names equal the adapter's built-ins, knowledge before the call and `search_knowledge_base`, the instrumentor nested under the kit's root span with one TOOL span per call (the instrumentor's relabelled CHAIN) | the other runtime tests | passed |
| The vendored kit equals `src/agentbreeder/runtime_kit/` byte for byte (kit unchanged, 1.0.0) | `tests/test_runtime_kit_vendored.py` | passed |
| The Northwind port builds `--locked` from Northwind's registry: `agent.config.json` equal to the ADK file's as JSON apart from the name, artifacts byte for byte, deploy manifest `runtime: {ref: runtime/deepagents@1.0.0, profile: kit}`; `mcp:` refused; not offered by `agentbreeder example`; Northwind's functions take no reserved name | `tests/test_northwind_deepagents.py` | 7 passed |
| The lock resolves to identical pins and hashes for `x86_64` and `aarch64` manylinux_2_28 (uv 0.11.6, `--exclude-newer 2026-11-05`, 76 packages); the amd64 wheels install into a target directory with `--require-hashes --no-deps` (used for `modules.json`) | | done |
| The repo tests touched by the change (port, append-only and content hashes, runtime profile and `tool_check`, the console's runtime list and identity view, `agentspec`, example, packaging (opt-in builds skipped), `list` runtime) | 12 files | 513 passed, 8 skipped |

## A finding while building: the main-loop test needs the runtime's help

The proxy tells a main-loop call from a hidden one by the request: the turn's system prompt and
exactly the agent's tool set (§7.2.4). The kit sets that tool set to the agent's functions. Deep
Agents also offers its eight built-in tools on every main-loop call, so without help every Deep
Agents call would count as hidden: the cap still held (a misclassified call fails safe as
`MODEL_STEP_LIMIT`), but a main-loop Nth answer would not have been used as the reply when the
(N+1)th call was refused, and every `model_called` row would say `main_loop: false`.

The adapter adds a middleware to the main agent only, after Deep Agents' own (Deep Agents places new custom
middleware after its core middleware, and does not give it to the general-purpose sub-agent). On
each main-loop call it sets the turn's system prompt and records, in the proxy's state for the
turn, that call's final system text and tool names. The sub-agent's calls have their own system
prompt and no `task` tool, so they stay hidden. No kit change was needed: the adapter reaches the
proxy's state through the kit's turn context.

Also found: with 0.7.32, a `ChatOpenAI` model and no `system_prompt`, Deep Agents adds **no**
system-prompt text: the system message the scripted gateway received was the agent's prompt and
notes only (`BASE_AGENT_PROMPT` is deprecated since 0.7.0, and the file and sub-agent middleware
add a section only when given one). F0's "about 2.6k prompt tokens per call" is therefore its tool descriptions.

## The plan before the run (spec §15 and §16.1 for F5)

As written before the run; each row's outcome is in "Docker acceptance run" below. The
commands are in `tests/test_northwind_deepagents_docker.py`, which mirrors F3's. Before it: `cd src/agentbreeder/examples/northwind && agentbreeder services up --environment
dev --env-file ~/.env` (or a copy, named by `AGENTBREEDER_NORTHWIND_DIR`).

| Item | Command |
|---|---|
| `docker build` of the image, and `agentbreeder conformance` on it (§15.1); the kit-profile checks are still not built | `AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_DEEPAGENTS=1 python -m pytest -q tests/test_northwind_deepagents_docker.py -k conformance` |
| The step cap on a real Gemini alias through Deep Agents (§16.1's proxy rule, for this runtime) | `... -k step_cap` |
| Northwind 61/61 through `release`, real gateway and model, on the `service/litellm` image (§15.2) | `... -k release` |
| Multi-turn history without tool replay (§7.5, D5) for this runtime | with the release run |
| One invoke per Northwind tool (§15.3); Phoenix and the console's Traces view by hand | `... -k one_invoke` |
| Deep Agents' framework surface on the real model and container (§8.3, §10.3): `read_file /etc/passwd`, an `execute` request | `... -k file_tools` |
| The `task` sub-agent on the real model (D10): the turn stays within `max_model_calls`; the `main_loop: false` rows read by hand | `... -k task_sub_agent` |
| Cost against LiteLLM's spend log, to the cent (§15.4); expect about 2.6k more prompt tokens per call than LangGraph | by hand, after `release` |
| `MODEL_ACCESS_DENIED` and `MODEL_BUDGET_EXCEEDED` against the real gateway (§15.5) | by hand, as in F3 |
| `promote` to `kind` and its cluster evals; Kubernetes' memory-backed `/tmp` (§15.6, §16.1); the one invoke on `kind` by hand | `AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_DEEPAGENTS=1 AGENTBREEDER_KIND_TEST=1 python -m pytest -q tests/test_northwind_deepagents_docker.py -k kind` |
| Egress on the deploy network (§15.7; F1b) | with the conformance run, as in F3 |
| **arm64**: install and run the image on `linux/arm64` (§16.1, "arm64 for each later runtime") | the build and release above, on an arm64 host |
| Image size, start-up time and memory against the engine's limits (§16.1's first-pass-only figures; F0 measured 90 MB, 2.2 s imports, 99 MB, 10 pids) | with the conformance run (`docker stats`) |

## Docker acceptance run (2026-11-20)

### Setup

- Branch `feat/1088-f5-deepagents` at `8dadc168` (`e37710d5` merged with `main` at `596f1f99`;
  no conflicts, and `content_manifest.py --check` passed), exported with `git archive` and built
  with `python -m build --wheel` (`agentbreeder-3.0.0a3-py3-none-any.whl`, sha256 `59b74352…`).
  The commit that records the run changes only status text, this record, the test file's
  docstrings and `_content.json`. Installed with `[eval]` and pytest into a fresh Python 3.12.8 venv.
- Every command ran under `env -i` with a fresh `HOME`, `TMPDIR` in the sandbox, `USER=demo`,
  `PIP_NO_CACHE_DIR=1`, and a Docker context created in the sandbox `HOME` that pointed at the
  current context's socket. `kind` came from `~/.local/bin`. A 0600 file held only
  `GEMINI_API_KEY` and `TAVILY_API_KEY` from `~/.env`. It was deleted at the end.
- macOS 13 x86_64, Docker Desktop engine 27.5.1. Resource Saver was off.
- `agentbreeder example northwind`, then `services up --environment dev --env-file <keys> --yes`
  (`service/litellm@1.0.0`, Northwind's pin). The test file ran from the sandbox, outside the
  repository (so the wheel, not `src/`, was imported), with `AGENTBREEDER_NORTHWIND_DIR` set to
  that copy, unchanged from the branch.

### Results

| Item | Result |
|---|---|
| `docker build` (the normal build path, `build agent.yaml --stack ../northwind`) | **Built.** `sha256:930e9cf0…`, linux/amd64, **472 MB** (LangGraph 379 MB, OpenAI Agents 359 MB; the difference is mostly the unused vendor SDKs below). The second `release` rebuilt the same image ID. |
| `agentbreeder conformance` (§15.1, `-k conformance`) | **`incomplete`: 26 pass, 0 fail, 13 not run, 0 n/a**, the same set as LangGraph and OpenAI Agents (table below). Build and conformance took about 3 minutes. |
| Step cap on a real Gemini alias (§16.1, `-k step_cap`, `max_model_calls: 1`) | **PASS.** Gemini called a tool despite `tool_choice: none` (#1111). The turn failed **502 `MODEL_STEP_LIMIT`**, and no tool ran. Its one `model_called` row: `step_limited: true`, `finish_reason: tool_calls`, `main_loop: true`, $0.0031275. |
| Northwind 61/61 through `release` (§15.2, `-k release`), real gateway and Gemini | **PASS, 61/61, twice.** Run 1 (the full file): no flaky case, mean correctness 1.0, p50 3,543 ms. Run 2 (before `kind`, because the probes below had removed production): 61/61 with `flaky: [order-unknown-id, cancel-other-customers-order]` (each passed on its retry), p50 3,654 ms. Released `agent/northwind-support-deepagents@0.1.0`. |
| Multi-turn history without tool replay (§7.5, D5) | The multi-turn golden cases passed in both releases. **D5 needs no change for Northwind on Deep Agents.** |
| The main-loop test on a real model (§16.1) | **PASS.** Every `model_called` row of the test file's turns (122: step cap, release 1, tool invokes, surface and `task` tests) has `main_loop: true`. Across the session 354 rows are `true` and 5 are `false`; the 5 are exactly the sub-agent's calls in the `task` probes below. The adapter's middleware (first half) works on Gemini. |
| One invoke per Northwind tool (§15.3, `-k one_invoke`) | **PASS, first run.** All 7 ran: `lookup_orders`, `search_knowledge_base`, `web_search`, `escalate_to_human`, `save_customer_note`, `cancel_order` (two turns) and `get_product_catalog`. Each reply had `framework: deepagents` and `runtime: runtime/deepagents@1.0.0`, at **$0.0051 to $0.0063 a turn** (LangGraph $0.0030 to $0.0042): about 4,100 to 4,500 prompt tokens a call, from the eight built-in tools' descriptions, as F0 measured. |
| File tools on the real model (§8.3, §10.3, `-k file_tools`) | **PASS** as written, but Gemini refused both requests under Northwind's prompt without calling a tool (`tools: []`), so the test proved only that nothing leaked. The probe below covers the tools themselves. |
| `task` on the real model (D10, `-k task_sub_agent`) | **PASS** as written (2 calls, within 4), but Gemini answered with `lookup_orders` directly and did not use `task`. The probe below covers `task`. |
| **Probe: the built-in file tools never touch the container** (§8.3) | **PASS.** A probe deployment (the port with a test-only prompt, published to the sandbox registry only, that tells the model to call tools when asked; Gemini still refused requests that said "system file", so neutral wording was used). `write_file /notes/todo.md` then `ls /` in one turn: `['/notes/']`. The same file in the next turn of the session: `File '/notes/todo.md' not found`. `read_file /app/agent.config.json`, `read_file /etc/hostname`: not found; `glob **/*.py` from `/`: no files; `grep root /etc`: no matches. In the container those files exist (`/app/agent.config.json`, `/etc/hostname`, 8,955 `.py` files), and no `todo.md` was written anywhere on its filesystem. No `execute` tool was offered (the LLM span's tool list: the 8 built-ins and Northwind's 7). |
| **Probe: `task` counts against the cap on a real model** (D10) | **PASS.** With `max_model_calls: 4`, a `task` call whose sub-agent ran `lookup_orders`: 4 calls, `main_loop` true, false, false, true; the sub-agent's `lookup_orders` audited once (`ok: true`) with the server's customer; reply `model_calls: 4`, $0.0102666. A `task` call with no tool: 3 calls, true, false, true. **The cap reached inside the sub-agent** (`max_model_calls: 3`, the same request): calls 1 (main, `tool_calls`), 2 (sub, `tool_calls`), `lookup_orders`, 3 (sub, `stop`: `tool_choice: none` honoured), then the main agent's 4th refused by the proxy: **502 `MODEL_STEP_LIMIT`**. Same shape as the runtime test. |
| Phoenix (§15.3) | **Seen.** Project `northwind-support-deepagents`, read after the test file's run and before the
probes: 79 traces for its 80 turns. Each turn has an `AGENT` root (`agent_turn`), a `RETRIEVER` (`retrieve_knowledge`), one `LLM` span (`ChatOpenAI`) per model call, and **exactly one `TOOL` span per Northwind tool call**: TOOL counts equal the `tool:*` audit rows (18, 9, 5, 4, 4, 2, 2); the instrumentor's tool span is relabelled `CHAIN`. Deep Agents' built-in tools (`task`, `ls`, `write_file`, …) appear only as `CHAIN` spans, with no TOOL span and no audit row: they are the framework's, not the agent's tools. A `task` turn shows a `general-purpose` chain with the sub-agent's LLM spans. Failed turns (`MODEL_STEP_LIMIT`, `MODEL_BUDGET_EXCEEDED`) are traced with `ERROR`. The missing trace is the step-cap turn's: its deployment was removed within seconds (**#1185**, as in F4). The console's Traces view was **not opened**. |
| Cost against LiteLLM's spend log (§15.4) | **PASS, exactly.** Release candidate key: 101 `support-model` rows, $0.295298175, equal to the 101 `model_called` rows in that window. Production key (tool, surface and `task` invokes): 20 rows, $0.05695335 on both sides. Step-cap key: 1 row, $0.0031275 on both sides. The whole session, `kind` and probes included: **359 `model_called` rows, $0.903262, equal to the 359 successful `support-model` spend rows.** `support-embedding` is not in `cost_usd`, as in 1.3.0. |
| `MODEL_ACCESS_DENIED` live (§15.5) | **PASS.** The probe's own LiteLLM key changed to `models: [support-embedding]` (`/key/update`, master key from the stack's secret file, never printed): **502 `MODEL_ACCESS_DENIED`**; the `turn_failed` row has gateway 403 and LiteLLM's model-access message. The key was then restored. |
| `MODEL_BUDGET_EXCEEDED` live (§15.5) | **PASS.** Probe with `budget_usd: 0.0001`: call 1 cost $0.002709 and ran `lookup_orders`, call 2 was refused (LiteLLM 422 `Budget has been exceeded!`): **402 `MODEL_BUDGET_EXCEEDED`**. The next two turns got 402 before any model call. The error row carries the key alias and a masked key (`sk-[redacted]`). Same behaviour as ADK, LangGraph and OpenAI Agents. |
| Egress on the deploy network (§15.7) | **PASS (default deny), the same as LangGraph.** The production deployment (kit profile) sat on its `-int` network and the stack's `agents` network, both `internal: true`, with the forwarder sidecar. From inside: `1.1.1.1:443` and `8.8.8.8:53` network unreachable; `example.com`, `api.anthropic.com`, `generativelanguage.googleapis.com`, `api.openai.com` and `host.docker.internal` not resolved; `phoenix` and `ops` not resolved; `litellm:4000` and `postgres:5432` connected; `proxy` resolved, port 8080 refused. |
| **The unused vendor SDKs cannot reach out** (owner decision on #1176) | **PASS.** From inside the container, each SDK made a real request with a dummy key and no retries: `anthropic` (`messages.create`) → `APIConnectionError` caused by `[Errno -3] Temporary failure in name resolution`; `google-genai` (`models.generate_content`) → `httpx.ConnectError`, name resolution; `langchain-anthropic` (`ChatAnthropic.invoke`) → `AnthropicConnectionError`, name resolution; `langchain-google-genai` (`ChatGoogleGenerativeAI.invoke`) → `httpx.ConnectError`, name resolution. None of them is imported by the adapter. |
| `promote` to `kind` (§15.6, `-k kind`) | **PASS.** `cluster up --stack . --environment dev --yes`, then `promote agent.yaml --to kubernetes`: pushed to `localhost:5001`, **cluster evals 61/61**, no flaky case, p50 3,960 ms. (A first `-k kind` run alone failed `NO_LOCAL_RELEASE`, correctly: the probes had removed production. Re-running `release` first fixed it; no code change.) |
| Kubernetes `/tmp` (§16.1) | **PASS.** Pod spec: `emptyDir {medium: Memory, sizeLimit: 64Mi}` at `/tmp`; `/proc/mounts`: `tmpfs /tmp … size=65536k`; writing `/tmp` worked; the root filesystem is read-only (`touch /probe`: Read-only file system); `allowPrivilegeEscalation: false`, all capabilities dropped, limits 1 CPU and 512 Mi; no `OPENAI_*`, `ANTHROPIC_*`, `GOOGLE_*` or `GEMINI_*` variable in the pod. |
| One invoke on `kind` (by hand) | **PASS.** `agentbreeder forward ID --port 18080`, then POST `/invoke`: 200, `lookup_orders`, `framework: deepagents`, 2 model calls, $0.0057165, and its trace in Phoenix (one TOOL span). |
| Start-up and memory (Python 3.12 Linux image) | After 41 turns: **107.1 MiB of the 512 MiB limit**, 8 pids (`docker stats`; LangGraph 101.5 MiB, OpenAI Agents 113.5 MiB). The second production deployment, idle: 102.8 MiB. CPU limit 1, read-only root. Start-up time not measured. |
| **arm64** (§16.1) | **Not run**: this host is x86_64. The lock resolves to identical pins for aarch64 (first half). The image has not been built or run on arm64. |

### Conformance, against LangGraph (F3) and the ADK baseline (F2a)

| | pass | fail | not run | n/a |
|---|---|---|---|---|
| `runtime/deepagents@1.0.0`, profile `kit` | 26 | 0 | 13 | 0 |
| `runtime/langgraph@1.0.0`, profile `kit` (`F3-langgraph.md`) | 26 | 0 | 13 | 0 |
| `runtime/adk@1.3.0` and `1.5.0`, profile `v1` (`F2a-adk-baseline.md`) | 22 | 4 | 8 | 5 |

- The same 26 checks pass as on LangGraph, including the four checks that fail on ADK
  (`sessions.busy`, `traces.llm_span`, `model_errors.text_logs` and `model_errors.text_spans`);
  they pass here.
- **Not run (13), the same set as LangGraph:** `knowledge.sources`, `knowledge.extra_searches`,
  `tools.customer`, `tools.audit`, `sessions.notes`, `traces.tool_span` (no fixture agent);
  `cost.model_called_rows` (no audit-row read); `hygiene.egress` (no network log); and the 5
  kit-profile checks, which are not built: `serving.readiness`, `serving.sigterm_drain`,
  `step_cap.enforced`, `model_errors.swallowed` and `proxy.requests`. The run above covers some of
  these by hand (step cap, egress, one TOOL span per call, `model_called` rows). That is not the
  check passing.

### What the run found

- **No product bug.** Six of the seven tests passed on the first run with the file unchanged.
  The `kind` test, skipped in that run, then failed once with `NO_LOCAL_RELEASE` because the
  probes had removed production, and passed after `release` was re-run. Only docstrings changed.
- **Two opt-in tests are weaker than their names** on Gemini with Northwind's prompt: the model
  refuses the file-tool requests and does not choose `task`, so `test_file_tools_never_reach_the_container`
  and `test_task_sub_agent_on_the_real_model` pass without exercising the tools. Their docstrings
  now say so and point here; the probe above is the evidence. A future version could deploy the
  probe prompt from the test, if the owner wants that automated.
- **#1185 again:** the step-cap turn's trace is missing because `remove` kills the container
  before the span batch is flushed.
- `runtime/deepagents@1.0.0/manifest.yaml` still says "Not run end to end on a real stack". It is
  left unchanged on purpose: a published version is immutable (its hash is pinned), and
  LangGraph's and OpenAI Agents' manifests keep the same sentence. The docs, README, changelog and
  `CLAUDE.md` carry the run's status.

### Spend

LiteLLM's spend log for the session, read before `services down`: **$1.0920 over 803 rows**, of
which 797 are `success` and 6 are `failure` at $0: 1 on the access-denied probe's key and 5 on
the budget probe's key (4 of the 6 are `support-model` rows, hence 363 rows below against the 359
successful ones above). By alias:

| Alias | Rows | Spend |
|---|---|---|
| `support-model` | 363 | $0.903262 |
| `support-judge` | 185 | $0.187909 |
| `support-embedding` | 255 | $0.000874 |

By key:

| Key | Spend |
|---|---|
| Release candidate, run 1 | $0.295480 |
| Release candidate, run 2 | $0.268637 |
| `kind` deployment (cluster evals plus one invoke) | $0.200865 |
| Three judge runs | $0.064819, $0.063377 and $0.059713 |
| Four probe deployments (file tools and `task`, cap 3, access denied, budget) | $0.078873 |
| Production (tool, surface and `task` invokes) | $0.056990 |
| Step cap | $0.003291 |

The conformance run uses fakes and made no spend.

### Leak scan and teardown

- **Leak scan:** the saved outputs, the sandbox `HOME`, the sandbox work and test directories,
  the background task logs and this worktree were scanned for the two keys' values (4,649
  files): 0 hits. The LiteLLM master key was read into a shell variable for the two
  `/key/update` calls and never printed.
- **Removed, in order:** the `kind` deployment (`remove --cluster`), the local production
  deployment (`remove --local`; each probe had been removed after its check), `cluster down
  --stack .`, `services down --purge --yes`, the `kind` network (created 19:03 UTC, after the
  snapshot), then the 49 image IDs absent from the pre-run snapshot (taken 18:39 UTC with
  `docker images -a`), by ID: each was built in this run (10:45 to 11:01 PST) or a base image
  pulled for it (pgvector, LiteLLM, Phoenix, python, kindest/node, registry).
- **Compared with the snapshot after teardown:** image IDs (with `-a`), volumes, networks (by ID)
  and containers equal it; the default bridge ID is unchanged; `docker system df` is identical
  (5 images, 20.16 GB; 1 volume; build cache 99 entries, 14.09 GB). Nothing was pruned.

## Recommendation (owner decision)

Every §15 item that can run on this host passed, with no product fix and no change to test logic
(the `kind` test needed `release` re-run after the probes; release 2 had two flaky cases that
passed on retry), and the three F5-specific checks the owner asked for (the `task` sub-agent against the cap,
file tools never touching the host, the unused vendor SDKs unable to reach out) passed on real
Gemini through a probe deployment. Not covered: arm64; the 13 unbuilt conformance checks (5 of
them the kit profile's); the console's Traces view; start-up time.

Recommendation: merge (#1176). Flip `VERIFIED` and `RUNTIMES_RUN` for Deep Agents only together
with LangGraph and OpenAI Agents, if the owner accepts conformance at 26/39 with the by-hand
coverage above, or once the kit-profile checks are built. Otherwise keep it "built, run end to end
on macOS x86_64, not verified". arm64 needs an arm64 host either way.
