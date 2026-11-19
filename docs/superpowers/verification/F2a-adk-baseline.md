# F2a verification: the ADK baseline against agent protocol v1 (#1083, #758, #759)

Status: **not run.** No conformance run on a real container is recorded here. The lane that
built F2a (2026-11-18, macOS) was not allowed to use Docker, so `agentbreeder conformance` has
only run against a stub agent and a fake Docker runner in the test suite. Everything under
"Expected gaps" is a reading of `runtime/adk@1.3.0`'s source, **not a result**.

This is on `main`, not in a published preview.

## What has to be run

The multi-framework spec (§10.3) says `runtime/adk@1.3.0` is run first, against profile `v1`,
and every check it fails is listed here (owner decision D14: list every gap, fix it in a later
`runtime/adk` version or record it as an ADK exception, never relax a check).

From a checkout that has this slice, with the package installed (`python -m pip install -e
".[dev]"`), a local Docker engine and nothing else using it:

```bash
# 1. An agent built on runtime/adk@1.3.0: Northwind pins it.
agentbreeder example northwind "$SANDBOX/northwind"
cd "$SANDBOX/northwind"
agentbreeder services up --environment dev --env-file ~/.env --yes   # writes the operator config build needs
agentbreeder build agent.yaml --stack . --environment dev --json | tee build.json

# 2. The conformance run. IMAGE is the "image" value in build.json (sha256:…).
agentbreeder conformance "$IMAGE" --project . --json | tee conformance.json
echo "exit $?"        # 1 is expected: `incomplete` or `fail`, never 0 while checks are unbuilt

# 3. Clean up, and check nothing of the run is left.
agentbreeder services down --yes
docker ps -a --filter label=io.agentbreeder.conformance
docker network ls --filter label=io.agentbreeder.conformance
```

Step 2 needs no stack: `services up` is only there so that `build` has an operator config. The
conformance run starts its own network, database and fakes, and pulls
`python:3.12-slim@sha256:4840d514…` and `pgvector/pgvector@sha256:0dd3149a…` the first time.
The report is also written to `~/.agentbreeder/conformance/adk/1.3.0/<digest>.json`.

To fill this record: paste the `counts` and every row whose `status` is not `pass` into the
tables below, attach `conformance.json` to #759, state the Docker engine and OS versions, and
change the status line. Repeat with `runtime/adk@1.4.0` (the `new agent` starter pins it) if
time allows; it is not required by the spec.

## Result

| Run | Image | Result | pass | fail | not run | not applicable |
|---|---|---|---|---|---|---|
| `runtime/adk@1.3.0`, Northwind, profile `v1` | not run | not run | | | | |

### Checks that failed

Not run. One row per failing check, with the report's `detail`, when the run is done.

| Check | Detail | Decision (fix in a later `runtime/adk`, or a recorded ADK exception) |
|---|---|---|
| | | |

## Expected gaps (from the source, not observed)

These are what a reader of `runtime/adk@1.3.0` would expect the run to show. They are here so
the run is read against them, and so D14's list is not empty while the run is pending. A row
that the run does not confirm is deleted, not kept.

### Against protocol v1 (checks that apply to a `v1` image)

| Check | Why it is expected to fail | Source |
|---|---|---|
| `sessions.busy` | A second request for a session waits for the first (a blocking per-session lock) and is then answered 200. 409 is returned only after a turn timed out and is still stopping. The check asks for 409 while a turn is running | `server.py` `session_lock` (blocking by default), `agent.py` `chat` (`INFLIGHT`) |
| `model_errors.text_logs` | A failed model call is logged as one line with `detail=` holding the gateway's message. It is redacted for keys and token hashes, not removed, so the scripted message is in the log | `agent.py` `chat_async` ("model call failed: … detail=…"), `redaction.upstream` |
| `model_errors.text_spans` | The failing span's status and its `exception.message` event carry the error text, redacted the same way | `telemetry.py` `span` and `RedactingExporter` |

### Not known until the run

| Check | What is uncertain |
|---|---|
| `traces.llm_span` | Whether the trace has a child span with `openinference.span.kind` `LLM`. The runtime relabels ADK's own model span `CHAIN` and relies on "the provider SDK's span" for the LLM span; with a LiteLLM client there may be none |
| `cost.reported` | Whether `prompt_tokens` and `output_tokens` equal the gateway's `usage` exactly (ADK's usage metadata may count differently) |
| `serving.oversize_body` | `runtime/adk@1.3.0` answers 400 without reading a 64 KiB body. If the socket is then reset before the client reads the status, the check fails with "reset before a status could be read"; whether Docker's forwarder shows a reset has not been observed |
| `serving.host` | Expected to pass (`localhost`, `127.0.0.1`, `agent`), but the request crosses the kit's TCP forwarder, which has not carried a real agent |
| every check on an agent with `mcp:` servers | The kit's relay address answers 503. The runtime is expected to report the server in `mcp_unavailable` and continue; Northwind has no `mcp:` server, so the baseline does not exercise it |
| the environment itself | Docker's handling of the two networks, the secret-file mounts on Docker Desktop, the database wait and the published fakes ports have run only against a fake runner |

### Against the kit profile (not applicable to a `v1` image; listed for D14)

`runtime/adk` does not claim the `kit` profile, so these do not fail the baseline. They are the
known distance between ADK and the profile the other frameworks will serve:

| Kit addition | `runtime/adk@1.3.0` |
|---|---|
| `/health` is 503 until the agent is built and after a failed start | Always 200 once the server listens, also during warm-up and after a failed warm-up (`server.py` `do_GET`, `warm`) |
| SIGTERM lets the turn in flight finish or time out within the stop grace | SIGTERM closes the MCP sessions and stops the listener (`server.py` `stop`); nothing bounds a turn in flight to the stop grace. The spec's D14 lists this as "no SIGTERM drain"; what happens to a turn in flight has not been observed |
| `MODEL_STEP_LIMIT` when the model still asks for tools on its last allowed call | No such code. The last call is sent with function calling off and the turn falls back to a fixed apology if no text comes back; a model that ignores the setting is #1111 |

## What was run in F2a (no Docker)

- The 26 built checks pass against a stub agent that serves protocol v1, and each of 30 broken
  behaviours of that stub fails the check meant to catch it (`tests/test_conformance_checks.py`).
- `environment.run` end to end against a fake Docker runner that starts the real fakes and the
  stub in process: the commands issued, the flags, that no secret is on a command line, and
  that everything is removed on success and on each failure
  (`tests/test_conformance_environment.py`).
- The fakes, the OTLP reader, the report and the command (`tests/test_conformance_fakes.py`,
  `tests/test_conformance_environment.py`).

## Not built in F2a

13 of the 39 checks have no implementation and are reported `not_run`, so every report is
`incomplete` at best:

- **the fixture agent** (a tool, a knowledge set and notes with known content): `knowledge.*`,
  `tools.*`, `sessions.notes`, `traces.tool_span`;
- **a read of the agent's audit rows**: `cost.model_called_rows` (and the audit half of
  `tools.audit`);
- **the kit-profile checks** (F2b): `serving.readiness`, `serving.sigterm_drain`,
  `step_cap.enforced`, `model_errors.swallowed`, `proxy.requests`;
- **a fake MCP relay** (spec §10.3): an `mcp:` server's relay address always answers 503, so
  no relayed tool call is checked;
- **the DNS and TCP log** of other connection attempts: `hygiene.egress` (the internal network
  gives the agent nowhere else to connect, but no attempt is recorded);
- code-mode images and the per-runtime framework checks (F3 to F8).

Also not done, as recorded deviations from the spec:

- **`list --json` does not read the image's `org.agentbreeder.runtime` label** (spec §11, source
  2). `list` has never called Docker, no image has the label yet, and a kit runtime's manifest
  carries `runtime` (source 1). The owner decides whether `list` may call Docker for it.
- **The fakes have not answered a real OpenAI or LiteLLM client.** Their response shapes were
  written from the client code in `runtime/adk@1.3.0`; only the stub agent has used them.
- **A hand-written agent can talk to the fakes itself** (send its own spans, use up scripted
  answers). The kit refuses to conclude from a record the fakes had to cut short, but a black-box
  suite cannot prove an image did not stage its answers. A report says what an honest runtime
  does, which is what a `runtime/` version is.
- The test database runs with the image's defaults apart from a pids limit, a memory limit and
  `no-new-privileges`, and the agent holds its superuser password. It is a throwaway on the
  internal network, but it is less confined than the agent and the fakes.
- A killed run (SIGKILL, or a power loss) leaves its containers, networks and
  `~/.agentbreeder/conformance/.runs/<id>/` behind; nothing sweeps them. The label
  `io.agentbreeder.conformance` finds them.

## Notes

- A report with a `not_run` row is `incomplete`, and `registry runtimes --json` shows it with
  `passed: false`. No runtime version can be called conformant until the checks above exist.
- The three v1 gaps expected above are behaviours `docs/agent-protocol.md` describes as
  `runtime/adk@1.3.0`'s. If the run confirms them, the owner decides per D14 between a later
  `runtime/adk` version and a recorded ADK exception; the checks stay as the spec has them.
