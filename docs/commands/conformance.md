# `agentbreeder conformance`

Check a built agent image against agent protocol v1 (local Docker, preview).

On `main`, not in a published preview (#759, #1083 F2a). **Not run on a real Docker engine yet:** the checks pass against a stub agent in the test suite and the Docker steps were tested against a fake runner only. The first real run is the ADK baseline, [`F2a-adk-baseline.md`](../superpowers/verification/F2a-adk-baseline.md). Do not call a runtime conformant from this command until that record says it ran.

`agentbreeder conformance IMAGE [--project DIR] [--json]` runs a black-box suite against an image `agentbreeder build` made and writes a report with one row per check. The protocol is in [`agent-protocol.md`](../agent-protocol.md).

- `IMAGE` is the image ID `build` printed (`sha256:` and 64 hex digits). The command finds the build record that names it in `DIR/deploy/` (`<agent>.build.json`), reads the deploy manifest beside it (`<agent>.deploy.yaml`) for the port, the health path and the `mcp:` servers, and takes the profile (`v1` or `kit`) from the image's runtime version in the registry. An image no build record names is refused (`CONFORMANCE_NO_BUILD_RECORD`).
- It needs the local Docker engine and network access to pull two pinned images the first time (Python 3.12 slim for the fakes, and the `pgvector/pgvector` image `service/postgres-pgvector` uses). It reads no operator config and no stack, and calls no identity provider and no model.

## What it starts

Everything is made for the run and removed afterwards (containers and networks carry the label `io.agentbreeder.conformance=<run id>`):

- an internal Docker network with three containers: the agent, a throwaway Postgres with pgvector, and the fakes. On a Linux engine the network also gets the isolated gateway mode the egress boundary uses, so the agent cannot reach host services through the bridge; that needs Docker Engine 28 or later, and an older Linux engine is refused. Docker Desktop keeps the gateway inside its VM;
- the agent, created by the same function `deploy` uses, so it runs with deploy's flags (uid 65532, read-only root, no capabilities, 512 MB, 128 pids, a 64 MB `/tmp`), publishing no port;
- the fakes: a scripted gateway (OpenAI chat completions and embeddings, Anthropic messages), a span collector and a relay address that is always down for `mcp:` servers. They record what the agent sends; a credential is kept as a hash only. Their control API and a TCP forwarder to the agent are published on host loopback, the control API behind a random token;
- secrets as read-only files, as at deploy: a random gateway key, the test database URL and one relay token per `mcp:` server. The hygiene checks search replies, the log and spans for them.

Before the checks, the agent gets one turn outside the report (up to 180 seconds), because a `v1` runtime may answer `/health` before it has built itself. The log is searched only when it can be read whole (at most 10,000 lines and 4 MB); a longer one makes the log checks `not_run`. A check also fails when the fakes had to drop a request or a span at one of their limits, because their record is then incomplete.

The suite is black-box: it reports what the image did against the fakes. An image written to deceive it can talk to the fakes itself, so a report is evidence about a runtime, not a security review of arbitrary code.

## Checks

39 checks, 26 built. A `v1` image is held to the 34 that are not kit-only.

| Check | Group | What it asks | Profiles | State |
|---|---|---|---|---|
| `serving.health` | Serving | GET /health answers 200 | v1, kit | built |
| `serving.bad_request` | Serving | A body that is not the v1 request is 400 and reaches no model | v1, kit | built |
| `serving.oversize_body` | Serving | A body over 64 KiB is 400 | v1, kit | built |
| `serving.long_message` | Serving | A message over 4,000 characters is 413 | v1, kit | built |
| `serving.content_type` | Serving | A request that is not application/json is 415 | v1, kit | built |
| `serving.host` | Serving | Only the Hosts localhost, 127.0.0.1 and agent are served | v1, kit | built |
| `serving.readiness` | Serving | /health is 503 until the agent is built and after a failed start | kit | not built (the runtime kit exists since F2b; the check does not yet) |
| `serving.sigterm_drain` | Serving | SIGTERM lets the turn in flight finish within the stop grace | kit | not built (the runtime kit exists since F2b; the check does not yet) |
| `reply.fields` | Reply | A reply has every v1 field with its type | v1, kit | built |
| `reply.identity` | Reply | framework and runtime are the image's | v1, kit | built |
| `knowledge.sources` | Knowledge | A scripted question's source is in the first 3 of sources | v1, kit | not built: needs the fixture agent |
| `knowledge.extra_searches` | Knowledge | search_knowledge_base stops at extra_searches | v1, kit | not built: needs the fixture agent |
| `tools.customer` | Tools | A tool gets the customer the server set, not one the model chose | v1, kit | not built: needs the fixture agent |
| `tools.audit` | Tools | One audit row and one TOOL span per call; a failing tool gives one ok: false row | v1, kit | not built: needs the fixture agent |
| `step_cap.enforced` | Step cap | The Nth call has no tools, a tool-calling Nth answer is MODEL_STEP_LIMIT | kit | not built (the runtime kit exists since F2b; the check does not yet) |
| `model_errors.budget` | Model errors | A spent budget is 402 MODEL_BUDGET_EXCEEDED and is not retried | v1, kit | built |
| `model_errors.rate_limited` | Model errors | A rate limit is 429 MODEL_RATE_LIMITED with the gateway's Retry-After | v1, kit | built |
| `model_errors.access_denied` | Model errors | A refused key is 502 MODEL_ACCESS_DENIED | v1, kit | built |
| `model_errors.request_refused` | Model errors | A refused request is 502 MODEL_REQUEST_REFUSED | v1, kit | built |
| `model_errors.unavailable` | Model errors | A failing gateway is 502 MODEL_UNAVAILABLE after at most 3 tries | v1, kit | built |
| `model_errors.swallowed` | Model errors | A framework that swallows the model error still gives the coded error | kit | not built (the runtime kit exists since F2b; the check does not yet) |
| `proxy.requests` | Proxy | No guardrail, api_base, metadata or mock_response field and no inbound header reaches the gateway; other paths, finished turns' tokens and other aliases are refused | kit | not built (the runtime kit exists since F2b; the check does not yet) |
| `cost.reported` | Cost | cost_usd and the token counts are the gateway's | v1, kit | built |
| `cost.unknown` | Cost | cost_usd is null when the gateway gave no cost | v1, kit | built |
| `cost.fallbacks` | Cost | fallbacks and served_by are the gateway's | v1, kit | built |
| `cost.model_called_rows` | Cost | One model_called audit row per model call | v1, kit | not built: needs a read of the audit rows |
| `sessions.history` | Sessions | A session's earlier messages reach the model on the next turn | v1, kit | built |
| `sessions.busy` | Sessions | A turn on a session that is still running one is 409 | v1, kit | built |
| `sessions.customers` | Sessions | A second customer with the same session id gets an empty history | v1, kit | built |
| `sessions.notes` | Sessions | Customers never see each other's notes | v1, kit | not built: needs the fixture agent |
| `traces.root_span` | Traces | One root agent_turn span with the turn's attributes | v1, kit | built |
| `traces.llm_span` | Traces | The turn's trace has a child LLM span | v1, kit | built |
| `traces.tool_span` | Traces | One child TOOL span per tool call | v1, kit | not built: needs the fixture agent |
| `hygiene.replies` | Hygiene | No gateway key, token or masked fragment in a reply | v1, kit | built |
| `hygiene.logs` | Hygiene | No gateway key, token or masked fragment in the log | v1, kit | built |
| `hygiene.spans` | Hygiene | No gateway key, token or masked fragment in a span | v1, kit | built |
| `model_errors.text_logs` | Model errors | The gateway's error text is not in the log | v1, kit | built |
| `model_errors.text_spans` | Model errors | The gateway's error text is not in a span | v1, kit | built |
| `hygiene.egress` | Hygiene | No connection attempt outside the fakes | v1, kit | not built: needs the network log (after F1b) |

Each row of the report has a `status`:

- `pass` or `fail` (with a one-line `detail`);
- `not_run`: the check is not built yet, or had nothing to read (no container log, no spans). The detail says which;
- `not_applicable`: a kit-profile check on a `v1` image.

`result` is `pass` only when every check that applies passed, `fail` when one failed, and otherwise `incomplete`. **`incomplete` is not a pass.** While any check is not built, no image can get `pass`, so no runtime version is called conformant yet. A check is never relaxed for a runtime: a runtime that fails one is fixed in a later version or the failure is recorded (owner decision D14).

Not built at all yet: the fixture agent (so nothing checks knowledge, tools or notes), a read of the agent's audit rows, the kit-profile checks, the DNS and TCP log of other connection attempts (the internal network gives the agent nowhere else to connect, but nothing records an attempt), a fake MCP relay (an `mcp:` server's relay address always answers 503, so nothing checks a relayed tool call), code-mode images, and the per-runtime framework checks of the multi-framework spec §10.3.

## Report

Always written to the default state directory, whatever `--stack` or `--state-dir` another command was given: `~/.agentbreeder/conformance/<runtime name>/<version>/<image digest>.json` (0600, in 0700 directories). `--json` prints the same object plus `report_path`:

`{"schema_version": 1, "protocol": 1, "profile", "runtime", "mode": "declared", "agent", "image", "result", "counts": {"pass", "fail", "not_run", "not_applicable"}, "started", "finished", "agentbreeder", "checks": [{"id", "group", "title", "status", "detail"}]}`.

`agentbreeder registry runtimes --json` shows the newest report of each runtime version as `conformance: {passed, result, digest, date}` ([`registry`](registry.md)).

## Exit codes

0 when `result` is `pass`; 1 when it is `fail` or `incomplete`; 2 when the run could not start or finish, with a code: `CONFORMANCE_IMAGE` (not an image ID), `CONFORMANCE_NO_BUILD_RECORD`, `CONFORMANCE_MANIFEST` (the deploy manifest is missing, invalid or for another image), `REF_NOT_FOUND`, `REF_INVALID`, `REF_UNPINNED` or `ARTIFACT_INVALID` (the build record's runtime), `PROTOCOL_UNSUPPORTED`, `CONFORMANCE_ENVIRONMENT` (Docker refused a step, or the database or the fakes did not start) and `CONFORMANCE_REPORT` (the report could not be written). An agent that never answers `/health` is not exit 2: the checks run and fail.

Flags: `agentbreeder conformance --help`.
