# #1083 F2b + F3 verification: the runtime kit and `runtime/langgraph@1.0.0`

Status: **the acceptance ran on Docker and `kind` (2026-11-19), except arm64.** Northwind on
LangGraph released 61/61 locally and 61/61 again on `kind`. Conformance: 26 pass, 0 fail, 13 not
run. The run found one product bug: the kit's history read was refused by Northwind's grants. It
is fixed in this PR (below). macOS 13 x86_64; arm64 was not run (no arm64 host). LangGraph is not
called supported, and `agentspec.VERIFIED` and `RUNTIMES_RUN` are unchanged: owner decision.

The first half of this record ("What ran") was written without Docker. The second half,
"Docker acceptance run", is the run.

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

## The plan before the run (spec §15 and the §16.1 items marked F3)

As written before the run; each row's outcome is in "Docker acceptance run" below. The commands
are in `tests/test_northwind_langgraph_docker.py`.

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

## Docker acceptance run (2026-11-19)

### Setup

- Branch `feat/1083-f3-langgraph` at `afb4c486` (a merge of `main` at `732fa304`) plus the fix
  and test changes below, exported with `git archive` and the diff applied, built with
  `python -m build --wheel` (`agentbreeder-3.0.0a3-py3-none-any.whl`, sha256 `64a2e066…`; the README
  line came later and is not in it; the first runs, up to the bug below, used the wheel without the fix, `7e70cfdd…`), installed with
  `[eval]` and pytest into a fresh Python 3.12.8 venv.
- Every command ran under `env -i` with a fresh `HOME`, `TMPDIR` in the sandbox, `USER=demo`,
  `PIP_NO_CACHE_DIR=1`, and a Docker context created in the sandbox `HOME`. `kind` came from
  `~/.local/bin`. A 0600 file held only `GEMINI_API_KEY` and `TAVILY_API_KEY` from `~/.env`; it was
  deleted at the end.
- macOS 13 x86_64, Docker Desktop engine 27.5.1 (4 CPUs, 7.7 GiB). Resource Saver was off.
- `agentbreeder example northwind`, then `services up --environment dev --env-file <keys> --yes`
  (`service/litellm@1.0.0`, Northwind's pin). The test file ran from the sandbox with
  `AGENTBREEDER_NORTHWIND_DIR` set to that copy.

### The bug the run found, and the fix

The first invoke on the capped build returned **500**, "The agent failed to answer this turn",
not `MODEL_STEP_LIMIT`. The container log said `turn failed: InsufficientPrivilege`, and Postgres
logged `permission denied for table turns` on the kit's history read
(`SELECT message, reply FROM turns WHERE customer=… AND session=… …`). A turn with no tool failed
the same way: **every LangGraph turn on Northwind failed.** Northwind's `commerce.sql` grants the
agent role (`support_agent`) only INSERT on `turns`. ADK never reads `turns` (its sessions are in
`adk_sessions`, which the role owns). The kit reads its history from `turns` (spec §7.5). The unit
tests did not catch it: they use a database where the role owns the tables, as does the
conformance kit, whose `sessions.history` passed.

Fix: `GRANT SELECT (session, customer, message, reply, error, created) ON turns TO support_agent`
in `commerce.sql`: the six columns `history()` reads, and no others (`tools`, `sources`,
`evidence`, trace ids and token counts stay unreadable). Also: `stack.lock`'s hash (written by
`services up --update`), `_content.json`, a README line, and a test that the grant names exactly
the columns `history()` uses
(`test_northwinds_agent_role_can_read_what_kit_history_reads`). On the live stack, after
`services up --update`: `has_column_privilege` true for `reply`, false for `tools`. Read-only
Sonnet review: APPROVED (notes taken: the test reads `history()`'s source rather than grepping
lines; the README says what the role can read). What the role gains: other customers' messages
and replies in `turns`, which it could already read through ADK's `adk_sessions` on an ADK
deployment. An existing Northwind stack needs `services up --update` (the `stack.lock` hash).

### Results

| Item | Result |
|---|---|
| `docker build` (the normal build path, `build agent.yaml --stack ../northwind`) | **Built.** `sha256:814f634d…`, linux/amd64, 379 MB. Same image ID in every later build (deterministic) |
| `agentbreeder conformance` (§15.1) | **`incomplete`: 26 pass, 0 fail, 13 not run, 0 n/a**, 62 s (table below). Exit 1, as expected while checks are unbuilt |
| Step cap on a real Gemini alias (§16.1, `-k step_cap`, `max_model_calls: 1`) | **PASS.** Gemini called a tool despite `tool_choice: none` (#1111). The turn failed **502 `MODEL_STEP_LIMIT`** ("The agent used its model calls for this turn; ask again or raise model.max_model_calls."), no tool ran. Twice (alone and in the full file) |
| Northwind 61/61 through `release` (§15.2, `-k release`), real gateway and Gemini, on the `service/litellm` image | **PASS, 61/61, twice.** Run 1: `flaky: [cancel-other-customers-order]` (passed on its retry), mean correctness 1.0, p50 3,943 ms, 191 s. Run 2 (full file): 61/61, no failures. Released `agent/northwind-support-langgraph@0.1.0` |
| Multi-turn history without tool replay (§7.5, D5) | The multi-turn golden cases passed in both releases. **D5 (text-only replay) needs no change for Northwind** |
| One invoke per Northwind tool (§15.3, `-k one_invoke`) | **PASS** after test fixes (below): `lookup_orders`, `search_knowledge_base`, `web_search`, `escalate_to_human`, `save_customer_note`, `cancel_order` (two turns), `get_product_catalog`, each reply `framework: langgraph`, `runtime: runtime/langgraph@1.0.0`, $0.0030 to $0.0042 a turn |
| Phoenix (§15.3) | **Seen**, in Phoenix's database, for the 7 tool traces and the `kind` invoke: project `northwind-support-langgraph`; an `AGENT` root (`agent_turn`), a `RETRIEVER` (`retrieve_knowledge`), `LLM` spans (`ChatOpenAI`), and **exactly one `TOOL` span per tool call**; LangGraph's own tool span relabelled `CHAIN`. The console's Traces view was **not opened** |
| Cost against LiteLLM's spend log (§15.4) | **PASS, exactly.** Production key, 18:09:30 to 18:12:32: 32 `support-model` spend rows, $0.0550875; 32 `model_called` audit rows in that window, $0.0550875. Each reply's `cost_usd` equals its turn's `model_called` rows (for example $0.0036255, $0.00424425, $0.00379125). The 18 `support-embedding` rows ($0.0000404) are not in `cost_usd`, as in 1.3.0 |
| `MODEL_ACCESS_DENIED` live (§15.5) | **PASS.** A probe deployment's own LiteLLM key changed to `models: [support-embedding]` (`/key/update`, master key from the stack's secret file, not printed): **502 `MODEL_ACCESS_DENIED`** ("The gateway refused this agent's key for its model…"). Gateway 403 `key_model_access_denied`. Then restored |
| `MODEL_BUDGET_EXCEEDED` live (§15.5) | **PASS.** The probe with `budget_usd: 0.0001`: the first turn's call 1 cost $0.0013635, call 2 was refused (LiteLLM 422 `Budget has been exceeded!`), **402 `MODEL_BUDGET_EXCEEDED`**, `retry_after_s: null`; the next two turns 402 before any model call. Same behaviour as ADK (`gateway-limits.md` S3) |
| Egress on the deploy network (§15.7) | **PASS (default deny).** The probe (kit profile) sat on its `-int` network (internal) and the stack's `agents` network (internal), with the forwarder sidecar. From inside: `1.1.1.1:443` and `8.8.8.8:53` network unreachable; `example.com`, `generativelanguage.googleapis.com` and `host.docker.internal` not resolved; `phoenix` and `ops` not resolved; `litellm:4000` and `postgres:5432` connected; `proxy` resolved (port 8080 refused) |
| `promote` to `kind` (§15.6, `-k kind`) | **PASS.** `cluster up`, then `promote agent.yaml --to kubernetes`: pushed to `localhost:5001`, **cluster evals 61/61**, no flaky, p50 4,443 ms |
| Kubernetes `/tmp` (§16.1) | **PASS.** Pod spec: `emptyDir {medium: Memory, sizeLimit: 64Mi}` at `/tmp`; `/proc/mounts`: `tmpfs /tmp … size=65536k`; writing `/tmp` worked; root filesystem read-only (`touch /probe`: Read-only file system); `allowPrivilegeEscalation: false`, all capabilities dropped |
| One invoke on `kind` (by hand) | **PASS.** `agentbreeder forward ID --port 18080`, POST `/invoke`: 200, `lookup_orders`, `framework: langgraph`, 2 model calls, $0.00372675, its trace in Phoenix |
| Start-up and memory (Python 3.12 Linux image) | Idle after 4 turns: **101.5 MiB of the 512 MiB limit** (`docker stats`), CPU limit 1, read-only root. Start-up time not measured |
| **arm64** (§16.1) | **Not run**: this host is x86_64. The lock resolves to identical pins for aarch64 (first half); the image has not been built or run on arm64 |

### Conformance, against the ADK baseline (F2a)

| | pass | fail | not run | n/a |
|---|---|---|---|---|
| `runtime/langgraph@1.0.0`, profile `kit` | 26 | 0 | 13 | 0 |
| `runtime/adk@1.3.0` and `1.5.0`, profile `v1` (`F2a-adk-baseline.md`) | 22 | 4 | 8 | 5 |

- LangGraph **passes the four checks ADK fails**: `sessions.busy` (409 at once), `traces.llm_span`,
  `model_errors.text_logs` and `model_errors.text_spans`.
- **Not run (13):** the 8 ADK also could not run (`knowledge.sources`, `knowledge.extra_searches`,
  `tools.customer`, `tools.audit`, `sessions.notes`, `traces.tool_span`: no fixture agent;
  `cost.model_called_rows`: no audit-row read; `hygiene.egress`: no network log), and the 5
  kit-profile checks, which are n/a for ADK and not built (`serving.readiness`,
  `serving.sigterm_drain`, `step_cap.enforced`, `model_errors.swallowed`, `proxy.requests`). The run
  above covers some of these by hand (step cap, egress, one TOOL span per call, `model_called`
  rows); that is not the check passing.
- The conformance kit uses its own database, so it did not catch the grant bug.

### Test file changes (the first time it ran)

The opt-in test had never run. Fixed in `tests/test_northwind_langgraph_docker.py`:
- the customer was `c-1001`, which Northwind does not have; it is now `eval-cust-a`, whose orders
  `release`'s `fixtures.sql` resets;
- the web-search prompt asked about fountain-pen news, which Northwind's prompt declines; it is now
  a third-party question (exporting from another note app), as the prompt allows;
- `cancel_order` needs an explicit "yes" in the same conversation (the prompt), so it gets two
  messages in one session, on order `NW-90002` (the golden set files a request on `NW-90004`,
  and the model then correctly says a request is already pending);
- the note is new each run (a note the customer already has is not saved again);
- `promote` takes the `agent.yaml` path, not the agent name (`AGENT_INVALID`).

With these, the whole file passed: `test_build_and_conformance`, `test_step_cap_on_real_gemini`,
`test_release_passes_61_of_61` in one run, then `test_one_invoke_per_northwind_tool` and
`test_promote_to_kind` against that release (the first full run failed those two on the
`NW-90004` order and the `promote` argument).

### Spend

LiteLLM's spend log for the session: **$0.8635 over 867 rows, all `success`** when read after the
cluster evals, plus the probe's $0.0014. Three releases' worth of evals (two local, one on
`kind`, each with judge calls), the conformance run (fakes, no spend), and the probes.

### Leak scan and teardown

- The saved outputs and the sandbox `HOME` were scanned for the two keys' values: 0 hits. The
  LiteLLM master key was read into a shell variable for the two `/key/update` calls and never
  printed. A gateway error in the agent's `turn_failed` audit row includes the key alias and a
  masked key (`sk-[redacted]`), as for ADK.
- Removed in order: the probe and the `kind` deployment (`remove`), `cluster down`,
  `services down --purge --yes`, then the images and the `kind` network not in the pre-run
  snapshot, by ID. Image IDs, volumes, networks and containers then equal the snapshot (4 images,
  17.76 GB, as before); the default bridge ID was unchanged. Build cache stayed at 90 entries:
  nothing was pruned. The image removal listed with `docker images -a` while the snapshot was taken
  without `-a`, so it was not guarded by creation time; the image list and total size equal the
  snapshot, so no image from before the run was removed.

### Recommendation (owner decision)

Every §15 item that can run on this host passed, after the one grant fix. Not covered: arm64; the 13
unbuilt conformance checks (5 of them are the kit profile's); the console's Traces view; start-up
time. Recommendation: **merge #1163 with the grant fix**. Flip `VERIFIED` and `RUNTIMES_RUN` for
LangGraph only if the owner accepts conformance at 26/39 with the by-hand coverage above as
enough, or once the kit-profile checks are built. Otherwise keep LangGraph "built, run end to end
on macOS x86_64, not verified". arm64 needs an arm64 host either way.
