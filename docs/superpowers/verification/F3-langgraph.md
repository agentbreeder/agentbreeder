# #1083 F2b + F3 verification: the runtime kit and `runtime/langgraph@1.0.0`

Status: **unit-tested only. The acceptance (spec §15) has not been run.** Written 2026-11-19 on
a branch from `main` at 63a85cba, macOS 13 x86_64, without Docker (this lane did not hold the
Docker slot). LangGraph is not called supported, and `agentspec.VERIFIED` and `RUNTIMES_RUN` are
unchanged.

## What ran

| What | Where | Result |
|---|---|---|
| The kit's model-call rules against `runtime/adk@1.3.0`'s own `classify`, `retry_delay`, `retry_after`, `response_cost`, `response_fallback`, the tally and the error messages (taken from 1.3.0's `agent.py` and `server.py` by AST) | `tests/test_runtime_kit_parity.py` | 3,557 cases, all equal |
| The proxy on a real loopback socket against a scripted gateway: allow-list, no inbound header forwarded, 404 for other paths (`/v1/../key/generate`, query strings, absolute form), 401 for an unknown or finished token, 403 for another alias, `Transfer-Encoding` and a second `Content-Length` refused, body and response caps, keep-alive, retries and coded failures, the terminal-failure answer, SSE synthesis with the cost header read, and the step cap (tool-calling Nth answer withheld, (N+1)th refused, main-loop and hidden calls) | `tests/test_runtime_kit_proxy.py` | 50 passed |
| The turn: reply fields, tool audit rows, failing tools, argument checks, history as messages, notes, knowledge before the model call, `search_knowledge_base`'s cap, a swallowed model error still coded, the token forgotten, two concurrent customers | `tests/test_runtime_kit_turn.py` | 20 passed |
| The server: `/health` readiness, v1 request rules, 409 at once, coded errors, 504, the SIGTERM drain | `tests/test_runtime_kit_server.py` | 24 passed |
| The vendored copy is byte-identical to the source; the kit imports nothing from `agentbreeder` | `tests/test_runtime_kit_vendored.py` | passed |
| The Northwind port builds `--locked` from Northwind's registry, with the ADK file's `agent.config.json` (equal as JSON apart from the name) and artifacts (byte for byte); the deploy manifest carries `runtime: {ref, profile: kit}`; `mcp:` is refused | `tests/test_northwind_langgraph.py` | passed |
| `runtime/langgraph@1.0.0`'s own tests against real LangGraph 1.2.19 (macOS wheels of the locked versions), a scripted gateway and fake memory: a Northwind-shaped turn, the step cap through LangGraph when the model ignores `tool_choice: none`, a failing gateway tried 3 times (LangChain's retries off), two concurrent customers, bad tool arguments, LangSmith variables removed | `runtime_tests/test_langgraph.py` | 7 passed |
| The lock resolves to identical pins for `x86_64` and `aarch64` manylinux_2_28 (uv 0.11.6, `--exclude-newer 2026-11-05`); the amd64 wheels install into a target directory (used for `modules.json`) | | done |

## Not run (spec §15 and the §16.1 items marked F3)

Each needs Docker and a real stack. The commands are in `tests/test_northwind_langgraph_docker.py`.

| Item | Command |
|---|---|
| `docker build` of the image, and `agentbreeder conformance` on it (§15.1). The kit-profile checks are still `pending` in `conformance/checks.py` | `AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_LANGGRAPH=1 pytest tests/test_northwind_langgraph_docker.py -k conformance` |
| Northwind 61/61 through `release` against the real gateway and model (§15.2), on the `service/litellm` image (§16.1) | `... -k release` |
| One invoke per Northwind tool (all seven functions), Phoenix and the console's Traces view (§15.3) | `... -k one_invoke` (Phoenix and the console by hand) |
| Cost against LiteLLM's spend log, to the cent (§15.4) | by hand, after `release` |
| `MODEL_ACCESS_DENIED` and `MODEL_BUDGET_EXCEEDED` against the real gateway (§15.5) | by hand |
| **The step cap on a real Gemini alias** (§16.1) | `... -k step_cap` |
| `promote` to `kind` (its cluster evals); Kubernetes' memory-backed `/tmp` (§15.6, §16.1); the one invoke on `kind` by hand | `AGENTBREEDER_KIND_TEST=1 ... -k kind` |
| Egress on the deploy network (§15.7; F1b is on `main`) | with the conformance run |
| **arm64**: install and run the image on `linux/arm64` (§16.1) | the build and release above, on an arm64 host |
| Python 3.12 Linux image start-up time and memory against the engine's limits | with the conformance run |
| Multi-turn history: whether the 7 multi-turn golden cases pass without tool replay (§7.5, D5) | with the release run |
