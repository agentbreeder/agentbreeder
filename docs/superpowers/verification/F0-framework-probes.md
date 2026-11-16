# #1084 F0 verification: framework probes (no product code)

Status: **partial** (run 2026-11-16 against `main` at b4a4d744, macOS 13.7.8 x86_64, Docker Desktop
engine 27.5.1, Python 3.12.8, uv 0.11.6). F0 of the multi-framework spec
(`docs/superpowers/specs/2026-11-16-multi-framework-agents-design.md`, §16) asked for a probe of
every *(F0)* fact. This record holds what ran. The five frameworks were probed in throwaway venvs
against a local LiteLLM 1.106.2 gateway with real Gemini, OpenRouter and OpenAI keys. Each one
then ran a two-session Northwind-shaped turn in a container with the engine's Docker limits.

Not run:
- **arm64:** any arm64 run (the host is amd64; the arm64 locks resolve but were not installed).
- **Kubernetes:** any Kubernetes run (no cluster grant was asked for; `/tmp` use was measured instead).
- **A real Anthropic model:** the Anthropic key is not usable, so the Claude runtime ran on `support-model` through
  LiteLLM `/v1/messages`.
- **The Claude CLI's group kill:** `setsid` in the wrapper, and killing the CLI at a timeout or on
  SIGTERM, were not run.
- **The LiteLLM image:** the gateway was the LiteLLM 1.106.2 Python package in a venv, not the
  `service/litellm` image (same version). Presidio was a regex stub speaking the Presidio REST API,
  so the guardrail rows test LiteLLM's hook and its bypass fields, not Presidio's recognisers.

Nothing was left running. Docker was returned to the pre-run snapshot of images and volumes. The
provider key files (0600, in the session scratch directory) were deleted after the runs.

**The record found eight things the spec must change before F1** (§ Spec deltas). The largest:
- The step cap's "last call with function calling off" does not work for `support-model`. Gemini
  through LiteLLM ignores `tool_choice: none` on both chat and `/v1/messages`.
- The Claude CLI read a secret file through an `@path` mention, with every tool off, unless
  `verbatim_prompts` is set.
- CrewAI writes every customer message to a SQLite file under `HOME`.

This is a probe record. Nothing here is product code, and nothing is on `main` beyond this file.

## Set-up

- **Gateway:** `litellm[proxy]==1.106.2` (the version in `service/litellm@1.0.0`/`1.1.0`) in a
  Python 3.12 venv, with Postgres from `pgserver` 0.1.4 on loopback. The config was Northwind's
  `support-model` (Gemini `gemini-3.8-flash`, OpenRouter fallback) plus `probe-openai`
  (`openai/gpt-5.6`) for comparison, with `drop_params`, `turn_off_message_logging` and two
  `default_on` `pre_call` Presidio guardrails shaped like G1b: `pii-mask` (MASK `EMAIL_ADDRESS`,
  `PHONE_NUMBER`) and `pii-block` (BLOCK `US_SSN`).
- **Deployment keys,** minted with `gateway_keys.mint_body`'s fields only (alias, models, budget,
  rpm, metadata `{agentbreeder: true}`):
  - `plain`, with no `allowed_routes`;
  - `guard`, with `allowed_routes` = `GUARDED_ROUTES`;
  - `msgs`, with `GUARDED_ROUTES` plus `/v1/messages`.
- **A recording proxy** on loopback stood where the kit's proxy will. It logged each call's path,
  model, `stream`, tool names, `tool_choice`, a hash of the system prompt, the API key the
  framework sent (the turn token), the body's keys, usage and the cost header. It then forwarded
  the call with the deployment key.
- **Each framework ran in its own venv** with two concurrent sessions. Each session had a different
  turn token set in a `contextvars.ContextVar`, which an `httpx.Auth` (or CrewAI's interceptor)
  put in `Authorization`. The tools were `get_order` and `search_knowledge_base` from raw JSON
  Schema. Each tool recorded the token it saw. An audit hook recorded every non-loopback
  `socket.connect` and `getaddrinfo`. The OpenInference instrumentor exported to an in-memory span
  exporter.
- **For the Claude runtime,** a scripted Anthropic upstream returned, call by call, a `tool_use` for
  any named tool, so the CLI's refusals could be seen without a model. A decoy key file stood in for
  the gateway key.
- **Container runs:** one image per framework (`python:3.12-slim`, the amd64 lock installed with
  `--require-hashes --no-deps --only-binary :all:`, user 65532). Each ran with the engine's flags
  from `runtime.py`: `--read-only --cap-drop ALL --security-opt no-new-privileges --memory 512m
  --cpus 1 --pids-limit 128 --tmpfs /tmp:rw,noexec,nosuid,size=64m`. `HOME=/tmp`, as the ADK
  1.3.0 image sets. Peaks were read from the container's cgroup (`memory.peak`, `pids.peak`).
- **Versions** were resolved with `--exclude-newer 2026-11-02` (the two-week rule).

## Versions and locks

| Runtime | Pins probed | Lock (py3.12, wheels only, hashes) |
|---|---|---|
| LangGraph | `langgraph` 1.2.19, `langchain` 1.4.8, `langchain-core` 1.7.4, `langchain-openai` 1.7.3, `openinference-instrumentation-langchain` 0.1.81 | 52 packages, amd64 and arm64 both resolve |
| Deep Agents | `deepagents` 0.7.32 plus the LangGraph set | 67 packages, both resolve |
| OpenAI Agents | `openai-agents` 0.24.0, `openai` 3.42.1, `openinference-instrumentation-openai-agents` 2.7.0 | 48 packages, both resolve |
| CrewAI | `crewai` 1.15.34, `openinference-instrumentation-crewai` 1.1.22 | 142 packages (pulls `chromadb` 1.5.9, `lancedb` 0.41.0, `onnxruntime` 1.31.1, `openai` 2.54.0), both resolve |
| Claude Agent SDK | `claude-agent-sdk` 0.2.177 (bundles Claude Code CLI 2.1.317), `openinference-instrumentation-claude-agent-sdk` 0.1.24 | 41 packages, both resolve |

- The locks were made with `uv pip compile -p 3.12 --python-platform {x86_64,aarch64}-manylinux_2_28
  --only-binary :all: --generate-hashes --exclude-newer 2026-11-02`. They are not committed, because
  each runtime slice (F3–F7) makes its own.
- **CrewAI on this macOS 13 host:** `lancedb` 0.41.0 and `onnxruntime` 1.31.1 have no wheel, so an
  unpinned `crewai` resolved down to 1.9.3 here, or tried to compile `lancedb` from Rust source.
  The Linux targets get 1.15.34 with wheels. The venv probe used the Linux-resolved set without
  `lancedb` and `onnxruntime` (only CrewAI's memory and RAG storage import them). The container
  run used the full amd64 lock.
- **Image sizes** (Docker's figure, base included) were LangGraph 77 MB, Deep Agents 90 MB, OpenAI
  Agents 73 MB, CrewAI 365 MB and Claude Agent SDK 220 MB. Build times were 26–145 s.

## LiteLLM `/v1/messages` with `support-model` (D6, D17)

All rows use real Gemini calls through the gateway.

| Check | Result |
|---|---|
| Plain call, non-streaming | 200; Anthropic shape; `usage` present; `x-litellm-response-cost`, `x-litellm-model-group: support-model` and `x-litellm-attempted-fallbacks: 0` present |
| `stream: true` | 200, `text/event-stream`, the six Anthropic event types. **No cost header** |
| Tool use (`tool_choice: {type: any}`) | `stop_reason: tool_use`. The block id is `call_…`, not `toolu_…`, and carries `provider_specific_fields.signature` |
| `tool_result` round trip | 200, the model answered from the result |
| History with `tool_use` and tools removed | 200 (Gemini does not refuse it) |
| **`tool_choice: {type: none}`**, with tools kept and a follow-up that invites a call | **Ignored: `tool_use` 3/3,** also 3/3 through `support-model-openrouter`. `probe-openai`: `end_turn` 3/3. (The first run's A5 check had no follow-up, so the model had nothing left to call and stopped; it is superseded by this row.) |
| `system` + `thinking` | 200. No thinking block came back |
| Key with `GUARDED_ROUTES` on `/v1/messages` | **403** "Virtual key is not allowed to call this route" |
| Key with `/v1/messages` added | 200 |
| Guarded key on `/v1/chat/completions` (control) | 200 |
| BLOCK (SSN) in the user message, in `system` and in a `tool_result` | 400 `Blocked entity detected: US_SSN by Guardrail: pii-block`; cost header 0 |
| BLOCK, streamed | 400 Anthropic error event |
| MASK (email, phone) | 200. The model repeated `<EMAIL_ADDRESS>` and `<PHONE_NUMBER>`, so the provider saw tags. Masking of the model's output was not tested separately |
| 12 bypass attempts with the SSN: body `guardrails: []` and `["pii-mask"]`, `metadata.guardrails`, `litellm_metadata.guardrails`, `disable_global_guardrails` (body, metadata), `opted_out_global_guardrails`, a forged `metadata.user_api_key_metadata`, `mock_response`, `litellm_disabled_callbacks`, headers `x-litellm-disable-guardrails` and `x-litellm-guardrails` | **All 400, blocked** |

The same `tool_choice` check on the chat path, with tools kept and history holding a tool call
plus a follow-up, gave:
- `support-model`: `tool_calls` 3/3 with `none`;
- `support-model-openrouter`: 3/3 with `none`;
- `probe-openai`: `stop` 3/3 (honoured).

## LangGraph (`runtime/langgraph`)

- **Model:** `ChatOpenAI(model=alias, base_url=…, http_async_client=…, http_client=…,
  max_retries=0)` works. The agent was `langchain.agents.create_agent(model, tools=…,
  system_prompt=…)` (LangChain 1.4.8). The spec's `create_react_agent` was not needed.
- **Tools:** `StructuredTool.from_function(coroutine=…, args_schema=<JSON Schema dict>)` works,
  and so does `func=` for sync tools.
- **Contextvar:** the turn token reached every model call and every tool call under two concurrent
  sessions. That held with async tools, with sync tools run in LangGraph's executor threads, and
  with a sync `invoke` in a worker thread. It also held with agents built once and reused across
  turns (code mode's shape).
- **Calls per turn:** 2 (tool calls, then answer). No hidden calls. Retry header 0, and 1 attempt
  against a 503 upstream.
- **Streaming:** `streaming=True, stream_usage=True` sends `stream_options.include_usage`. Streamed
  calls carry no cost header.
- **Usage:** `usage_metadata` per message, with Gemini's reasoning tokens in `output_token_details`.
- **Spans (OpenInference langchain):** LLM 4 (`ChatOpenAI`), TOOL 4, CHAIN 10 (`LangGraph`, `model`,
  `tools`).
- **Network:** none besides the proxy.
- **Loggers:** `langchain*`, `langgraph*`, `langchain_core*`, `langchain_openai*`, `openai*`, `httpx`,
  `openinference*`.
- **Container:** imports 2.0 s, peak memory 93 MB, 7 pids, `/tmp` 0 bytes.

## Deep Agents (`runtime/deepagents`)

- **Agent:** `create_deep_agent(model=<ChatOpenAI>, tools=…, system_prompt=…,
  backend=StateBackend())`. `StateBackend` is also the default when `backend` is `None`.
- **Built-in tools offered to the model:** `ls`, `read_file`, `write_file`, `edit_file`, `delete`,
  `glob`, `grep`, `task`. **That is `delete` added and no `write_todos`,** so §8.3's list is wrong for
  0.7.32. `execute` is not offered with `StateBackend`.
- **Scripted model (a fake chat model asking for each tool):**
  - `execute` → "Execution not available. This agent's backend does not support command
    execution";
  - `read_file /etc/passwd` → not found;
  - `ls /`, `glob /etc/*` and `grep root /etc` → nothing;
  - `write_file /tmp/…` → written to graph state only (no file on the host), then read back and
    deleted in state.
  
  The real model, asked to do the same, refused without calling a tool.
- **`task` sub-agent:** its calls had a different system-prompt hash and the tool set without
  `task`. So §7.2.4's main-loop test (prompt hash plus exact tool set) tells them apart. A
  delegated lookup made 4 calls (2 main, 2 sub-agent).
- **Prompt size:** about 2.6k prompt tokens per call (Deep Agents' own system prompt and tool
  descriptions), against 176 for LangGraph.
- **Contextvar:** as LangGraph, held. 2 calls per Northwind turn. Retry header 0.
- **Spans:** as LangGraph, plus AGENT spans for its middleware (`PatchToolCallsMiddleware…`).
- **Network:** none. Loggers `deepagents*` plus LangGraph's.
- **Container:** imports 2.2 s, 99 MB, 10 pids, `/tmp` 0.

## OpenAI Agents SDK (`runtime/openai-agents`)

- **Model:** `OpenAIChatCompletionsModel(model=alias, openai_client=AsyncOpenAI(base_url=…,
  http_client=…, max_retries=0))` works.
- **Tools:** `FunctionTool(…, params_json_schema=…, strict_json_schema=False)` works.
- **Usage:** `ModelSettings(include_usage=True)` gives usage in `result.context_wrapper.usage`.
- **Contextvar:** held. 2 calls per turn. Retry header 0, and 1 attempt against a 503.
- **Built-in tools** (`ShellTool`, `LocalShellTool`, `CodeInterpreterTool`, `ComputerTool`,
  `ApplyPatchTool`, `FileSearchTool`, `HostedMCPTool` and others) exist only when added to
  `Agent.tools`. None is on by default. `RunConfig` also has a `sandbox` field (not used).
- **Tracing, a correction to §8.1:**
  - **`set_tracing_disabled(True)` also stops OpenInference.** The instrumentor plugs into the SDK's
    own trace processors, so no spans were exported.
  - The instrumentor's default `exclusive_processor=True` replaces the SDK's default OpenAI exporter.
  - With a dummy `OPENAI_API_KEY` set and the default, nothing left the host and all spans were
    exported.
  - With `exclusive_processor=False`, the SDK connected to OpenAI's trace endpoint (it got a 401).
  
  So the runtime must keep tracing on and pass `exclusive_processor=True` (the default) explicitly.
- **Spans:** LLM 4 (`generation`), TOOL 4, CHAIN 6, AGENT 4. `tool_span_kinds` can map a tool span
  only to `RETRIEVER`, not `CHAIN`, so §7.3's relabel needs the kit's own span processor.
- **Loggers:** `agents*`, `openai*`, `openai.agents`, `openinference*`.
- **Container:** imports 4.1 s, 100 MB, 6 pids, `/tmp` 0.

## CrewAI (`runtime/crewai`)

- **Model:** `LLM(model="openai/support-model", base_url=…, api_key=…, max_retries=0)` makes a
  native `crewai.llms.providers.openai.completion.OpenAICompletion` with the OpenAI SDK.
  **`litellm` is not in its dependencies,** so §8.5's LiteLLM branch and
  `LITELLM_LOCAL_MODEL_COST_MAP` do not apply.
- **The turn token:** `LLM` takes no `http_client`, but takes an `interceptor` (a
  `BaseInterceptor[httpx.Request, httpx.Response]`) whose `on_outbound` set the token from the
  contextvar.
- **Contextvar:** held in both `kickoff()` in worker threads and `kickoff_async()`. Tools ran in
  `ThreadPoolExecutor` threads and saw their session's token.
- **Calls per turn:** 2 (`tool_choice: auto` is always sent). No final-answer or formatting call
  was seen in these turns.
- **Retries: `max_retries=0` is not enough.** Against a 503 upstream, CrewAI made **3 attempts per
  turn** (`run_with_rate_limit_retry` in `agent_utils.py`, above the client). §7.2.7's "answer every
  later call of the turn at once with the same status" covers this.
- **Telemetry:** `CREWAI_DISABLE_TELEMETRY` and `CREWAI_DISABLE_TRACKING` exist and are honoured
  (`crewai_core.telemetry._is_telemetry_disabled`, which also honours `OTEL_SDK_DISABLED`).
  Trace upload is a separate switch, `CREWAI_TRACING_ENABLED=false`. No non-loopback connection
  was seen with all three set.
- **Storage:**
  - **Without `HOME` it does not start:** on a read-only root, import fails with `OSError: [Errno 30]
    Read-only file system: '/.local'` (`crewai_core/paths.py:db_storage_path`, called at import).
  - With `HOME=/tmp` it wrote two files under `HOME` on every run, even with `memory=False`:
    `…/<CREWAI_STORAGE_DIR or cwd name>/latest_kickoff_task_outputs.db` and
    `crewai/credentials/secret.key`.
  - **The SQLite file holds every task's inputs and output, so the customer's message and the
    answer** (checked: it contained `A-1001`). It is a store of customer text outside `turns`.
- **Instrumentor:** `openinference-instrumentation-crewai` 1.1.22 gave TOOL 4, AGENT 2 and CHAIN 2,
  and **no LLM spans.** The kit must write the LLM spans from the proxy, as for Claude.
- **Loggers:** `crewai*`, `crewai_core*`, `chromadb*` (imported even with memory off), `openai*`,
  `openinference*`. CrewAI logs a WARNING per call about approximate token counts for an unknown
  model.
- **Container:** imports 4.6 s, peak 178 MB, 18 pids, `/tmp` 20 KB.

## Claude Agent SDK (`runtime/claude-agent-sdk`)

### Package, CLI and licence (D8)

- **Packaging:** `claude-agent-sdk` 0.2.177 wheels are per platform (`manylinux_2_17` x86_64 and
  aarch64, macOS, Windows; 129–143 MB). Each bundles the Claude Code CLI as a native executable,
  `claude_agent_sdk/_bundled/claude` (`_cli_version.py`: 2.1.317), 327 MB on macOS x86_64. No Node
  install is needed. The same CLI is on npm as `@anthropic-ai/claude-code` 2.1.317 with
  per-platform optional packages and a `postinstall` script.
- **The SDK wheel** is MIT (`License: MIT`, `licenses/LICENSE`, "Copyright (c) 2025 Anthropic,
  PBC").
- **The CLI's own terms** (npm `LICENSE.md`): "© Anthropic PBC. All rights reserved. Use is subject
  to the Legal Agreements outlined here: https://code.claude.com/docs/en/legal-and-compliance".
- **What that page and the pages it links say** (read 2026-11-16: the legal-and-compliance page, the Commercial Terms, the Consumer Terms and the Agent SDK overview):
  - Claude Code is governed by the Commercial Terms for API, Team and Enterprise users, and the
    Consumer Terms for Free, Pro, Max and Ultra users.
  - The Agent SDK overview says its use is governed by the Commercial Terms.
  - "Preinstalling or running Claude Code in your products or services (e.g. in hosted sandboxes or
    other agent infrastructure) requires agreeing to our Commercial Terms of Service."
  - "The Claude Code binary must not be modified" and it "must be installed and run as published".
  - Each end user authenticates with their own API key, subscription or third-party provider
    credentials, and customers "may not pay for, resell, or intermediate Claude usage on their end
    users' behalf".
  - The pages are **silent** on third parties building container images for others, and on
    `ANTHROPIC_BASE_URL` pointing at a non-Anthropic gateway or model.
- **What F0 cannot settle:** this record does not interpret the terms. Whether an image that
  `agentbreeder build` makes, with the CLI in it, is "preinstalling … in your products or
  services", and how the end-user authentication condition applies when the model behind the
  gateway is not Anthropic's, is the owner's call (with legal advice if needed).

### What a Northwind turn sends (D6)

- With `support-model` behind LiteLLM `/v1/messages` and the scrubbed wrapper below, two concurrent
  sessions each answered correctly. Each made 2 calls (`num_turns` 3).
- Both kit tools ran in-process through `create_sdk_mcp_server`, with the session's turn token.
- **Every call was `stream: true`.** The body keys were `context_management`, `max_tokens`
  (32000), `messages`, `metadata`, `model`, `output_config`, `stream`, `system`, `thinking` and
  `tools`. The user agent was `claude-cli/2.1.317 (external, sdk-py, agent-sdk/0.2.177)`. The
  tools sent were the two kit tools plus a `DeferredToolPlaceholder` entry.
- **The CLI adds its own text to every request.** It is a user-role "Environment" block with the
  working directory, platform, shell, OS version and the model name, followed by the date.
- The SDK's `ResultMessage.total_cost_usd` (0.014 per turn) is computed with Anthropic prices and
  is wrong for Gemini.
- **`can_use_tool` is never consulted** for tools in `allowed_tools`. The SDK warns:
  `CanUseToolShadowedWarning … use a PreToolUse hook`.

### §8.4 controls against CLI 2.1.317 (D15)

**Scrubbed environment:**
- `ClaudeAgentOptions(env=…)` **merges over the parent environment**
  (`subprocess_cli.py:819-823`). Without a wrapper, the CLI process held `LLM_GATEWAY_KEY_FILE`,
  `DATABASE_URL`, `AGENTBREEDER_TRACE_URL` and a test variable (checked with `ps eww` on macOS).
- With `cli_path` set to an `env -i` wrapper that passes an allow-list, the CLI's
  `/proc/<pid>/environ` in the container held only these names:
  - `PATH`, `HOME`, `CLAUDE_CONFIG_DIR`, `TMPDIR`;
  - `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`;
  - the six model variables;
  - `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_AGENT_SDK_VERSION`, `CLAUDE_CODE_SDK_READS_SESSION_STATE`;
  - the telemetry and traffic switches.
- The SDK needs `CLAUDE_CODE_SDK_READS_SESSION_STATE=1` passed through. It sets it, and the wrapper
  must keep it.

**Built-in tools off:**
- With `tools=[]` the init message lists only the two `mcp__agentbreeder__…` tools.
- A scripted model asked, one call each, for 25 names, against the decoy key file (macOS) and
  against `/run/secrets/gateway-key` mounted read-only in the container: Bash, Read, Write, Edit,
  Glob, Grep, WebFetch, WebSearch, Task, Agent, NotebookEdit, TodoWrite, Skill, ToolSearch,
  DeferredToolPlaceholder, LSP, Monitor, KillShell, BashOutput, EnterPlanMode, ExitPlanMode,
  SendMessage, CronCreate, an unknown `mcp__agentbreeder__…` and another server's `mcp__…`.
- Every one came back `is_error: true`, "No such tool available: X. X is disabled for this
  session, in subagents as well as here." The file was unchanged, and `can_use_tool` was never
  called.
- **`tools=[]` alone is enough.** The same refusals came with `disallowed_tools` empty, so the
  disallow list is a second layer.

**Slash commands and `@` mentions:**
- **Without `verbatim_prompts`:**
  - `/config`, `/mcp`, `/model opus`, `/compact` and `/clear` ran **in the CLI with no model call**
    (`/config` printed its settings usage, `/model` switched the session model);
  - `/help` answered "isn't available in this environment"; `/init` went to the model as
    its literal text; `/deep-research acme returns` went to the model expanded as a skill (the
    literal text was not in the request);
  - **`@/tmp/…/gateway-key what is in this file?` made the CLI run its Read tool, despite
    `tools=[]` and Read in `disallowed_tools`.** It sent the file's content to the model as "Called
    the Read tool … Result of calling the Read tool: DECOY-GATEWAY-KEY-f0". Retained in `e1-plain-*` (see Evidence). `@/etc/hosts` was read the same way. In the container, against the read-only `/run/secrets` mount, the upstream log held the decoy text in 1 request without `verbatim_prompts` and in 0 with it. That run then failed in the probe's own orphan check (no `pgrep` in the image), and its log was not kept, so the container result is reported but not retained.
- **With `ClaudeAgentOptions(verbatim_prompts=True)`:** all ten inputs reached the model as literal
  text, and no file was read (retained for macOS in `e1-verbatim-*`; the container repeat is
  reported above but not retained). The SDK documents this option as "no
  `@path` file-mention expansion and no slash-command dispatch".
- `init.slash_commands` still lists 44 built-in commands and skills (`config`, `compact`,
  `init`, `deep-research`, `claude-api` and others) with `setting_sources=[]`. The same names appear in a clean container, so they ship with the
  CLI and are not the host's.

**History:**
- Streamed input accepted an `assistant` message but **reordered it.** The upstream got
  `[assistant, user+user]`, with the assistant turn first and the two user messages merged. That
  is not faithful history.
- **`resume=<session_id>` with the turn's config directory kept** gave the next call a correct
  `user, assistant, user` history.
- The transcript is the CLI's own JSONL (entry types such as `queue-operation`, `user`,
  `attachment`, `api-request-blob`, `assistant`, `cost-state`). That format is undocumented.
- `session_store` (a mirror adapter) received no entries in this probe, and the reason was not
  found.

**Process group and transcripts:**
- The CLI runs in the **parent's process group.** `anyio.open_process` is not given a new session,
  so §8.4's kill-the-group needs the wrapper to `setsid`.
- No CLI process was left after any run.
- The per-turn directory held `.claude/.claude.json`, a backup of it, a built-in plugin store
  file, the session transcript (customer text) and `.cache/claude-cli-nodejs/…/mcp-logs-…`. It was
  36 KB after a real two-call turn, and was deleted at turn end.

**Network:**
- With `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `DISABLE_AUTOUPDATER` and
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`: no connection besides the base URL, and one model
  call per scripted turn.
- Without them: one **hidden model call** per turn (no tools, a 3.3k-character system prompt) and a
  `HEAD /api/hello` to the base URL.

**Retries:**
- Default: 11 attempts over 243 s against a 503, which is longer than the 170 s turn.
- `CLAUDE_CODE_MAX_RETRIES=0`: 1 attempt, but the run still took 63 s to end. The kit's turn
  deadline and group kill are needed regardless.

**Instrumentor:** `openinference-instrumentation-claude-agent-sdk` 0.1.24 gave TOOL 4 and AGENT 2,
and **no LLM spans.**

### Resources (D16)

| Concurrent Claude turns in one container | Peak memory (cgroup) | Peak pids | Result |
|---|---|---|---|
| 1 (scripted) | 194 MB | 9 | ok |
| 2 (real `support-model`) | 305 MB | 17 | ok, run took 8.1 s |
| 4 (scripted) | 462 MB | 32 | ok, run took 3.8 s |
| 6 (scripted) | **512 MB (the limit)** | 44 | all 6 answered; the run took 7.2 s against 3.8 s for 4. Whether the kernel OOM-killed a process was not checked; no turn failed |

The 4- and 6-turn rows used the scripted upstream (one short call each), so they are a floor: real turns with longer context would use more. 3 concurrent turns were not run. Each CLI's `VmHWM` was 34–98 MB. The binary sits in `site-packages`, not `/tmp`, so the `noexec`
`/tmp` did not matter. `/tmp` use was 36 KB per turn. The SDK imports in 1.1 s, and the CLI
answered its first call within about 1 s.

## Across frameworks

| | LangGraph | Deep Agents | OpenAI Agents | CrewAI | Claude Agent SDK |
|---|---|---|---|---|---|
| Turn token by contextvar, 2 concurrent sessions | yes | yes | yes | yes (interceptor) | per-turn env (wrapper) |
| Hidden calls in a Northwind turn | 0 | 0 (2 with `task`) | 0 | 0 | 0 with the traffic switches, 1 without |
| Attempts against a 503 with retries off | 1 | 1 | 1 | **3** | 1 (`CLAUDE_CODE_MAX_RETRIES=0`), 11 by default |
| LLM spans from the instrumentor | yes | yes | yes | **no** | **no** |
| TOOL spans | yes | yes | yes | yes | yes |
| Imports in the container | 2.0 s | 2.2 s | 4.1 s | 4.6 s | 1.1 s (plus about 1 s per CLI start) |
| Peak memory, 2 sessions, in 512 MB | 93 MB | 99 MB | 100 MB | 178 MB | 305 MB |
| Peak pids, in 128 | 7 | 10 | 6 | 18 | 17 |
| Network besides the proxy (venv audit hook) | none | none | none (exclusive processor) | none (3 switches) | none (4 switches) |
| Writes outside the turn | none | none | none | **SQLite of task inputs and outputs, credentials key** | per-turn config dir (deleted at turn end) |

All are far inside deploy's 30 s health wait. Kubernetes' memory-backed `/tmp` (64 Mi emptyDir,
counted in the 512 Mi limit) was not run. The `/tmp` use above (20 KB at most for Python, 36 KB per
Claude turn) suggests it does not matter.

## Decisions F0 informs

| # | What F0 found | Recommendation for the owner |
|---|---|---|
| D6 | `support-model` through LiteLLM `/v1/messages` runs the Claude CLI's main loop, tool use and resumed history. It is not like for like in one way: `tool_choice: none` is ignored (as on chat, below). No thinking block is returned | **`support-model`**, as the spec recommends |
| D8 | The CLI's licence reads "All rights reserved"; it ships inside every platform wheel of the SDK. Its use is governed by the Commercial Terms or the Consumer Terms depending on how the user authenticates, and the SDK overview names the Commercial Terms. "Preinstalling or running Claude Code in your products or services" needs those terms; the binary must not be modified; end users authenticate with their own credentials. Nothing is said about images built for others or non-Anthropic models | Owner's call (F0 does not interpret terms). If unclear, the spec's fallback stands: the user installs it into their own build, and it stays out of `builtin_registry` |
| D15 | Each control that was run held against 2.1.317: the `env -i` wrapper, `tools=[]` (with and without `disallowed_tools`), `verbatim_prompts=True`, `setting_sources=[]`, `strict_mcp_config` and the traffic switches were run together; `CLAUDE_CODE_MAX_RETRIES=0` was run separately. **`setsid` and the group kill were not run** (the CLI shares the parent's group; no orphan was seen after normal turns, and no timeout or SIGTERM was tested). **But the `@path` result is the D15 residual happening: a CLI feature outside the tool layer read a same-uid secret file.** One option closes it today, and a future CLI version could add another such path | Owner's call. If shipped, `verbatim_prompts=True` and the wrapper are mandatory, and the conformance kit must send `@<secret path>` and slash commands on every CLI version. The two-uid contract removes the class |
| D16 | The four Python frameworks peak at 93–178 MB and 6–18 pids for two sessions. The Claude runtime peaks at 305 MB for 2 real turns, 462 MB for 4 scripted turns, and hits the 512 MB limit at 6 scripted turns | The Python runtimes need no change. For the Claude runtime, a cap on concurrent turns (a runtime setting, not a deploy limit) may avoid D16, but the cap's value is **interpolated and untested**: 3 turns were not run and the scripted peaks are a floor. F7 must measure it with real turns before choosing between a cap and D16 |
| D17 | With a regex Presidio stub (LiteLLM's hook, not Presidio's recognisers): blocking acts on `/v1/messages` in user, system, `tool_result` and streamed requests, and input masking acts. None of 12 bypass fields or headers switched them off. A `GUARDED_ROUTES` key gets 403 there, and one with `/v1/messages` added works. Output masking was not tested here (G0's §12.4 row records it as masked on 1.106.2) | **The evidence supports the condition for blocking and input masking:** add `/v1/messages` to keys of deployments whose runtime has `model_api: anthropic-messages`. F7's conformance should cover output masking and the real Presidio images on this route |

## Spec deltas (to fold into the spec before F1)

1. **§7.2.4, the step cap does not work as written for `support-model`.**
   - Gemini, direct or through OpenRouter via LiteLLM 1.106.2, ignores `tool_choice: none` on chat
     and on `/v1/messages` (3/3 each). OpenAI honours it.
   - So the Nth call can return another tool call. The proxy needs its own rule for an Nth response
     that still holds tool calls. For example, keep its text and drop the calls, or answer
     `MODEL_STEP_LIMIT`.
   - ADK 1.3.0's last call uses the same mechanism (`FunctionCallingConfig(mode="NONE")`, which
     LiteLLM turns into `tool_choice: none`), so its cap may not hold on Gemini either. **ADK itself
     was not tested; this needs its own issue.**
2. **§7.2.3, Anthropic fields:** the CLI sends `context_management`, `metadata`, `output_config`
   and `thinking`. The allow-list must decide each one; `metadata` carries a user id and should be
   dropped. Every CLI call is `stream: true`, so the SSE synthesis (D7) is on the Claude runtime's
   main path.
3. **§7.2.8:** the guardrails spec's §12.4 table does have a `/v1/messages` row (BLOCK 400,
   provider saw tags, response masked; that request was sent on as `/v1/responses`), contrary to
   "nothing is known yet". G0 did not send `/v1/messages` with a route-limited key. F0 adds that
   (403 with `GUARDED_ROUTES`), `system`, `tool_result`, streaming and the bypass set, on a
   Gemini-backed alias.
4. **§8.1:** do not call `set_tracing_disabled(True)`; it stops OpenInference too. Keep tracing on,
   with the instrumentor's `exclusive_processor=True`. `tool_span_kinds` cannot relabel to `CHAIN`.
5. **§8.3:** the built-in tools in 0.7.32 are `ls`, `read_file`, `write_file`, `edit_file`,
   `delete`, `glob`, `grep` and `task` (no `write_todos`). `reserved_tool_names` must list `delete`.
   Expect about 2.6k prompt tokens per call.
6. **§8.4:**
   - add `verbatim_prompts=True` (mandatory) and `setsid` in the wrapper;
   - pass `CLAUDE_CODE_SDK_READS_SESSION_STATE`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` and
     `CLAUDE_CODE_MAX_RETRIES=0`;
   - `can_use_tool` is bypassed for allowed tools (use a `PreToolUse` hook if a per-call gate is
     wanted);
   - **history works only through `resume` of the CLI's own transcript.** Either keep a per-session
     (not per-turn) config directory, or write the CLI's undocumented format. The spec's "if it
     cannot, this runtime is not built" needs a decision on which;
   - the SDK's cost is wrong for non-Anthropic models (the proxy's tally is the source);
   - the "Environment" block the CLI adds to every request includes the working directory and OS
     version.
7. **§8.5:**
   - CrewAI 1.15 uses the native OpenAI SDK (no LiteLLM); the token goes through `interceptor=`;
   - `max_retries=0` still leaves 3 attempts;
   - `HOME` must be set (it does not start otherwise);
   - **its task-output SQLite stores customer messages.** The adapter must disable or redirect it
     per turn and delete it, or the runtime must not ship. That is §13.7's concern;
   - add `CREWAI_TRACING_ENABLED=false`;
   - its instrumentor has no LLM spans;
   - `lancedb`, `chromadb` and `onnxruntime` come in with the core package.
8. **§13.5:** the CLI ships inside the SDK wheel (pip-audit sees only the wheel). The wheel's hash
   in the lock pins the binary. The npm route does not apply when the wheel is used.

## Evidence retained

The first pass ran several checks inline and did not keep their output. A second pass on the same
day re-ran them with every report and every upstream request log kept
(`probe/evidence.sh`, outputs in `evidence/`). The raw files stay in the probe session's scratch
directory and are not committed, because their paths include a home directory. All results
reproduced:

| Group | What was re-run | Result |
|---|---|---|
| E1 | The ten slash and `@` inputs, with and without `verbatim_prompts` (20 runs, scripted upstream) | Without the option: 6 handled by the CLI with no model call; `@<decoy key>` sent the key text and `@/etc/hosts` the hosts file. With it: 10 of 10 reached the model as literal text, and neither file's text was sent |
| E2 | `tool_choice` none and auto, 3 times each, on chat and `/v1/messages`, for `support-model`, `support-model-openrouter` and `probe-openai` (36 real calls) | The two Gemini aliases returned a tool call 12 of 12 times with `none`; OpenAI returned text 6 of 6 |
| E3 | The Claude CLI against an always-503 upstream | 11 attempts in 243 s by default; 1 attempt in 63 s with `CLAUDE_CODE_MAX_RETRIES=0` |
| E4 | One scripted turn with and without the traffic switches | With: 1 model call. Without: 2 (one with no tools and a 3,331-character system prompt) and `HEAD /api/hello` |
| E5 | The scripted attack with `disallowed_tools` empty (Bash, Read, WebFetch, Task, DeferredToolPlaceholder) | All refused, "No such tool available" |
| E7 | LangGraph, Deep Agents, OpenAI Agents and CrewAI against the 503 upstream, two sessions each | 2, 2, 2 and 6 requests (one per session, three per session for CrewAI); retry header 0 throughout |

Not re-run, so reported from the first pass only: the container `@path` repeat, image sizes and
build times, and the 4- and 6-turn memory rows (their numbers are in the saved container reports).

## Leak scan and teardown

- **Keys and logs:** the key files and the master key were 0600 files in the session scratch
  directory, read by a launcher script and never printed. They were deleted after the runs. The
  gateway ran with `LITELLM_LOG=WARNING` and the recorder logged no bodies.
- **Docker:** the probes created five images (`f0probe/*`) and pulled `python:3.12-slim`. All six
  were removed. Images and volumes match the pre-run snapshot (`alpine:3.20` and the two Presidio
  images). No container is left.
- **Docker, not undone:**
  - the build cache grew from 0.72 GB to 3.26 GB and was not pruned, because a prune would also
    remove the cache entries that were there before;
  - the default `bridge` network has a new id (created 19:09 UTC). No network was created or removed
    by the probes; it looks like a daemon restart.
- **This record** was checked for home paths, usernames and tokens before commit.
