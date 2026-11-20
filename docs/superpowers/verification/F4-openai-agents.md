# #1083 F4 verification: `runtime/openai-agents@1.0.0`

Status: **built and unit-tested with a scripted model only. The acceptance (spec §15) has not
run.** No Docker image was built, nothing ran against a real gateway or model, `release` and `kind`
were not run, and arm64 was not run. The OpenAI Agents SDK is not called supported, and
`agentspec.VERIFIED` and `RUNTIMES_RUN` are unchanged. This lane did not hold the Docker slot.

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

## Not run (spec §15 and the §16.1 items marked F4)

The commands are in `tests/test_northwind_openai_agents_docker.py` (opt-in; it needs Docker and
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
