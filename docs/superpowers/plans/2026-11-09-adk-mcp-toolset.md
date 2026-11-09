# Plan: runtime/adk@1.1.0, MCP toolsets through the relay (#746, slice 4c)

Spec: `docs/superpowers/specs/2026-11-08-agent-services-design.md` §7.3 (runtime bullet), §7.7.7
(protocol scope), acceptance criteria 10–11. #741's fixes already shipped in `runtime/adk@1.0.5`
and `@1.0.6`, so this slice is just `1.1.0`, built on `1.0.6`. The relay (#745, PR #778) and the
deploy side (#744) are merged. Deploy already does three things for each `mcp_access` entry:
- writes the grant file;
- mounts the per-deployment token as `AGENTBREEDER_MCP_<NAME>_TOKEN_FILE`;
- sets `AGENTBREEDER_MCP_<NAME>_URL`.

Here `<NAME>` is `mcp.env_name(<ref name>)`.

## Verified against the installed library (google-adk 2.12.0, mcp 2.3.0)

Read from the locked packages in a `python:3.12` container built from the 1.0.6 lock, and checked
again by the plan review (round 1 below):

- **Prefix.** ADK joins the prefix with `_` (`base_toolset.py:162`). So the runtime passes
  `tool_name_prefix=<server>`, not `<server>_` as §7.7.7 says. `tool_filter` matches the
  unprefixed MCP name.
- **`header_provider`** is called only with a `ReadonlyContext`. Its result keys both the session
  pool and the tool-list cache. A constant token therefore means **one pooled session per
  deployment, shared by every customer**, and the relay's per-token limits (4 concurrent, 60 a
  minute) are shared too: a fifth concurrent call is a `CONCURRENCY_LIMIT` tool error.
- **Timeouts.** `StreamableHTTPConnectionParams.timeout` bounds connect, write and pool, the
  initialize ready-wait and `tools/list` (`asyncio.wait_for` in `_execute_with_session`).
  `tools/call` has no `wait_for`. It is bounded only by httpx's per-read `sse_read_timeout`.
  `_create_session` retries once, so a stalled handshake costs 2 × `timeout`.
- **A failing toolset doesn't fail the turn.** `LlmAgent._convert_tool_union_to_tools` catches any
  exception from `get_tools_with_prefix`, logs it at ERROR with a traceback, and the agent runs
  without those tools. `get_tools` itself is `@retry_on_errors`, so two attempts are made.
- **MCP failures are results, not exceptions.** `_MCP_GRACEFUL_ERROR_HANDLING` is on by default,
  so `MCPTool.run_async` turns an `McpError` (which includes every relay JSON-RPC refusal) or any
  other exception into `{"error": "..."}`. A tool result with `isError: true` is also a plain
  result. `on_tool_error_callback` doesn't fire for MCP tools.
- **404 recovery.** A 404 on a known session becomes `McpError("Session terminated")`. `call_tool`
  is **not** retried, to avoid duplicate side effects; the session is discarded. So after a
  relay restart, call N returns an error to the model and call N+1 re-initializes. Tool listing
  retries transparently.
- **Unknown tools.** A model call to an undeclared name raises `ValueError` in ADK before any
  plugin callback, so the turn fails.
- **Closing sessions.** `terminate_on_close=True` sends `DELETE` on `close()`. Sessions are bound
  to their event loop.
- **Extra GET stream.** After initialize the SDK opens a standalone GET stream. The relay answers
  405, and the SDK gives up after two retries (DEBUG level), so expect two GETs per session.
- **Reserved names.** ADK silently skips an MCP tool whose unprefixed name is reserved:
  `transfer_to_agent`, `adk_request_credential`, `adk_request_confirmation`, `adk_request_input`,
  `set_model_response`.
- **No sampling, resources or redirects.** No sampling is advertised, `use_mcp_resources=False`,
  and redirects are never followed. `_MCP_MODERN_PROTOCOL` is off by default, so the client uses
  the handshake-era protocol the relay requires. If a future ADK flips that default, the relay
  refuses the stateless protocol: pin it in the r13 notes.
- **Proxies.** The httpx client keeps `trust_env`, so it would use `HTTP(S)_PROXY`; deploy sets
  none.

## Changes

### 1. `runtime/adk@1.1.0`: a new version (1.0.6 is locked and stays unchanged)

The new version is a copy of `1.0.6`, plus:

- **`runtime/mcp.py` (new).** It builds the toolsets from `config["mcp"]` (`[{name, version,
  tools}]`, already written by `build`). For each server:
  - **Relay URL:** from `AGENTBREEDER_MCP_<NAME>_URL` (`<NAME>` = ref name uppercased, `-`→`_`,
    as in deploy). It is required. If it's missing or isn't `http(s)://host[:port]/path` without
    userinfo, a `ConfigError` stops startup.
  - **Token:** from `cfg.secret("AGENTBREEDER_MCP_<NAME>_TOKEN")`, read once at startup. It must
    be 64 lowercase hex characters. The error text never includes the value.
  - **The toolset**, built with:
    ```
    RelayToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=url, timeout=10.0, sse_read_timeout=125.0, terminate_on_close=True),
        tool_filter=sorted(tools),          # convenience; the relay is the boundary
        tool_name_prefix=<ref name, - -> _>,
        header_provider=lambda ctx: {"Authorization": f"Bearer {token}"},
        tool_list_cache_ttl_seconds=60,     # fewer metered tools/list calls on the relay
    )
    ```
    - `timeout=10` bounds connect, initialize and list (at worst 20 s with ADK's retry).
    - `sse_read_timeout=125` sits just above the relay's 120 s deadline, so the relay's
      `UPSTREAM_TIMEOUT` reaches the model.
  - **`RelayToolset(McpToolset)`** wraps `get_tools`. On an exception it records the lost
    capability where it can be audited, then re-raises so ADK's own degrade applies. The record
    is `mcp_unavailable: [<server>]` in the turn trace, plus one `mcp_unavailable` audit event per
    turn and server.
  - **Logging:** the runtime configures logging for the `google_adk` loggers at WARNING, in one
    JSON-free line, without tracebacks for the MCP modules. This avoids the multi-line tracebacks
    ADK prints.
  - **Name checks at startup** (`ConfigError`):
    - Every prefixed name (`<prefix>_<tool>`) must be unique across all servers, registry function
      tools and `search_knowledge_base`. This also catches `a`+`b_c` against `a_b`+`c`.
    - No granted tool's unprefixed name may be one of ADK's reserved names, which ADK would
      silently skip.
    - Duplicate server names are already refused by `agent.yaml` (one version per name).
- **Redaction (`agent.py`):** a module-level set `SECRETS` holds each MCP token and the gateway
  key. `redact()` replaces those exact values as well as the `_KEYISH` pattern, and it is applied
  to:
  - `turn_failed` detail;
  - tool error strings in both tool callbacks;
  - the `ConfigError` text that `server.py` returns in its 503.
- **MCP results in the audit (`after_tool_callback`):**
  - For an `MCPTool` (`isinstance` works on the prefixed copy), `ok` is false and `error` is
    redacted text when the result has `error`, or when `isError` is true. The tool error text is
    cut to 300 characters.
  - `detail["mcp"] = "mcp/<name>@<version>"`.
  - Tool args are recorded as for registry tools.
- **Shutdown (`server.py`):**
  - A `SIGTERM` handler schedules `close()` of every toolset on `agent.LOOP`. It waits at most
    5 s (Docker's stop grace is 10 s), then calls `server.shutdown()`.
  - A best-effort `DELETE`, so the relay's upstream sessions close. When it doesn't complete,
    the relay's idle expiry closes them.
- **Dependencies:** `requirements.in` and `requirements.lock` are unchanged (`mcp==2.3.0` is
  already locked).
- **`manifest.yaml`:**
  - `version: 1.1.0`, with an MCP sentence in the description.
  - New `capabilities: [mcp]`.
  - A comment naming the `AGENTBREEDER_MCP_*` inputs.
- **Self-reported version:** `Dockerfile`, `runtime/agent.py` and the `/invoke` response all say
  `runtime/adk@1.1.0`.
- **Turn budget:** `TURN_TIMEOUT=170` is unchanged. A 120 s MCP call plus model calls can exceed
  it and give a 504; this is documented, not raised.

### 2. `build`: refuse `mcp:` on a runtime without MCP support (done)

`_runtime_serving` already reads the runtime's `manifest.yaml`. It will also read
`capabilities` (a list of strings; default `[]`). If `agent.mcp` is non-empty and `"mcp"` isn't
listed, `build` refuses with `MCP_RUNTIME_UNSUPPORTED`. Today an agent with `mcp:` on
`adk@1.0.6` builds, and its tools silently never appear.

### 3. Tests

**Runtime unit tests** (`runtime/adk/1.1.0/runtime_tests/test_mcp.py`):
- **Setup:** the real ADK `McpToolset` and the real `service/mcp-relay@1.0.0` `relay.py`, run in
  process. Both are loaded by path from the repo, as are a small mock upstream and harness copied
  into `runtime_tests/relay_harness.py` (the repo's `tests/` isn't importable there). The stub
  `ScriptedModel` is reused.
- **Calls:** the granted call `github_list_issues` reaches upstream with the relay's credential
  (never the agent's token). The result reaches the model, and the audit records `ok: true` and
  `mcp: mcp/github@1.0.0`.
- **Filtering:** an ungranted declared tool isn't in the model's declarations.
- **Relay refusals:** when the relay refuses (for example, by narrowing the grant file after
  listing), the model sees an error result and the audit records `ok: false`.
- **Recovery:** after a relay restart, call N returns an error to the model (audit `ok: false`)
  and call N+1 re-initializes and succeeds.
- **Relay down:** the turn completes with no MCP tools. The trace and audit carry
  `mcp_unavailable: [github]`, and stderr has no traceback.
- **Token:** it never appears in the audit detail, the `/invoke` response, stderr or the relay's
  log.
- **Startup refusals:** a missing URL, a malformed token (whose value isn't in the error), a
  prefixed-name collision across servers or with a function tool, and a reserved tool name each
  give a `ConfigError`.
- **Shutdown:** closing the toolsets sends `DELETE`, and the relay drops the session.
- Dropped from the plan: "an ungranted tool called by the model never reaches upstream". ADK
  fails that turn before any callback, and the relay side is already covered by
  `test_ungranted_call_never_reaches_upstream_and_logs_a_hash`.

**CI:** a new `runtime-adk` job in `.github/workflows/ci.yml`. It uses Python 3.12, installs
`pip install --require-hashes --only-binary=:all: -r .../1.1.0/runtime/requirements.lock` plus a
pinned pytest, and runs `pytest .../1.1.0/runtime_tests`. These tests are the main proof, so they
must run in CI; until now runtime tests were run by hand.

**Repository tests:** the `build` capability check (done), and the built-in listing includes
`runtime/adk@1.1.0`.

**Opt-in real Docker test** (`tests/test_adk_mcp_docker.py`, `AGENTBREEDER_DOCKER_TEST=1`):
- **The stack:**
  - `postgres-pgvector`;
  - the mock MCP service from #745's test, as an `mcp:` entry;
  - a stdlib OpenAI-compatible fake gateway as a project service. It returns a
    `mock_list_issues` tool call, then a final text.
- **The agent:** `agentbreeder build` of a minimal `agent.yaml` (`runtime/adk@1.1.0`).
- **Running it:** the agent container runs on the agents network, with credentials mounted the
  way deploy does:
  - `AGENTBREEDER_MCP_MOCK_TOKEN_FILE`, a read-only secret file;
  - `AGENTBREEDER_MCP_MOCK_URL`;
  - the grant, written with `mcp.write_grant` into the stack's `grants/<env>/mock/`;
  - the gateway URL and `DATABASE_URL`.
- **Assertions:**
  - `POST /invoke` answers.
  - The mock counted exactly one `list_issues` call, made with the relay's credential.
  - The canary credential isn't in the agent's logs or `docker inspect`.
  - The agent token isn't in the relay's logs.
- **Scope of the claim:** "an agent built from `agent.yaml` calls an MCP tool through the relay".
  The full `deploy` path (the identity gate and the `_grant_mcp` mount) is unit-tested in #744
  and checked live in #748.

### 4. Docs and claims

- **`docs/agent-yaml.md`:** with `runtime/adk@1.1.0`, `mcp:` tools are callable through the stack
  relay. Tool names are `<server>_<tool>`. `MCP_RUNTIME_UNSUPPORTED` applies on older runtimes.
- **`docs/services.md`:** the preview note is updated to the verified claim above.
- **The website** (`services.mdx`, `what-ships.tsx` if it mentions MCP) and `ROADMAP.md`.
- **Spec review log r13** records:
  - the prefix correction (§7.7.7);
  - that `<NAME>` derives from the ref name (§7.7.10);
  - the timeout split;
  - the shared per-token session and limits;
  - one failed call after a relay restart;
  - that ADK's own degrade is kept, and audited;
  - that the relay contract depends on ADK's `_MCP_MODERN_PROTOCOL` staying off;
  - `capabilities`;
  - the scope of the Docker test.

  It also adds to the §7.6.13 claims that MCP tool arguments go to audit detail and OTLP spans,
  and that upstream results go to spans (Phoenix).
- **Northwind** stays on `adk@1.0.6` (it has no `mcp:`), so neither its lock nor its 61/61 run
  changes.

### 5. Cross-repo

Before the PR, grep agentbreeder-cloud for `runtime/adk`, `capabilities` and
`MCP_RUNTIME_UNSUPPORTED`.

## Sequencing

1. Copy `1.0.6` to `1.1.0`, update the version strings and manifest, and get the copied runtime
   tests green.
2. `build` capability check, tests first.
3. `runtime/mcp.py` and the `agent.py` wiring, with runtime tests first (in the container).
4. The opt-in Docker end-to-end test.
5. Docs, spec r13, and the full suite and ruff.
6. Different-model review (`/pair-loop`, reviewer claude-fable-5-1), then the gate, then the PR.

## Out of scope

- Remote MCP upstreams with live credentials, and the GitHub built-in (#748).
- The forward proxy for browser upstreams (#747).
- Per-tool policy and approvals (#742).
- Moving Northwind to 1.1.0.

## Review log

**Round 1** (plan, claude-fable-5-1). 12 findings, all accepted and folded in above:
- Four load-bearing library claims were wrong:
  - ADK already degrades a failing toolset;
  - `timeout` doesn't bound `tools/call`;
  - MCP failures are `{"error"}` results, so the audit's `ok` was wrong;
  - the call made after a relay restart fails once.
- The redaction, SIGTERM and CI mechanics were made concrete.
- The built-in tool's name is `search_knowledge_base`, and reserved and cross-server prefix
  collisions are now checked.
- The unknown-tool test case is dropped.
- The spec r13 items above were added.

**Round 2** (plan, claude-fable-5-1): OK to implement after three corrections, all folded in:
- SIGTERM: shut down from a worker thread (calling `shutdown()` from `serve_forever`'s thread
  deadlocks);
- the logging handler covers the whole `google_adk` hierarchy;
- a failed server is remembered per turn, so later model steps don't reconnect or audit again.

Also adopted from its low-severity suggestions: MCP tools carry `agentbreeder_mcp` (their server
ref) for the audit; the copied test harness names its source.

## Implementation notes

- **Module name:** the runtime module is `mcp_client.py`, not `mcp.py`. The runtime directory is
  first on `sys.path`, and a local `mcp.py` would shadow the `mcp` package ADK imports.
- **Relay 1.0.1:** the in-process tests found that relay 1.0.0's JSON-RPC body on its 404 stops
  ADK recovering after a relay restart (see spec r13). `service/mcp-relay@1.0.1` answers with a
  bare 404, and `RELAY_REF` points to it.
