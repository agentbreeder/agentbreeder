# #1083 F6 verification: `runtime/crewai@1.0.0` (#1090)

Status: **the acceptance ran on Docker and `kind` (2026-11-20), except arm64.** Northwind on
CrewAI released 61/61 locally (four times) and 61/61 again on `kind`. Conformance: 26 pass, 0 fail,
13 not run, after one fix: the first run failed `traces.llm_span`, because CrewAI's instrumentor
writes no LLM span and neither did the adapter (spec §12 asks for one per call); the adapter now
writes them (below). macOS 13 x86_64; arm64 was not run (no arm64 host). CrewAI is not called
supported, and `agentspec.VERIFIED` and `RUNTIMES_RUN` are unchanged: owner decision.

The first half of this record ("What ran") was written without Docker. The second half,
"Docker acceptance run", is the run.

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

## The plan before the run (spec §15 and the §16.1 items marked F6)

As written before the run; each row's outcome is in "Docker acceptance run" below. The commands
are in `tests/test_northwind_crewai_docker.py` (opt-in; it needs Docker and the Northwind stack up
with real keys):

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
- bandit on `runtime/`: 0 high; 7 medium: 6 in the vendored kit (the same as LangGraph's copy)
  and one B108 in `crewai_confine.py`, the start-up sweep of the fixed `/tmp` path CrewAI's
  compaction writes to (`noqa: S108`). An earlier count of 6 missed it (corrected 2026-11-20).

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

## Docker acceptance run (2026-11-20)

### Setup

- Branch `feat/1090-f6-crewai` at `0c7ea728` (on `main` at `596f1f99`; `main` was not merged in
  this run), exported with `git archive` and built with `python -m build --wheel`. Three wheels, in
  order: `0c7ea728` as is (sha256 `f1e94eac…`, run 1); with the LLM-span fix (`11dd646d…`, run 2);
  with the fix and the reviewed guard, the tree committed with this record (`915e2e4a…`, run 3, the
  probes, the fourth `release` and `kind`). Each installed with `[eval]` and pytest into a fresh
  Python 3.12.8 venv (later wheels with `--force-reinstall --no-deps`).
- Every command ran under `env -i` with a fresh `HOME`, `TMPDIR` in the sandbox, `USER=demo`,
  `PIP_NO_CACHE_DIR=1`, and a Docker context in the sandbox `HOME` pointing at the current
  context's host. `kind` came from `~/.local/bin`. A 0600 file held only `GEMINI_API_KEY` and
  `TAVILY_API_KEY` from `~/.env`; it was deleted at the end.
- macOS 13 x86_64, Docker Desktop engine 27.5.1 (4 CPUs, 7.7 GiB). Resource Saver was off.
- `agentbreeder example northwind`, then `services up --environment dev --env-file <keys> --yes`.
  The port was copied from the installed wheel next to it; the test file ran from outside the
  repository (so the wheel was imported), with `AGENTBREEDER_NORTHWIND_DIR` set to that copy.

### The bug the run found, and the fix

Run 1's `agentbreeder conformance` failed one check, **`traces.llm_span`** ("the turn's trace has
no child LLM span"); LangGraph and Deep Agents pass it. CrewAI's OpenInference instrumentor writes
AGENT, CHAIN and TOOL spans and no LLM span (F0), and spec §12 says that for CrewAI one LLM span per
call is written "from its tally (alias, `served_by`, token counts), with no request or response
body". Kit 1.0.0 does not, and the adapter did not: the first half of this record and
`docs/runtimes/crewai.md` said "no LLM spans" instead.

Fix, in the adapter only (the kit stays 1.0.0, byte for byte): `llm_spans` wraps the turn's
`on_model_called`, so each `model_called` audit row is written as before and then one span,
`llm_call` (`openinference.span.kind` `LLM`), is written as a child of `agent_turn`, with
`llm.model_name` (`served_by`), `agentbreeder.model_alias`, `llm.token_count.prompt`,
`.completion` and `.total`, `agentbreeder.step` and `agentbreeder.main_loop`, and its start time
from the row's latency. No message text. The proxy answers CrewAI on its own task, outside the
turn's span, so the span is parented on the context current when the turn started. A span that
cannot be written is logged at debug and never fails the turn. Tests:
`test_each_model_call_is_one_llm_span_under_the_turn` (fails with the call removed) and
`test_a_span_that_cannot_be_written_does_not_fail_the_turn`; runtime tests 49 passed. Read-only
Sonnet review: round 1 APPROVED with a minor (guard the span write) and nits (the test's
processor, an exact audit-row count, the doc), all applied; round 2 APPROVED.

### Results

| Item | Result |
|---|---|
| `docker build` (the normal build path, `build agent.yaml --stack ../northwind`) | **Built.** Final image `sha256:8746d51a…`, linux/amd64, **370 MB** (F0: 365 MB). The same image ID on every later build of the same tree |
| `agentbreeder conformance` (§15.1) | Run 1: **25 pass, 1 fail (`traces.llm_span`), 13 not run.** After the fix (runs 2 and 3): **`incomplete`, 26 pass, 0 fail, 13 not run, 0 n/a** (table below) |
| Step cap on a real Gemini alias (§16.1, `-k step_cap`, `max_model_calls: 1`) | **PASS, three times.** Gemini called a tool despite `tool_choice: none` (#1111); **502 `MODEL_STEP_LIMIT`**, no tool ran; the one `model_called` row carries `step_limited` |
| Northwind 61/61 through `release` (§15.2), real gateway and Gemini, on the `service/litellm` image | **PASS, 61/61, four times.** Run 1: flaky `cancel-other-customers-order` (passed on its retry), p50 3,640 ms. Run 2: no flaky, p50 3,787 ms. Run 3 (final image): no flaky, p50 4,078 ms. Before `kind`: flaky `cancel-other-customers-order`, p50 4,010 ms. The same case was flaky on LangGraph (F3) |
| Multi-turn history without tool replay (§7.5, D5) | The multi-turn golden cases passed in every release. **D5 needs no change for Northwind on CrewAI** |
| **The main-loop test on a real model** (§16.1) | **PASS.** All **578** `model_called` rows of the session have `main_loop: true`: the adapter's system message (CrewAI's own prompt for the agent) matched every real CrewAI call. CrewAI made no hidden call |
| One invoke per Northwind tool (§15.3, `-k one_invoke`) | **PASS, three times.** All seven: `lookup_orders`, `search_knowledge_base`, `web_search`, `escalate_to_human`, `save_customer_note`, `cancel_order` (two turns), `get_product_catalog`; each reply `framework: crewai`, `runtime: runtime/crewai@1.0.0`, $0.0031 to $0.0045 a turn |
| **The task-output store and console on the real image** (§8.5, §13.7, §16.1, `-k no_file_in_the_container`) | **PASS, three times.** After a turn with a unique marker: no file under `/tmp` holds it, no `*.db`, `secret.key` or `tokens.enc`, no `CREWAI_*`/`OPENAI_*` variable in the container's configuration, neither the marker nor a console panel in `docker logs` |
| Phoenix (§15.3) | **Seen.** Project `northwind-support-crewai`: an `AGENT` root (`agent_turn`), a `RETRIEVER` per turn and **exactly one `TOOL` span per Northwind tool call** (TOOL counts equal the `tool:*` audit rows: 56, 25, 21, 9, 9, 6, 6); CrewAI's own tool spans (`<tool>.run`) are relabelled `CHAIN`. **LLM spans** (fixed images): every turn that reached Phoenix has exactly as many `llm_call` spans as `model_called` rows (154 turns, 238 spans), each with the model and token counts and no `input`/`output` attribute. The two step-cap turns of runs 2 and 3 reached Phoenix with one span only: their deployments were removed seconds later (**#1185**, as in F4 and F5). The console's Traces view was **not opened** |
| Cost against LiteLLM's spend log (§15.4) | **PASS, exactly.** The whole session: **578 `model_called` rows, $0.98019525, equal to the 578 successful `support-model` spend rows.** Per key, for example run 1: release candidate 106 rows $0.1771275, production 18 rows $0.031389, step cap 1 row $0.001515, each equal on both sides. `support-embedding` is not in `cost_usd`, as in 1.3.0 |
| `MODEL_ACCESS_DENIED` live (§15.5) | **PASS.** A probe deployment's own LiteLLM key changed to `models: [support-embedding]` (`/key/update`, master key from the stack's secret file, never printed): **502 `MODEL_ACCESS_DENIED`**; the `turn_failed` row has gateway 403 and LiteLLM's model-access message. Restored; the next turn answered (`lookup_orders`, 2 calls) |
| `MODEL_BUDGET_EXCEEDED` live (§15.5) | **PASS.** Probe with `budget_usd: 0.0001`: call 1 cost $0.00148275 and ran `lookup_orders`, call 2 was refused (LiteLLM 422 `Budget has been exceeded!`): **402 `MODEL_BUDGET_EXCEEDED`**; the next two turns 402 before any model call. The error row carries the key alias and a masked key (`sk-[redacted]`). Same as ADK, LangGraph and Deep Agents |
| **CrewAI's extra attempts against the real gateway** (§16.1) | **PASS.** Probe with `rpm: 1` (the turn's embedding call used the minute's request): the model call got LiteLLM's 429 with a 60 s reset, and the turn ended **429 `MODEL_RATE_LIMITED`** in 0.65 s (1.8 s for the whole `invoke`), with **one 429 in LiteLLM's log**. CrewAI's own retry loop (3 attempts, 1 to 8 s backoff) made no attempt, and the proxy made no retry either |
| Egress on the deploy network (§15.7) | **PASS (default deny), the same as LangGraph.** The production deployment sat on its `-int` network and the stack's `agents` network, both `internal: true`, with the forwarder sidecar. From inside: `1.1.1.1:443` and `8.8.8.8:53` network unreachable; `example.com`, `telemetry.crewai.com`, `app.crewai.com`, `us.i.posthog.com`, `api.openai.com`, `generativelanguage.googleapis.com` and `host.docker.internal` not resolved; `phoenix` and `ops` not resolved; `litellm:4000` and `postgres:5432` connected; `proxy` resolved, port 8080 refused. The `openai` SDK (`chat.completions.create`, dummy key, no retries) → `APIConnectionError`; an `httpx` POST to CrewAI's telemetry endpoint → name resolution failed |
| **CrewAI telemetry and outbound connections** (owner's F6 check) | **PASS.** After the test file's turns, the container's sockets (`/proc/net/tcp`) connected only to `postgres` (5432), `litellm` (4000), its own forwarder sidecar (traces) and the loopback model proxy. Nothing to any CrewAI host, which does not resolve under default deny anyway |
| **chromadb never listens** (owner's F6 check, the known exception) | **PASS.** Listening sockets in the production container: `0.0.0.0:8080` (the server), `127.0.0.1:<port>` (the kit's model proxy) and `127.0.0.11` (Docker's DNS); no IPv6 listener. Processes: `python /app/runtime/server.py` only (and the probe). On `kind`: `0.0.0.0:8080` and the loopback proxy only. No ChromaDB port (8000) or process anywhere |
| `promote` to `kind` (§15.6, `-k kind`) | **PASS.** `cluster up --stack . --environment dev --yes`, then `release` again (the probes had removed production, as in F5), then `promote agent.yaml --to kubernetes`: pushed to `localhost:5001`, **cluster evals 61/61**, no flaky, p50 3,439 ms |
| Kubernetes `/tmp` (§16.1) | **PASS.** Pod spec: `emptyDir {medium: Memory, sizeLimit: 64Mi}` at `/tmp`; `/proc/mounts`: `tmpfs /tmp … size=65536k`; writing `/tmp` worked; read-only root (`touch /probe`: Read-only file system); `allowPrivilegeEscalation: false`, all capabilities dropped, limits 1 CPU and 512 Mi; no `OPENAI_*`, `CREWAI_*`, `GEMINI_*`, `GOOGLE_*` or `ANTHROPIC_*` variable in the pod spec |
| One invoke on `kind` (by hand) | **PASS.** `agentbreeder forward ID --port 18080`, POST `/invoke`: 200, `lookup_orders`, `framework: crewai`, 2 model calls, $0.00391125; its trace in Phoenix: `agent_turn`, `retrieve_knowledge`, two `llm_call` LLM spans, one TOOL span |
| Start-up and memory (Python 3.12 Linux image) | Production after the test file's turns (the tool, marker and earlier turns): **174.8 MiB of the 512 MiB limit, 20 pids**; the last production deployment, idle after `release`: 154.7 MiB, 6 pids (`docker stats`; F0: 178 MB and 18 pids for two sessions; LangGraph 101.5 MiB, Deep Agents 107.1 MiB). CPU limit 1, read-only root. Start-up time not measured |
| **arm64** (§16.1) | **Not run**: this host is x86_64. The lock resolves to identical pins for aarch64 (first half); the image has not been built or run on arm64 |
| The console's Build ▸ framework list (Playwright) | Not run locally; CI `console-ui` |

### Conformance, against LangGraph (F3) and the ADK baseline (F2a)

| | pass | fail | not run | n/a |
|---|---|---|---|---|
| `runtime/crewai@1.0.0`, profile `kit` (after the fix) | 26 | 0 | 13 | 0 |
| `runtime/langgraph@1.0.0`, profile `kit` (F3) | 26 | 0 | 13 | 0 |
| `runtime/adk@1.3.0` and `1.5.0`, profile `v1` (`F2a-adk-baseline.md`) | 22 | 4 | 8 | 5 |

- The same 13 not run as LangGraph: `knowledge.sources`, `knowledge.extra_searches`,
  `tools.customer`, `tools.audit`, `sessions.notes`, `traces.tool_span` (no fixture agent),
  `cost.model_called_rows` (no audit-row read), `hygiene.egress` (no network log), and the 5
  kit-profile checks, not built (`serving.readiness`, `serving.sigterm_drain`,
  `step_cap.enforced`, `model_errors.swallowed`, `proxy.requests`). The run above covers some by
  hand (step cap, egress, one TOOL span per call, `model_called` rows); that is not the check
  passing.
- CrewAI passes the four checks ADK fails (`sessions.busy`, `traces.llm_span` after the fix,
  `model_errors.text_logs`, `model_errors.text_spans`).

### Test file

`tests/test_northwind_crewai_docker.py` ran as written: no test change was needed (F3's fixes were
already in it). Only its docstring now says it ran.

### Spend

LiteLLM's spend log for the session, read before `services down`: **$1.3010 over 1,290 rows**, of
which 1,283 are `success` and 7 are `failure` at $0 (the access-denied, budget and rate-limit
probes). By alias:

| Alias | Rows | Spend |
|---|---|---|
| `support-model` | 583 (578 success) | $0.980195 |
| `support-judge` | 307 | $0.319679 |
| `support-embedding` | 400 (398 success) | $0.001159 |

`support-model` by key: four release candidates $0.177128, $0.172553, $0.167174 and $0.182108;
the `kind` deployment (cluster evals and one invoke) $0.176013; three productions (tool and marker
turns) $0.031389, $0.032007 and $0.032200; three step caps $0.001515, $0.001380 and $0.001493; the
access-denied probe $0.003755 (after the restore) and the budget probe $0.001483. The conformance
runs use fakes and made no spend.

### Leak scan and teardown

- **Leak scan:** the sandbox (`HOME`, work and test directories, logs), the background task
  outputs and this worktree were scanned for the two keys' values (4,443 files): 0 hits. The
  LiteLLM master key was read into a shell variable for the `/key/update` calls and never printed.
- **Removed, in order:** the `kind` deployment, the local production deployment (each probe had
  been removed after its check), `cluster down --stack .`, `services down --purge --yes`, then the
  56 image IDs absent from the pre-run snapshot (taken 20:53 UTC with `docker images -a`), by ID:
  each was built in this run (13:09 to 13:43 PST) or a base image pulled for it (pgvector, LiteLLM,
  Phoenix, python, kindest/node, registry), and the `kind` network (created 21:41 UTC).
- **After:** image IDs, volumes, networks and containers equal the snapshot, the default bridge ID
  is unchanged, and `docker system df` equals it (5 images, 20.16 GB; build cache 99 entries,
  14.09 GB). Nothing was pruned.

### Recommendation (owner decision)

Every §15 item that can run on this host passed, after the one adapter fix (LLM spans), and the
owner's F6 checks held on the real image: chromadb never listens, CrewAI's telemetry and memory
stay off, and the step cap counts CrewAI's calls. Not covered: arm64; the 13 unbuilt conformance
checks (5 of them the kit profile's); the console's Traces view; start-up time. Recommendation:
**merge #1186 with the LLM-span fix.** Flip `VERIFIED` and `RUNTIMES_RUN` for CrewAI only together
with LangGraph and Deep Agents, once the owner accepts conformance at 26/39 with the by-hand
coverage above, or once the kit-profile checks are built. Otherwise keep CrewAI "built, run end to
end on macOS x86_64, not verified". arm64 needs an arm64 host either way. The kit could write the
proxy's LLM spans itself at its next version (the Claude runtime, F7, needs the same).
