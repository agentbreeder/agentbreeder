# #1083 F5 verification: `runtime/deepagents@1.0.0` and Northwind on Deep Agents

Status: **built, not run end to end.** Nothing in this slice has run on Docker, `kind`, arm64 or
a real model. The runtime's own tests ran against real Deep Agents 0.7.32 behind the kit's real
loopback proxy with a scripted gateway, and the port builds `--locked` from Northwind's registry
with `docker build` replaced by a recorder. Every §15 acceptance item and every §16.1 item for F5
is written as an opt-in test and listed below as **not run**, with its command. Deep Agents is not
called supported; `agentspec.VERIFIED` and `RUNTIMES_RUN` are unchanged.

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

## Not run (spec §15 and §16.1 for F5), with the commands

The commands are in `tests/test_northwind_deepagents_docker.py`, which mirrors F3's and has never
run. Before it: `cd src/agentbreeder/examples/northwind && agentbreeder services up --environment
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

## Recommendation (owner decision)

Merge as "built, not run end to end", like F3 before its Docker run. Run the table above on the
Docker slot before Deep Agents is described anywhere as more than built. Flip `VERIFIED` and
`RUNTIMES_RUN` only after that run and the owner's call on conformance coverage (as for
LangGraph). arm64 needs an arm64 host either way.
