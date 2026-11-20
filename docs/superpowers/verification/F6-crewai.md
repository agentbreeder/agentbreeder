# #1083 F6 verification: `runtime/crewai@1.0.0` (#1090)

Status: **built and unit-tested with a scripted model only. The acceptance (spec §15) has not
run.** No Docker image was built, nothing ran against a real gateway or model, `release`,
`conformance` and `kind` were not run, and arm64 was not run. CrewAI is not called supported, and
`agentspec.VERIFIED` and `RUNTIMES_RUN` are unchanged. This lane did not hold the Docker slot.

## What ran (2026-11-19, macOS 13 x86_64, no Docker)

| What | Where | Result |
|---|---|---|
| The lock resolves to identical pins and hashes for `x86_64` and `aarch64` manylinux_2_28 (uv 0.11.6, `--exclude-newer 2026-11-05`, `--only-binary :all:`): 144 packages, F0's set (`crewai` 1.15.34, `openinference-instrumentation-crewai` 1.1.22, `chromadb` 1.5.9, `lancedb` 0.41.0, `onnxruntime` 1.31.1, `openai` 2.54.0) plus the kit's packages | `runtime/requirements.lock` | done; the two outputs were byte-identical |
| The locked versions installed into a Python 3.12 venv with `--no-deps`, **except** `lancedb` and `onnxruntime` (no macOS 13 x86_64 wheel; only CrewAI's memory and RAG storage import them, as in F0's venv probe) and `cryptography` 50.0.2 (no macOS 13 wheel; 48.0.1 locally). The Linux image and the CI job `runtime-crewai` install the lock unchanged | | done |
| `runtime/modules.json` generated from the lock installed for `x86_64-manylinux_2_28` (`uv pip install --target`, every package) | `scripts/runtime_modules.py` | written; `--check` passes |
| `runtime/crewai@1.0.0`'s own tests against real CrewAI, the kit's real loopback proxy, a scripted gateway (httpx `MockTransport`) and fake memory | `runtime_tests/test_crewai.py` | 47 passed |
| The Northwind port builds `--locked` from Northwind's registry, with the ADK file's `agent.config.json` (equal as JSON apart from the name) and artifacts (byte for byte); the deploy manifest carries `runtime: {ref, profile: kit}`; `mcp:` is refused; the adapter's modules, `crewai` and `chromadb` are refused as tool module names | `tests/test_northwind_crewai.py` | passed |
| The vendored kit is byte-identical to `src/agentbreeder/runtime_kit/` (kit 1.0.0, unchanged) | `tests/test_runtime_kit_vendored.py` | passed |

What the runtime tests cover, each with CrewAI making the real requests:

- A Northwind-shaped turn through `Agent.kickoff_async`: the system message (CrewAI's role, the
  prompt with the customer's notes, the goal), history as `user`/`assistant` messages, the tool
  schema, the tool result with the server-side customer, the audit rows in order, cost, tokens and
  `served_by` from the gateway's answer.
- **§16.1 (F6), stub level: the main-loop test.** CrewAI wraps the prompt in its own system
  message, so with the kit's rule alone every call was classified hidden (seen on the first run).
  The adapter now gives the proxy the system message CrewAI's own prompt code builds for the
  turn's agent; every CrewAI call in the tests was then classified main-loop
  (`model_called.main_loop: true`), with and without knowledge. Not run on a real model.
- The step cap when the model ignores `tool_choice: none` (`MODEL_STEP_LIMIT`, the withheld tool
  never run), and the last allowed call's text as the reply when CrewAI finishes normally on it.
  The proxy's refused-(N+1)th path is covered by the kit's own tests, not through CrewAI.
- Three tool calls in one model answer run on CrewAI's worker threads into the kit's loop, each
  with the turn's customer and its own audit row.
- An empty model answer: CrewAI asks once more itself (a second counted model call), then raises;
  the adapter answers with the kit's fallback text, as LangGraph does, not a 500 (found in
  review). CrewAI's own "produced no final output" text is not used as a reply either.
- **§16.1 (F6), stub level: CrewAI's extra attempts.** On `Agent.kickoff_async`, a 503 gave 3
  tries by the proxy and no extra attempt by CrewAI. A 429 is where CrewAI's own retry loop
  (`run_with_rate_limit_retry`, 3 attempts, 1–8 s backoff) acts: with the loop's guard left off,
  CrewAI made 3 calls, the gateway saw 1, and the proxy answered the other 2 with the stored 429
  at once. The adapter sets the guard for the turn, so CrewAI makes 1 call. The proxy also answers
  `MODEL_STEP_LIMIT` with 429, which CrewAI's loop would otherwise retry with backoff.
- **§8.5, §13.7, §16.1 (F6): the task-output store, at stub level.** With a fresh `HOME` and temp
  directory, after a turn with unique markers: no `latest_kickoff_task_outputs.db`, no
  `crewai/credentials` directory, and no file under `HOME` or the temp directory holding the
  message, a tool argument or the reply. A real `Crew` built and kicked off in the same process
  keeps no task output and writes no database. The start-up sweep removes a database and a
  credentials directory left by an earlier run.
- **What `secret.key` holds:** a random Fernet key that `crewai_core.token_manager.TokenManager`
  makes to encrypt CrewAI's own platform access token (`tokens.enc`). It is made when CrewAI's
  trace listener asks whether the user is logged in to the CrewAI platform. It holds no
  AgentBreeder secret and no customer text. On this runtime's path (no `Crew`, tracing off) it was
  not created; the start-up sweep removes it if present.
- **Found: CrewAI prints the turn to stdout.** CrewAI's global event listener builds its console
  formatter with `verbose=True` whatever the agent says, so every run printed panels with the
  backstory (the customer's notes), tool arguments and output, and the answer; an event-pairing
  warning also printed through a `rich` console. The runtime silences the formatter, CrewAI's
  printer and `rich` console output, and checks it at start-up. With markers in the message, a
  tool argument, the answer and a gateway error, stdout and stderr held only the kit's coded log
  line.
- **Found in review: CrewAI's "better compaction" (on by default) spills large tool results.** A
  tool result over about 7 KB (CrewAI assumes an 8,192-token window for an unknown model) was
  written to `/tmp/crewai-tool-results-*/…/result.md` (a fixed `/tmp`, not `TMPDIR`) for the
  turn, and CrewAI gave the model an undeclared `read_a_deferred_tool_result` tool, so the agent's
  next step was recorded as a hidden call. The agent now has `use_better_compaction=False`; a test
  with a 9 KB tool result finds no such directory, the declared tool set on every call and every
  step main-loop, and fails with the setting removed. The start-up sweep removes any leftover
  directory. Start-up also checks the retry guard, the compaction settings, CrewAI's system-prompt
  code, and that tracing and telemetry are off; Python warnings are ignored in the entry point.
- **Accepted, self-only:** CrewAI substitutes `{tools}` and `{tool_names}` into the user message
  after the customer's text is in it, so a customer who writes `{tools}` gets the tool schema
  text in their own message. The model already has the schemas; no other customer's text is
  involved. Not changed.
- **Found: CrewAI caches tool results by arguments by default.** A cached result skips the audit
  row and could hand one customer another customer's result. The agent's cache and each tool's
  cache function are off; two customers asking for the same order each ran the tool.
- Two concurrent customers keep their own customer in tool calls.
- Bad arguments: an `ok: false` row and the kit's error text to the model (CrewAI's own pydantic
  check is bypassed so the kit audits the refusal); strict mode's `null` for an optional argument
  is dropped, so the function's default applies.
- **F4's finding (#1175):** a call to a tool the agent lacks, and a tool call whose arguments are
  not JSON, are answered to the model by CrewAI; nothing runs, the turn goes on, no 500.
- A model alias the agent does not declare: `MODEL_ACCESS_DENIED`.
- A customer note holding `{input}` does not put the customer's message into the system prompt.
- The entry point removes every `CREWAI_*` and `OPENAI_*` variable and sets the three telemetry
  switches before CrewAI is imported; `OTEL_SDK_DISABLED` is not set.
- With a Python audit hook on `socket.connect`, a turn connected only to `127.0.0.1` (the gateway
  here is a `MockTransport`, so this shows CrewAI's telemetry and tracing opened nothing).
- Knowledge before the call and `search_knowledge_base`.
- The instrumentor: its spans share the kit's trace, its TOOL spans are exported as CHAIN (one
  TOOL span per call, the kit's).
- The JSON Schema to pydantic generator: types, descriptions, enums, required and defaults kept;
  `$ref`, `anyOf`/`oneOf`/`allOf`, tuple `items`, type lists, nested objects with properties and
  unusable property names refused; every Northwind tool converts.

## Not run (spec §15 and the §16.1 items marked F6)

The commands are in `tests/test_northwind_crewai_docker.py` (opt-in; it needs Docker and the
Northwind stack up with real keys):

| Item | Command |
|---|---|
| `docker build` of the image, and `agentbreeder conformance` on it (§15.1) | `AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_CREWAI=1 python -m pytest -q tests/test_northwind_crewai_docker.py -k conformance` |
| The step cap on a real Gemini alias, through CrewAI | `... -k step_cap` |
| Northwind 61/61 through `release` against the real gateway and model (§15.2), including the 7 multi-turn cases without tool replay (§7.5, D5) | `... -k release` |
| One invoke per Northwind tool (§15.3); Phoenix and the console's Traces view by hand | `... -k one_invoke` |
| **The task-output store on the real image** (§8.5, §13.7, §16.1): after a turn with a marker, no file under the container's `/tmp` holds it, no `*.db`, `secret.key` or `tokens.enc`, no `CREWAI_*`/`OPENAI_*` variable in the container, and neither the marker nor a console panel in `docker logs` | `... -k no_file_in_the_container` |
| Cost against LiteLLM's spend log, to the cent (§15.4) | by hand, after `release` |
| `MODEL_ACCESS_DENIED` and `MODEL_BUDGET_EXCEEDED` against the real gateway (§15.5) | by hand |
| `promote` to `kind`, Kubernetes' memory-backed `/tmp`, one invoke on `kind` (§15.6) | `AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_CREWAI=1 AGENTBREEDER_KIND_TEST=1 python -m pytest -q tests/test_northwind_crewai_docker.py -k kind` |
| Egress on the deploy network (§15.7) | with the conformance run, once its network log exists |
| The main-loop test on a real model (§16.1) | the `model_called` rows of the release run (`main_loop`) |
| CrewAI's extra attempts against the real gateway (§16.1) | a 429 from the real gateway (a tiny `rpm`) during the release run, read from the proxy's `model_called` rows and the gateway's log |
| **arm64**: install and run the image on `linux/arm64` (§16.1) | the build and release above, on an arm64 host |
| Image size, start-up time and memory against the engine's limits (F0: 365 MB, 4.6 s imports, 178 MB and 18 pids for two sessions) | with the conformance run |
| The console's Build ▸ framework list showing CrewAI (Playwright, `tests/ui/test_builder_framework.py`) | CI `console-ui`; not run locally |

## Supply chain

- `pip-audit --no-deps -r runtime/requirements.lock`: **4 advisories, all in `chromadb` 1.5.9,
  none with a fixed version** (GitHub advisory severities: two critical, one of them CVSS 10, and
  two high, CVSS 8.8): CVE-2026-45829 (pre-auth code injection through the collections
  endpoint with `trust_remote_code`), CVE-2026-45833 (the same, authenticated), CVE-2026-45831 and
  CVE-2026-45830 (tenant checks in its RBAC provider and collection authorisation). All four are in
  ChromaDB's HTTP **server**. `crewai` depends on `chromadb`, and importing the runtime imports it
  as a library (checked: it is in `sys.modules` after start-up; `lancedb`, `onnxruntime`,
  `kubernetes` and `mcp` are not). No ChromaDB server is started, no port is opened for it, and
  CrewAI's memory and RAG (its only users) are off. **The owner accepted the four (2026-11-20)
  as a known security exception**, recorded in `CLAUDE.md` ("Known security exceptions") and
  scoped to those four IDs and this lock; re-check when chromadb ships a fix. The image still
  carries ChromaDB's server code (the `chromadb.server` package and the `chroma` CLI in
  `chromadb_rust_bindings`), installed and unused. Guards: `tests/test_crewai_chromadb_exception.py`
  (the exception's scope; the lock still pins 1.5.9; no other lock carries chromadb; the image
  exposes only 8080 and runs only `server.py`; no runtime source names a ChromaDB server, CLI or
  client) and the runtime test `test_chromadb_is_a_library_only_no_server_client_or_port`
  (across start-up and a turn on real CrewAI: no server module loaded, the CLI, clients and
  systems never called, no process started apart from Python's own `uname`/`file` platform probe,
  only loopback binds; three negative controls fail it).
- bandit on `runtime/`: 0 high; 6 medium, all in the vendored kit (the same as LangGraph's copy).

## Notes for the run

- **Deviations from spec §8.5 (accepted by the owner, 2026-11-20):** no `Crew`/`Task` per turn but
  `Agent.kickoff_async` with structured history (§7.5 forbids history pasted into a prompt, and a
  `Task` takes only text; with no `Crew`, the task-output writer is never made); the
  JSON Schema to pydantic generator lives in the runtime (`crewai_schema.py`), not the kit, so the
  kit stays 1.0.0 and no other kit runtime needs a new version; CrewAI's retry loop is switched off
  through its own re-entrancy guard, a private name.
- CrewAI makes no planning, memory, summarising or formatting call on this runtime's path. It does
  make extra calls in three cases, each counted by the proxy's cap: an empty answer is asked again
  once; after 5 identical (tool, arguments, result) calls in a row it forces a final answer with a
  call that carries no tools (the reviewer reproduced this with a scripted model: 6 calls); and at
  `max_iter` it forces a final answer, which the proxy's cap reaches first (`max_iter` = cap + 2).
- CrewAI logs a WARNING per call about approximate token counts for an unknown model; the adapter
  sets that logger to ERROR. CrewAI's other `logging` records are quieted by the kit's
  `logs.quiet` (one redacted line each); Python warnings are ignored in the entry point.
- Northwind's `turns` grant (F3) already covers this runtime's history read.
