# Multi-framework agents: one `agent.yaml`, framework runtimes, shared registries (#1084): design

- Status: **r4, reviews converged** (round 4 APPROVED). The r1–r3 `/architect` and `/security` reviews (Fable, read-only) are folded
  in (§20). Owner decisions D1–D18 are open (§17). No code in this PR.
- Issue #1084, part of epic #1083 (Northwind on the OpenAI Agents SDK, LangGraph, Deep Agents, the
  Claude Agent SDK and CrewAI). Sibling spec: `2026-11-16-multi-framework-console-design.md`
  (#1085). Reuses #758 (agent protocol v1) and #759 (conformance kit) from epic #756. Its first
  framework slice is the #760 acceptance (a second framework passes conformance).
- Builds on (on `main` at 38cb7336): `agentfile.py` (authoring schema v1), `build.py`
  (`_runtime_serving`, `_runtime_capabilities`, `ADK_RESERVED_TOOLS`), `tool_check.py`, `registry.py`
  (`KINDS`, `CODE_KINDS`, `runs_code`), `builtin_registry/runtime/adk/1.3.0` (`agent.py`, `tools.py`,
  `server.py`, `rag.py`, `memory.py`, `telemetry.py`, `mcp_client.py`, `redaction.py`), `evals.py`
  (what a case reads from a reply), `phoenix_traces.py` (what the console reads from spans),
  `agentspec.py` (`VERIFIED`), the guardrails spec (`2026-11-15-guardrails-design.md`, G1a on
  `main`), the authenticated-invoke spec (#780) and the sub-agents and skills specs (#1058, #1059).
- **Nothing in this document has been run.** Framework behaviour quoted here comes from general
  knowledge of each library as of mid-2026, not from a pinned wheel. Slice F0 (§16) checks every
  such claim against the exact versions before any runtime code is written; each one is marked
  *(F0)*.

## 1. Problem

AgentBreeder builds agents from `agent.yaml` for one framework, Google ADK (`runtime/adk`). Teams
already write agents in the OpenAI Agents SDK, LangGraph, Deep Agents, the Claude Agent SDK and
CrewAI. Today they can deploy such an agent only as an opaque image (`deploy` is image-in). It then
gets the identity gate, approvals and audit, but not the registry, the eval-gated `release`, the
gateway's cost and fallbacks, coded model errors, the MCP relay, traces the console can read, or
Try it.

2.x had framework runtimes. They are not a base to build on (§3): none reached models through a
gateway, the tool, memory and tracing code was never copied into images, nothing was pinned, and
configuration and secrets were baked into image layers.

## 2. Goal

An agent author picks a framework with one line, `runtime: runtime/<framework>@X.Y.Z`. Everything
else in `agent.yaml` (prompt, model alias, tools, knowledge, MCP servers, memory, evals, budget)
means the same thing in every framework. The result is built, evaluated, released, deployed and
governed exactly as an ADK agent is, with no framework-specific code in the engine or the builder.

**Acceptance per framework:** Northwind (`src/agentbreeder/examples/northwind`), with the same
prompt, tools, knowledge and eval set, passes 61/61 through `release` on that framework, with a
recorded real run (§15). Only then is the framework called supported.

## 3. 2.x prior art (Fable review)

The owner asked for a review of the 2.x framework runtimes with the **Fable** model. Three
read-only `claude-fable-5-2` subagents read tag `v2.7.3` (via `git show`, never checked out) on
2026-11-15. One covered the runtime and build contract, one the server templates and tracing, and
one security, including cloud security. 2.x is **reviewed, not restored** (CLAUDE.md). Paths below
are at `v2.7.3`: `R/` = `engine/runtimes/`, `T/` = `engine/runtimes/templates/`.

### 3.1 What 2.x had

- **Four Python runtimes and one Node stub, near-identical copies.**
  - Validation was file existence only (`R/langgraph.py:71-85`, `R/crewai.py:71-78`,
    `R/openai_agents.py:72-79`).
  - Build copied the user's directory, merged its `requirements.txt` line by line with the
    framework's packages, copied one `server.py` template and wrote a Dockerfile.
  - The user's object was found by trying a list of names: `graph`, `app`, `workflow` or `agent` for
    LangGraph (`T/langgraph_server.py:129`), `flow` or `crew` for CrewAI (`T/crewai_server.py:131-134`),
    `agent`, `app`, `triage_agent` or `runner` for OpenAI Agents (`T/openai_agents_server.py:107,118`).
- **No Claude Agent SDK runtime.** `claude_sdk` wrapped the raw `anthropic` Messages client with a
  hand-written 10-iteration tool loop (`T/claude_sdk_server.py:6-11,284-404`). It never started the
  Claude Code CLI and had no built-in tools or permission modes.
- **No Python Deep Agents runtime.** The only one was a Node template importing the npm package
  `deepagent` at `latest` (`R/node.py:34,69`). LangChain's package is `deepagents`. The reviewer
  could not tell what `deepagent` resolves to, which is a package-confusion risk in itself.
- **No gateway routing anywhere.** `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL` and a LiteLLM URL appear
  in no runtime or template. The only effect of `model.gateway: litellm` was to skip adding the
  `litellm` pip package (`R/base.py:140-149`). Every framework used a direct vendor key:
  - OpenAI Agents called `set_default_openai_key` (`T/openai_agents_server.py:236-242`), and a
    custom `AsyncOpenAI(base_url=…)` only for Ollama (`:219-230`). `AGENT_MODEL` was logged but
    never applied (`:225`).
  - CrewAI changed `agent.llm.model` and `base_url` at startup inside `except Exception: pass`
    (`T/crewai_server.py:246-270`).
  - LangGraph left the model to user code (`R/langgraph.py:159-183`).
  - Claude refused LiteLLM model names at validation (`R/claude_sdk.py:90`).

### 3.2 What was wrong

**Critical**
1. **Memory and tracing never shipped.** No runtime copied `memory_manager.py` or `_tracing.py`
   into the image or added their packages (`R/langgraph.py:122-123`). The servers swallowed the
   `ImportError` (`T/langgraph_server.py:293-297,335-342`), so 2.x agent containers emitted no spans
   and kept no memory. `tests/integration/runtimes/test_memory_wiring_hr2.py:20-41` only searched
   the template source for a string, so it passed anyway.
2. **The tool bridge was dead.** Nothing produced `AGENT_TOOLS_JSON`; only the templates read it.
3. **Memory kept only the last turn** (`memory_manager.py:159-163`). Its key was
   `agentbreeder:memory:{agent}:{session}` in one shared table (`:128-129,193`), with no customer
   dimension, and it was replayed into the prompt as flat `[role] content` text
   (`T/claude_sdk_server.py:255-260`). That allowed stored injection and role spoofing.
4. **No timeouts, cancellation, retries or coded errors.** Callers got
   `500 {"detail": str(e)}` with the vendor's text. Some stream errors came back as a 200 with an
   error frame.

**High**
5. **Nothing was pinned.** The base image was `python:3.11-slim` by tag, packages were floors
   (`langgraph>=0.2.0`, `crewai>=0.80.0`, `openai-agents>=0.1.0`, `R/langgraph.py:166-167`,
   `R/crewai.py:184-185`, `R/openai_agents.py:169-170`), and Node packages were `latest`. A user's
   `crewai==0.70` and the injected `crewai>=0.80.0` both ended up in the merged requirements
   (`R/crewai.py:118-119`). `--extra-index-url` lines from user requirements passed through.
6. **Configuration and secrets went into image layers.** Name, model, system prompt and every
   `deploy.env_vars` entry, including `AGENT_AUTH_TOKEN`, were written as Dockerfile `ENV`
   (`R/base.py:219-238`).
7. **The control-plane package went into every agent image** (`agentbreeder==…`, `R/base.py:70-110`),
   and the platform API key went into every container (`engine/deployers/base.py:109-116`).
8. **Invocation auth failed open.** With `AGENT_AUTH_TOKEN` unset every request was accepted
   (`T/claude_sdk_server.py:43-45`). Tokens were compared with `!=`. Local containers were published
   on all interfaces (`engine/deployers/docker_compose.py:278`).
9. **Third-party telemetry was never turned off.** OpenAI Agents tracing (exported to OpenAI by
   default), CrewAI telemetry and LangSmith were all left at their defaults.
10. **`/health` returned 200 when the agent failed to load** (`T/claude_sdk_server.py:199-200`,
    `T/crewai_server.py:235-236`). A broken container passed its health check.
11. **Five different contracts.** `input` was a string in some templates and a dict in others, the
    session id was in three different places, and there were five stream shapes. LangGraph and
    custom had no `/stream`.
12. **Blocking calls in async handlers**: LangGraph's sync `invoke` (`T/langgraph_server.py:537`),
    tool execution in the Claude loop (`T/claude_sdk_server.py:371`), and a busy-wait in CrewAI's
    stream (`T/crewai_server.py:328-336`).

**Medium.**
- **LangGraph.**
  - A precompiled graph never got the checkpointer (`T/langgraph_server.py:313-317`), so `thread_id`
    and `/resume` did nothing.
  - `/resume` answered any thread's interrupt for any caller (`:429-439`).
  - Caller `config` was merged into the run config (`:372-375`).
- **OpenAI Agents.** `FunctionTool(fn)` is not the SDK's constructor (`:198`), and the error was
  swallowed.
- **The custom runtime let user code replace the server and Dockerfile** (`R/custom.py:135-137,154`).
- **Kubernetes pods had no hardening**: no `securityContext`, read-only root filesystem,
  NetworkPolicy or service-account opt-out.
- **Tests did not exercise the frameworks.** They asserted strings in generated Dockerfiles and
  replaced the frameworks with `MagicMock` in `sys.modules` (`tests/unit/test_langgraph_server.py:51-110`).
  No test ran a real framework against a model endpoint.

### 3.3 What 3.x keeps from it

| Idea | Where in 2.x | In this design |
|---|---|---|
| One documented HTTP contract for every framework, so deployers and the console need no per-framework change | `engine/schema/runtime-contract-v1.md:69-77` | Agent protocol v1 (§10), checked by the conformance kit |
| A uniform tool-call timeline in every reply, with an adapter per framework | `T/langgraph_server.py:543-619`, `T/openai_agents_server.py:343-400` | `tools` in the reply and TOOL spans, written by the shared kit (§7.3), not reconstructed from framework messages |
| Interrupt and resume, and the handoff chain, as framework-native features users expect | `T/langgraph_server.py:405-447`, `T/openai_agents_server.py:319-339` | Out of v1 (§4); the protocol reserves the state (§10.4) |
| Typed SSE events with a terminal result and error | `T/crewai_server.py:334-354` | Reserved for protocol v1.1 streaming (§10.4) |
| Sync user code via `asyncio.to_thread` | `T/custom_server.py:210-215` | The kit's `run_to_completion`, already in `runtime/adk` |
| Structured validation errors with a path and a suggestion | `R/base.py:152-200` | Already the 3.x style (coded errors that name fields, never values) |
| **A negative lesson:** one explicit export name, type-checked at build | the name-guessing lists in §3.1 | Code mode's `factory` (§9.2) |

The Fable reviewers' security requirements are folded into §13; each requirement there names the
2.x finding it comes from.

## 4. Scope and non-goals

In scope:
- the `agent.yaml` change (§5);
- runtime kinds and the runtime manifest contract (§6);
- the shared runtime kit and its model proxy (§7);
- how each framework binds the shared registries (§8);
- onboarding existing agents (§9);
- the agent protocol and conformance (§10);
- build, release and deploy (§11);
- traces (§12);
- security, including cloud security (§13);
- decisions, acceptance, slices and cross-repo (§14–§19).

Not in scope:
- **Streaming responses.** `runtime/adk` has none. Protocol v1.1 (§10.4) adds it for every runtime
  at once.
- **Interrupt and resume** inside a turn, framework handoffs as protocol states, and long-running
  turns. Sub-agents (#1058) and `writes_requests` tools cover the governed cases today.
- **Node or TypeScript frameworks.**
- **Direct vendor routes.** Owner, 2026-11-15: models are reached only through the stack's LiteLLM
  gateway. #1023 is not offered to these runtimes.
- **Framework-native code execution**: the Claude Agent SDK's Bash and file tools, a Deep Agents
  shell or real filesystem backend, CrewAI's code interpreter. They are refused (§13.3).
- **Moving `runtime/adk` onto the kit.** A later, separate runtime version (D12).

## 5. `agent.yaml` across frameworks

### 5.1 Shape

There is no new field. The framework is the runtime ref:

```yaml
kind: agent
schema_version: 1
name: northwind-support-langgraph
runtime: runtime/langgraph@1.0.0      # or runtime/adk@1.3.0, runtime/openai-agents@1.0.0,
                                      # runtime/deepagents@1.0.0, runtime/claude-agent-sdk@1.0.0,
                                      # runtime/crewai@1.0.0
model: {alias: support-model, reasoning: low, max_model_calls: 4, budget_usd: 25, rpm: 120}
prompt: {ref: prompt/northwind-support@1.0.1}
knowledge: [{ref: knowledge/northwind-help@1.0.0, top_k: 5, extra_searches: 1}]
tools: [{ref: tool/orders@1.0.0}, …]
memory: {sessions: postgres, notes: true}
agent_events: postgres
traces: otlp
evals: {ref: eval/northwind-golden@1.0.1, threshold: 1.0, retries: 1}
```

### 5.2 What every field means in every framework

Each field means what it means on `runtime/adk@1.3.0`, as that runtime implements it today.

| Field | Meaning (as in 1.3.0) | Who implements it on a kit runtime |
|---|---|---|
| `model.alias` | A gateway alias; the agent's chat model | the model proxy's alias allow-set (§7.2.5) |
| `model.reasoning` | `reasoning_effort` on each chat call | the proxy sets it in the upstream body |
| `model.max_model_calls` | At most N chat calls per turn; the Nth has function calling off (1.3.0 sets mode `NONE`, `agent.py:739-742`) | the proxy (§7.2.4) |
| `model.budget_usd`, `rpm` | Ceilings on the deployment's gateway key | unchanged (deploy, `gateway_keys.py`) |
| `prompt` | The system prompt | the adapter puts it in the framework's system or instructions slot |
| `knowledge` | **Retrieval before the model call**: hybrid pgvector and BM25 over the previous customer message plus this one, injected as a `<knowledge_base_results>` block in the user message (`agent.py:612-638`); `sources` are the hits' titles. Plus, when `extra_searches` > 0, the **`search_knowledge_base`** tool (`build.py:181`, `agent.py:669-713`) for further searches | the kit's `turn.py` (pre-model retrieval) and `rag.py` (the tool, same name and cap) |
| `tools` | Registry functions `fn(rt, **args)`, declared by `tool.yaml` JSON Schema | the kit's loader; the adapter wraps each in the framework's tool type (§8) |
| `mcp` | The relay's tools, named `<server>_<tool>` | the kit's MCP client, as plain functions (§7.4) |
| `memory.sessions` | Conversation history in the agent's Postgres, per (customer, session) | the kit (§7.5) |
| `memory.notes` | Per-customer notes injected into the prompt | the kit (`memory.py`) |
| `agent_events`, `traces` | The append-only event log and OTLP traces | the kit (§12) |
| `evals` | The eval set `release` runs against `/invoke` | unchanged: `evals.py` reads `reply`, `tools`, `sources` (titles; 32 of Northwind's 61 cases check `sources_any` in the first 3) and `trace_id` |

**No framework-specific fields in v1 (D3).** If a later runtime version needs options, it declares
`options_schema` in its manifest, and `agent.yaml` gains `runtime_options:`, validated at build and
included in the build digest. That is a later authoring-schema change with its own review.

### 5.3 Framework facts move out of the engine

Today the engine knows ADK in these places:

| Where | What |
|---|---|
| `agentfile.py` | `knowledge` has at most one entry |
| `build.py:172` | `ADK_RESERVED_TOOLS`, the MCP names ADK reserves |
| `build.py:181` | `KNOWLEDGE_TOOL` |
| `build.py:411` | the ADK-worded `MCP_RUNTIME_UNSUPPORTED` message |
| `tool_check.py:44-59` | `RUNTIME_MODULES` and the ADK lock's import names, `LOCKED_IMPORTS`, for the shadowing check |
| `agentspec.py:330` | `knowledge` at most one entry (the builder spec's own copy) |
| `agentspec.py:69,542`, `newagent.py:54` | the ADK runtime ref used for new agents |

F1 replaces each with data read from the resolved runtime version (§6.2):
- `limits`, `reserved_tool_names` and `capabilities` come from `manifest.yaml`;
- the shadowing lists come from a generated `runtime/modules.json` (the runtime's own module names
  and its lock's import names), which a repo test keeps in step with each version's files;
- `new agent` takes `--runtime`, with ADK as the default.

**Existing ADK versions are append-only, and every `agent.lock` hashes them,** so they are not
edited. A runtime manifest **without a `protocol` key** gets one named legacy profile in the
engine, `LEGACY_ADK`. That profile:
- implies `capabilities: [knowledge, notes]` plus whatever the manifest lists;
- sets `limits.knowledge: 1`;
- uses today's `ADK_RESERVED_TOOLS` and `tool_check` constants.

It also supplies, for the console's resolved view (`registry runtimes --json`), `display_name`
"Google ADK", protocol 1 and profile `v1`.

This is the only framework-named constant left in the engine, and it applies only to versions that
predate this design. F1's golden test pins that Northwind and the `new agent` starter build
byte-identically on `adk@1.2.0` and `1.3.0`, **and** that every refusal they gave before
(`MCP_TOOL_RESERVED`, `MCP_RUNTIME_UNSUPPORTED`, the knowledge count) is still given.

### 5.4 JSON Schema and digests

- **`agent.yaml`** has no JSON Schema (`docs/agent-yaml.md`), so none changes. Schema version stays
  `1`.
- **The deploy manifest gains one optional field, `runtime: {ref, profile}`** (§13.4).
  - `build` writes it **only for kit runtimes** (runtime manifest `profile: kit`, §6.2). Existing ADK
    versions (≤ 1.3.0, the `LEGACY_ADK` profile) never carry it, so their manifests and revision
    digests are unchanged, and F1's byte-identical golden test holds.
  - It is part of `DeployTemplate` and so of the release package's deploy template
    (`build.py:269-273`), which means `promote` to `kind` keeps it.
  - It changes `docs/manifest.schema.json` once. An absent field leaves the digest of every existing
    manifest unchanged. The Cedar `Agent` entity is unchanged until D9.
- **The build record already names the runtime ref and its hash** (`build.json`, `agent.lock`, the
  release package's `package.json`).
- **A gate restriction on runtimes per environment** is D9 (later).

## 6. Runtimes as registry kinds

### 6.1 Names and versions

| Framework (Python) | Runtime ref | Pinned packages (exact versions chosen in F0) |
|---|---|---|
| Google ADK | `runtime/adk@1.3.0` (exists) | `google-adk`, `litellm` |
| OpenAI Agents SDK | `runtime/openai-agents@1.0.0` | `openai-agents`, `openai` |
| LangGraph | `runtime/langgraph@1.0.0` | `langgraph`, `langchain-core`, `langchain-openai` |
| Deep Agents | `runtime/deepagents@1.0.0` | `deepagents` plus the LangGraph set |
| Claude Agent SDK | `runtime/claude-agent-sdk@1.0.0` | `claude-agent-sdk` and the Claude Code CLI it runs (§8.4, D8) |
| CrewAI | `runtime/crewai@1.0.0` | `crewai` without `crewai-tools` |

- **The rules are the existing registry rules:** append-only, content-hashed, locked in
  `agent.lock`, and code kinds (`runs_code("runtime")`).
- **Each version is a full directory** in `builtin_registry/runtime/<name>/<X.Y.Z>/` with
  `manifest.yaml`, a fixed `Dockerfile`, `runtime/` (with `requirements.in`, a hash-locked
  `requirements.lock` and `modules.json`) and `runtime_tests/`.
- **Versions are independent:** each framework starts at `1.0.0` and moves on its own. A kit change
  ships as a new version of each runtime that takes it.
- **The Claude CLI is an exception until D8:** it is not placed under `builtin_registry` until D8
  is decided (§13.5).

### 6.2 The runtime manifest is the builder's only source of framework facts

The new keys, for runtimes that declare `protocol`:

```yaml
framework: langgraph                 # shown in the console and in the reply's `framework`
display_name: LangGraph              # console label (≤ 100 characters)
protocol: 1                          # agent protocol version it serves (§10)
profile: kit                         # the v1 kit profile (§10.2)
kit: 1.0.0                           # the runtime kit version vendored in runtime/kit/ (§7)
capabilities: [mcp, knowledge, notes]          # build refuses fields the runtime lacks
limits: {knowledge: 1, tools: 50, mcp_servers: 10}
reserved_tool_names: [search_knowledge_base]   # plus the framework's own (F0)
model_api: openai-chat               # or anthropic-messages (claude-agent-sdk only)
notes: ""                            # optional console note (≤ 300 characters)
docs: docs/runtimes/langgraph.md     # optional, relative
```

- **`build` reads only these keys** (and `modules.json`) and never branches on `framework`. That
  keeps CLAUDE.md's rule of no framework logic in the builder.
- **Messages name the missing capability and the runtime ref**, for example "runtime/langgraph@1.0.0
  has no `mcp` capability".
- **Each kit runtime's fixed Dockerfile** carries the static labels
  `org.agentbreeder.runtime=<ref>` and `org.agentbreeder.protocol=1`. Earlier ADK Dockerfiles have
  none (§11).

## 7. The runtime kit

Five adapters over one shared core. The core does everything that must not differ between
frameworks. An adapter only translates between that core and one framework (§8).

### 7.1 What it is and where it lives

- **Source of truth:** `src/agentbreeder/runtime_kit/`, a plain Python package with no AgentBreeder
  imports, never installed as the control-plane package (§13.5).
- **Each runtime version vendors it** as `runtime/kit/`, a byte copy (D2). A repo test checks that
  the newest version of every kit runtime carries the kit version its manifest names, byte for
  byte.
- **The 1.3.0 code it is taken from is not reusable as-is.** It is written against ADK and
  LiteLLM objects (`upstream_status`, `classify` and `retry_delay` read LiteLLM and OpenAI
  exception types, `agent.py:158-166,246,291`). The kit reimplements those rules over raw HTTP
  responses, with **parity tests** that feed the same status codes and headers to 1.3.0's functions
  and to the kit's and compare the result.

| Module | Taken from (1.3.0) | Does |
|---|---|---|
| `server.py` | `server.py` | Protocol v1 HTTP (§10) |
| `turn.py` | `agent.py` `chat_async`, `run_turn` | Turn id, session lock, **pre-model knowledge retrieval and notes injection**, the trace dict, `turn_failed`/`reply_sent` events, the reply |
| `model_proxy.py` | `agent.py` `CostClient`, `classify`, `retry_delay`, `after_model_callback` | The loopback model proxy (§7.2) |
| `tools.py` | `tools.py` | `tool.yaml` loading, shadowing check, `ToolRuntime`, `run_to_completion`, per-call audit and spans |
| `rag.py` | `rag.py` | Retrieval and the `search_knowledge_base` function |
| `mcp_client.py` | `mcp_client.py` | The relay client, each granted tool as an async function (§7.4) |
| `memory.py` | `memory.py` | Notes, tickets, turns, `audit_events` (memory schema 2), history reads (§7.5) |
| `telemetry.py`, `redaction.py` | the same | The root span, OTLP export, redaction |
| `adapter.py` | new | The adapter interface (§7.6) |

### 7.2 The model proxy

Every framework is pointed at **a loopback proxy in the agent's own process**, never at the
gateway directly (D1). The proxy holds the gateway key and implements, once, what 1.3.0 built into
ADK's LiteLLM client.

#### 7.2.1 Listener and HTTP hardening

- An asyncio server bound to **`127.0.0.1` only**, on a random port. It never binds to `0.0.0.0`:
  sidecars such as the egress boundary helper share the network namespace (`egress/boundary.py:6`).
- **A real HTTP/1.1 parser (h11).** It refuses `Transfer-Encoding` on requests, duplicate or
  conflicting `Content-Length`, absolute-form targets and any path not in §7.2.3.
- **Caps:**
  - at most 64 concurrent connections, sized for the session concurrency the server allows times
    the parallel tool and sub-agent calls in a turn; F0 measures a Northwind turn's peak;
  - a 4 MiB request body and an 8 MiB upstream response;
  - a 120 s idle timeout.

#### 7.2.2 Turn binding

- Each turn gets a random 128-bit **turn token**, which is `remember()`ed by redaction so it is
  scrubbed wherever the runtime redacts. 1.3.0's `redaction.remember` has no way to forget, so the
  kit's redaction adds `forget()`, called at turn end, and redacts by a set bounded to the live
  turns.
- **The token travels as the client's API key** (`Authorization: Bearer`, or `x-api-key` on the
  Anthropic path), never in the URL, which libraries log and instrumentors record.
- **The proxy maps the token to the turn's tally** and refuses an unknown or finished token with a
  fixed 401 body. Tokens are deleted when the turn ends.
- **The framework never sees the gateway key.** The proxy reads it from `LLM_GATEWAY_KEY_FILE`.
- **How a framework gets the turn's token:**
  - **In-process frameworks** (OpenAI Agents, LangGraph, Deep Agents, CrewAI) get one stable base
    URL, `http://127.0.0.1:<port>/v1`, and an `httpx` client whose auth hook reads the token from a
    **contextvar** that the kit sets for the turn. So an agent object built once at start-up (and
    code mode's prebuilt graph, §9.2) works across turns.
  - F0 must show the contextvar reaches every model call under two concurrent sessions for each
    framework. CrewAI runs work in threads, and LangGraph can run sync nodes in an executor.
  - Where it does not, that adapter builds its agent per turn, and code mode is not offered on that
    runtime (`code_mode` capability absent).
  - **The Claude CLI** gets the token as `ANTHROPIC_AUTH_TOKEN` in its per-turn environment (§8.4).

#### 7.2.3 Upstream requests are built from scratch

The proxy never forwards the inbound request. It builds a new one:
- **Path:** a fixed upstream path chosen by the inbound route. Inbound `POST /v1/chat/completions`
  goes upstream to `/v1/chat/completions`, and inbound `POST /v1/messages` goes to `/v1/messages`,
  only on runtimes whose `model_api` is `anthropic-messages`. Any other path, any query string, and
  any dot segment are 404 (for example `/v1/../key/generate`).
- **Body:** an allow-list of fields per API: `model`, `messages`, `tools`, `tool_choice`,
  `temperature`, `top_p`, `max_tokens` / `max_completion_tokens`, `stop`, `response_format`,
  `stream` for chat; and the Anthropic equivalents plus `system`, `thinking` (F0). Everything else is
  dropped. That includes `api_base`, `api_key`, `metadata`, `user`, `mock_response`, `guardrails`,
  `fallbacks`, `caching`, `extra_body` and every LiteLLM control field, so the agent cannot switch
  off the gateway's guardrails (guardrails spec, the fields at lines 438-449) or redirect a call.
  The proxy then sets `model` (§7.2.5) and `reasoning_effort`.
- **Headers:** no inbound header is forwarded. The proxy sends only `Authorization: Bearer <gateway
  key>`, `Content-Type` and, on the Anthropic path, `anthropic-version` (a pinned value).
- **Timeout:** 90 s per upstream call, as 1.3.0 (`agent.py:382`). Frameworks' own client timeouts
  are set above the 170 s turn so the proxy's always fires first.
- **Streaming:** upstream calls are always non-streaming. When the framework asks for `stream: true`
  (chat or Anthropic), the proxy waits for the whole response and sends it back as the API's SSE
  events. This keeps the cost header (D7) and one code path. The synthesis is bounded by the
  response cap.
- **Embeddings:** the kit's retriever calls the gateway's `/v1/embeddings` directly with the key,
  as 1.3.0's `Embedder` does (`rag.py:62-80`). The proxy does not serve `/embeddings`, so a
  framework cannot reach it.

#### 7.2.4 Step cap

- **What counts:** every chat call the proxy serves for the turn. That includes framework-internal
  calls the author never sees: the Claude CLI's background calls, Deep Agents summarisation and its
  `task` sub-agent, and CrewAI's final-answer and formatting calls. F0 lists, per framework, which
  hidden calls a Northwind turn makes, so `max_model_calls` can be checked against reality before
  the acceptance run.
- **The Nth call** is sent with function calling off: `tool_choice: "none"` on chat, and `tool_choice:
  {type: "none"}` on Anthropic. The tools stay in the body, because removing them while the
  history holds tool calls is refused by Anthropic-shaped backends. F0 probes both shapes through
  LiteLLM to the `support-model` backend.
- **A further call** is answered by the proxy with a fixed 429-shaped body (`MODEL_STEP_LIMIT`) and
  never sent upstream.
- **Main-loop and hidden calls.** The proxy tells a **main-loop call** (the agent's own reasoning
  step) from a hidden one by the request itself: a main-loop call carries the agent's system prompt
  (its hash is compared) and exactly the agent's tool set. Anything else is a hidden call: a
  summary, a sub-agent, a title. Both kinds count toward N.
- **Logical calls.** The proxy's own retries (§7.2.7) do not count; N counts calls the framework
  made, as 1.3.0 counts model calls, not HTTP attempts.
- **The turn's result:**
  - The framework's own final answer is the reply whenever the framework finishes normally.
  - Only when the proxy **refused an (N+1)th call** does the step limit decide the result. If the
    Nth call was a main-loop call, the turn replies with that call's text, whatever the framework
    does next. Otherwise (the Nth call was hidden, so its text is not an answer to the customer),
    the turn fails with `MODEL_STEP_LIMIT` (502, "the agent used its model calls for this turn;
    ask again or raise `model.max_model_calls`").
  - The step limit is **not** a terminal failure in the sense of §7.2.7. It is decided here, and it
    never replaces a terminal model failure that happened earlier in the turn.
- **Framework limits** (`max_turns`, `recursion_limit`, `max_iter`) are set high enough never to
  fire first, as a backstop.

#### 7.2.5 Model allow-set

- **The proxy keeps an allow-set:** the agent's alias, plus each sub-agent alias once #1058 lands.
  On the OpenAI path, a request for any other model is refused with a fixed 403 body and recorded as
  `MODEL_ACCESS_DENIED` for the turn.
- **On the Anthropic path only,** the Claude CLI picks model names itself, for its main model and a
  background "small fast" model. The proxy rewrites any model name to the agent's alias, and such
  calls count toward the cap (§7.2.4).
- The gateway key's own alias list enforces the same boundary. This makes it a local, coded answer.

#### 7.2.6 Cost, fallbacks, tokens and model events

From each upstream response the proxy records into the turn's tally, with 1.3.0's rules
(`agent.py:126-158,267-290`):
- the cost header;
- `x-litellm-attempted-fallbacks`;
- `x-litellm-model-group` (alias-shaped values only);
- the response's `usage` (prompt and output tokens).

It writes one `model_called` audit row per call (alias, `served_by`, `fallbacks`, `cost_usd`,
latency), as 1.3.0's `after_model_callback` does (`agent.py:753-783`). `model_calls`,
`prompt_tokens` and `output_tokens` in the reply come from the tally. `center/tryit.py:179-182`
reads them.

`cost_usd` keeps 1.3.0's meaning: chat calls only, never embeddings, and `null` if any chat call
had no cost header.

#### 7.2.7 Retries and coded errors

- **The rules are 1.3.0's,** reimplemented over raw responses with parity tests (§7.1):
  - at most 3 attempts;
  - retry 409/500/502/503 and connection errors after a short backoff;
  - wait on a rate limit only while the turn's total wait stays at most 10 s;
  - never retry 402, 408 or 504;
  - no retry starts in the last 40 s of the 170 s turn.
- **Framework client retries are set to 0** where the library allows it *(F0)*.
- **Once the turn has a terminal failure,** the proxy answers every later call of that turn at once
  with the same status, so a framework's own retry loop ends quickly.
- **What the framework sees:** the proxy's answer to a failed call is a fixed body, the code and
  nothing else. Gateway or provider text never reaches the framework, its logs or its exceptions.
  The redacted detail goes to the agent's log line and the turn record, as in 1.3.0.
- **What the caller sees:** whenever the tally holds a terminal model failure, the turn answers
  with 1.3.0's coded body (`{error, code, correlation_id, trace_id, retry_after_s}`, the same status
  and `Retry-After`). That holds **whether the framework raised or swallowed the error and returned
  text**.
- **Any other failure** is 1.3.0's fixed 500, `turn failed`.

#### 7.2.8 Guardrails

- **On the chat path, G1b's guardrails act at the gateway**, so they apply unchanged. §7.2.3 is what
  stops the agent from switching them off.
- **On the Anthropic path (`/v1/messages`) nothing is known yet.** The guardrails spec's G0 runs sent
  no `/v1/messages` call (guardrails spec, line ~508). Whether LiteLLM runs the configured guardrails
  there, and whether the bypass fields (guardrails spec, lines 434-451) are the same, is an F0 probe.
  Until it passes, the Claude runtime does not declare `guardrails` and is refused in a
  guardrails-required environment.
- **G2's block codes** (`MODEL_INPUT_BLOCKED`, `MODEL_OUTPUT_BLOCKED`, `GUARDRAIL_UNAVAILABLE`) are
  added to the kit's classifier once, and every kit runtime gets them.
- **A runtime declares `guardrails`** only after its conformance run covers them; otherwise
  `RUNTIME_GUARDRAILS_UNSUPPORTED` (guardrails spec §7).
- **A conflict to resolve:** in guardrails-required environments only, G1 mints keys with
  `allowed_routes` limited to chat and embeddings (guardrails spec, lines 505-514). A Claude
  runtime's `/v1/messages` call would be refused there.
  So either the key minting adds `/v1/messages` for a deployment whose runtime has `model_api:
  anthropic-messages` (the deploy manifest would need to carry that), or the proxy translates
  Anthropic requests to chat completions itself. That is D17, and F7 is blocked on it.

### 7.3 Tools

- **The tool contract is already framework-neutral:** `tool.yaml` JSON Schema, and `fn(rt, **args)`
  in a worker thread with a `ToolRuntime` whose `customer` comes from the server-side session.
- **The kit wraps every function once** (registry tools, `search_knowledge_base`, MCP tools). The
  wrapper handles:
  - argument validation against the declared schema;
  - the call;
  - latency;
  - the `tool:<name>` audit rows (one per call, `ok: false` on failure);
  - the TOOL span;
  - the reply's `tools`;
  - evidence.
- **The adapter wraps that callable in the framework's tool type**, passing the declared schema
  through (§8).
- **A framework cannot call a tool the agent did not declare.**
- **One TOOL span per call** is new work per framework. 1.3.0 relabels ADK's own spans
  (`telemetry.py`). For kit runtimes, the kit emits the TOOL span, and the instrumentor's tool spans
  are dropped by a span processor or relabelled `CHAIN` *(F0 per instrumentor)*. The conformance
  kit checks the count. The ADK baseline may differ, and its report says so.

### 7.4 MCP

- The kit's relay client exposes each granted tool as an async function `<server>_<tool>`, with
  1.3.0's `mcp_unavailable` behaviour and 60 s listing cache.
- Adapters never use a framework's own MCP client. That would be five MCP stacks with different
  timeouts, auditing and token handling.

### 7.5 Sessions and history

- **Sessions are keyed by (customer, session)**, as ADK keys its sessions by (app, user, session)
  (`agent.py:463-465`). A second customer naming the same session id gets a separate, empty
  history, never someone else's. There is no new error code.
- **History comes from the `turns` table 1.3.0 already writes** (session, customer, message, reply;
  `memory.py:28`). There is no new table and no `memory_schema` change. The adapter gets the last 20
  turns (capped at 64 KiB) as **structured messages** in the framework's message type, never as
  text pasted into the prompt (2.x finding 3).
- **A known difference from ADK.** ADK replays its whole event history, including tool calls and
  the injected `<knowledge_base_results>` passages. The kit replays user and assistant text only;
  each turn retrieves afresh over the previous customer message plus the new one, exactly as 1.3.0
  does. 7 of the 61 golden cases (`northwind-golden@1.0.1`) have more than one turn, so this is a bounded risk to 61/61. F3 measures
  it on LangGraph first. If cases fail because of it, the kit adds the previous turn's tool results
  as tool messages (D5).
- **Not used:** LangGraph's `PostgresSaver`, the OpenAI Agents SDK's sessions, CrewAI memory and
  the Claude CLI's transcripts (§8.4). They add packages, DDL and per-framework state formats, and a
  turn here is synchronous.
- **Session binding is not authentication.** `customer_id` comes from the request body
  (`server.py:164-166`), as the authenticated-invoke spec (#780) records, so keying by customer
  protects against mistakes and the model, not against a caller.
- **Locking:** the per-session lock is keyed by (customer, session).

### 7.6 The adapter interface

```python
class Adapter(Protocol):
    framework: str
    def build(self, kit: Kit) -> None: ...               # once at start-up (or per turn, §7.2.2)
    async def run_turn(self, turn: Turn) -> str: ...     # structured history + message -> reply text
    def close(self) -> None: ...
```

`Kit` gives the adapter:
- `kit.prompt`;
- `kit.functions` (§7.3);
- `kit.http_client()`, the turn-aware `httpx` client (§7.2.2), and `kit.base_url`;
- `kit.alias`;
- `kit.chat_model()` and `kit.tools()`, the framework-native model and tools for code mode (§9.2);
- `turn.history` and `turn.message`, the latter carrying the knowledge block and notes already
  injected by `turn.py`;
- `kit.run_blocking`.

Everything else in the reply comes from the kit.

## 8. Per-framework binding

Library facts marked *(F0)* are checked against the pinned wheel before code. In every row the
framework's client gets `kit.base_url` and the turn-aware `kit.http_client()`, whose auth hook
supplies the turn token as the API key (§7.2.2).

### 8.1 OpenAI Agents SDK (`runtime/openai-agents`)

- **Model.** `OpenAIChatCompletionsModel(model=alias, openai_client=AsyncOpenAI(base_url=kit.base_url,
  http_client=kit.http_client(), max_retries=0))`. It uses Chat Completions, not Responses, which
  the proxy does not serve.
- **Turn off the SDK's own trace upload to OpenAI** (`set_tracing_disabled(True)`) *(F0)*.
- **Tools.** `FunctionTool(name, description, params_json_schema=…, on_invoke_tool=…,
  strict_json_schema=False)`. Strict mode would make every parameter required.
- **Run.** `Runner.run(agent, input=<history + message>, max_turns=max_model_calls + 2)`.
- **Traces.** `openinference-instrumentation-openai-agents` *(F0)*.

### 8.2 LangGraph (`runtime/langgraph`)

- **Model.** `ChatOpenAI(model=alias, base_url=kit.base_url, http_async_client=kit.http_client(),
  max_retries=0)`.
- **Graph.** The prebuilt ReAct agent (`create_react_agent`, or `langchain.agents.create_agent` in
  LangChain 1.x, *(F0)*) with the system prompt, no checkpointer, and `recursion_limit` = 2 ×
  max_model_calls + 4.
- **Tools.** `StructuredTool.from_function(coroutine=…, args_schema=<JSON Schema dict>)` *(F0)*.
- **LangSmith** environment variables are removed before import.
- **Traces.** `openinference-instrumentation-langchain`.

### 8.3 Deep Agents (`runtime/deepagents`)

- **Agent.** `create_deep_agent(model=<the ChatOpenAI above>, tools=…, system_prompt=…)` with the
  **state backend only**. No filesystem, store or sandbox backend, so no `execute` tool *(F0)*.
- **Built-in tools.** The file tools (`ls`, `read_file`, `write_file`, `edit_file`, `glob`,
  `grep`) and `write_todos` act on in-memory graph state for the turn only. Their names are in
  `reserved_tool_names`.
- **A conformance check** asks for `execute` and a host path through `read_file`. Both must be
  refused or act on state only.
- **The built-in `task` sub-agent** shares the turn's call cap (§7.2.4), and its tool calls are
  audited as the agent's (D10).
- **Traces.** As LangGraph.

### 8.4 Claude Agent SDK (`runtime/claude-agent-sdk`)

This is the hardest runtime, because the SDK runs the Claude Code CLI, a general-purpose coding
agent, as a subprocess **with the same uid as the kit**. The skills spec already refused a
same-uid subprocess for model-directed scripts, because the secret files (gateway key, database
URL, MCP tokens) are readable by that uid (`2026-11-16-skills-design.md:276`; mounts readable by
65532, `runtime.py:497-507`, `kubernetes.py:595,657-660`).

The difference here is that the CLI is a fixed binary with its tools switched off. But a CLI bug or
a missed tool would put the model one step from those files. **D15 decides whether this runtime
ships with that residual risk or waits for a two-uid deploy contract.** F7 is blocked on F0, F1b,
D6, D8, D15, D16 and D17 (§16).

If it ships, these controls apply:
- **Scrubbed environment.** The CLI is started through a wrapper that clears the environment
  (`env -i`) and passes an allow-list:
  - `PATH`;
  - `HOME` and `CLAUDE_CONFIG_DIR`, set to a per-turn directory under `/tmp/turns/<turn>/` that is
    deleted at the end of the turn;
  - `ANTHROPIC_BASE_URL`, the proxy;
  - `ANTHROPIC_AUTH_TOKEN`, the turn token;
  - the model variables, set to the alias;
  - the telemetry-off variables.

  No `*_FILE` path, `AGENTBREEDER_*` URL or `DATABASE_URL` reaches it. `ClaudeAgentOptions(env=…)`
  merges over the parent environment *(F0)*, so it is not enough on its own.
- **Built-in tools off:**
  - an empty base tool set *(F0)*;
  - `allowed_tools` holds only the kit's `mcp__agentbreeder__<name>`;
  - `disallowed_tools` holds every built-in of the pinned version (taken from it in F0);
  - `permission_mode="default"` with a deny-by-default `can_use_tool`, never `bypassPermissions`;
  - `setting_sources=[]`, `--strict-mcp-config`;
  - no `add_dirs`, `agents`, hooks or plugins;
  - `cwd` is the per-turn empty directory.
- **Tools:** the kit's functions through one in-process SDK MCP server (`create_sdk_mcp_server`).
- **Slash commands and file mentions:** a customer message beginning with `/`, or containing
  `@/path`, must reach the model as text and must not be acted on by the CLI *(F0)*. If the pinned
  version cannot be told so, the kit escapes such input.
- **History:** prior turns as structured messages, if the SDK can take them *(F0)*. If it cannot,
  this runtime is not built: pasting history as text is the 2.x finding 3 it must not repeat.
- **Process lifetime:** the CLI runs in its own process group (`start_new_session=True`). The kit
  kills the group at turn end, at timeout and in the SIGTERM drain. A conformance check looks for an
  orphaned CLI.
- **Resources:** the engine fixes memory at 512 MB, 128 pids (Docker) and a 64 MB `noexec` `/tmp`
  (`runtime.py:514-521`; Kubernetes sets memory and an emptyDir, with no pids limit,
  `kubernetes.py:578,681`). A Node-based CLI may not fit, and a `noexec` `/tmp` blocks a binary
  placed there. F0 measures against those exact limits. D16 decides whether limits become
  runtime-declared, which would change the deploy template and digest, as the guardrails spec's
  `runtime_capabilities` does.
- **Model and traces:** `ANTHROPIC_BASE_URL` points at the proxy (§7.2.3, §7.2.5). The kit writes
  the LLM spans from the proxy when no instrumentor exists *(F0)*.
- **Conformance checks specific to this runtime:**
  - a scripted model asks, in turn, for Bash, Read, Write, Edit, Glob, Grep, WebFetch and Task,
    targeting the key file path. Every call is refused and audited;
  - the CLI's `/proc/<pid>/environ` holds no `*_FILE` name;
  - concurrent turns use separate config directories.

### 8.5 CrewAI (`runtime/crewai`)

- **Model.** `LLM(model=f"openai/{alias}", base_url=kit.base_url, …)` with retries 0. Whether
  CrewAI calls a native OpenAI client (which takes the turn-aware `http_client`) or LiteLLM
  underneath (which needs the token through a LiteLLM callback, and
  `LITELLM_LOCAL_MODEL_COST_MAP=True` so it never fetches a cost map) is *(F0)*.
- **Crew.**
  - One `Agent`: role and goal from the description, backstory = the prompt;
    `allow_code_execution=False`, `allow_delegation=False`, `memory=False`; `max_iter` =
    max_model_calls + 2.
  - One `Task` per turn: the message, with history as context.
  - A sequential `Crew`.
- **Tools.** `BaseTool` subclasses with a pydantic `args_schema` generated from the JSON Schema. The
  generator is in the kit, unit-tested, and refuses features it cannot express.
- **Packages.** No `crewai-tools`.
- **Telemetry off.** `CREWAI_DISABLE_TELEMETRY=true` and `CREWAI_DISABLE_TRACKING=true` *(F0)*.
  Never `OTEL_SDK_DISABLED`.
- **Writable directories.** CrewAI's own storage directories must sit under `/tmp` or be disabled,
  because the root filesystem is read-only *(F0)*.
- **Traces.** `openinference-instrumentation-crewai` *(F0)*.

## 9. Onboarding existing agents

### 9.1 Image import (#757, unchanged)

An existing image in any framework is registered with `agentbreeder import`, and #757 writes its v2
manifest.
- **What it gets:** the identity gate, approvals, kill switch and audit.
- **What it gets only if it speaks protocol v1** and uses the gateway variables: evals, cost, Try it
  and traces.
- **Conformance** (§10.3) reports which protocol features it has.

### 9.2 Code mode: bring your framework code (D4)

The author keeps their own graph, crew or agent object, and the runtime and kit run it.

- **In `agent.yaml`:** `entry: {ref: code/<name>@X.Y.Z, factory: build}`. While iterating it can be
  `{path: ./src, factory: build}`, which `release` refuses as `REF_UNPINNED`.
- **New registry kind `code`.** It adds `code` to `KINDS` and `CODE_KINDS` (`registry.py:42,358`),
  and so to the `REF` pattern built from `KINDS`. Every publish, import and fork path needs a rule
  for it, and `registryimport.py:174`'s code confirm must cover it, with tests.
  - A version holds `code.yaml` (module, factory, the target framework, extra requirements) and
    `src/`.
- **The CLI surface** (F8):
  - `agentbreeder new agent NAME --from-code=DIR --runtime=REF --model=PROVIDER [--entry=MODULE:FACTORY]
    --dry-run --json`.
  - Without `--entry` it is **Find**: it reads files only and returns `{refused: [{code, path?, top_level_names?}],
    candidates: ["module:factory", …], framework_packages: [name], framework_mismatch: bool}`.
  - With `--entry` it is **Review**: `{files: [{path, size, sha256}], left_out: [path], code_yaml,
    requirements_declared: [line], agent_yaml, stack_yaml, digests, plan_sha256}`.
  - The write (`--expect-digest`, no `--dry-run`) resolves the requirements, then writes.
  - Codes: `PROVIDER_KEY_RESERVED`, `REQUIREMENT_REFUSED`, `ENTRY_NOT_FOUND`, `PATH_UNREADABLE`,
    `LOCK_FAILED`, `DIGEST_MISMATCH`.
  - `agentbreeder next --json` gains a `conformance` step for a project whose agent has `entry:`,
    after `build` and before `release`. It is the console guided path's source (console spec §5.3).
- **Factory discovery is static.** The CLI parses the source with `ast` to list candidate factories
  (module-level functions taking one argument). It never imports user code on the host, at review
  or at build. A test uses a module with import-time side effects.
- **Requirements.**
  - The extra requirements are resolved into the runtime's hash-locked lock, wheels only
    (`--only-binary=:all:`). `--index-url`, `--extra-index-url`, VCS, URL and path requirements are
    refused.
  - The resolve, which sends package names to the index, runs **after** the code confirm, never in
    the review.
  - A package with no wheel is `LOCK_FAILED`.
  - Vendor key names in the project's `.env` or declared secrets are refused (`PROVIDER_KEY_RESERVED`).
- **The factory contract:** `def build(kit) -> <native object>`.
  - The return type is checked at start-up. A mismatch fails start-up (`ENTRY_TYPE_MISMATCH`).
  - **The types:**

    | Runtime | Return type |
    |---|---|
    | OpenAI Agents | an `Agent` |
    | LangGraph | a compiled graph with a `messages` key |
    | Deep Agents | a `create_deep_agent` graph |
    | CrewAI | a `Crew` with one input, `message` |

  - Claude Agent SDK has no code mode in v1.
  - Code mode is offered only on runtimes with the `code_mode` capability (§7.2.2).
- **What it is honest to say about code mode.**
  - User code runs in the agent's process with the agent's privileges. It can read the gateway key
    file, the database URL and the MCP tokens, call the gateway directly, and so bypass the kit's
    step cap, cost tally, audit and alias allow-set.
  - The return-type check does not stop it adding a Deep Agents filesystem backend, a LangGraph
    shell tool or `subprocess`.
  - **What still binds it** is the gateway key's own limits (granted aliases, budget, rpm), the
    deployment's network policy (§13.4) and the identity gate.
  - The kit's controls hold for code that uses `kit.chat_model()` and `kit.tools()` as intended.
    The confirm says this (console spec §5.2).
- **Its own slice (F8)**, after declared mode passes Northwind on that framework, and after D11 and
  the `kind` NetworkPolicy (§13.4).

### 9.3 Declared mode (the Northwind ports)

The runtime builds the agent from `agent.yaml` alone (§8), and the author writes no Python.

## 10. Agent protocol v1 (#758) and conformance (#759)

### 10.1 Protocol v1 is what `runtime/adk@1.3.0` serves today

`docs/agent-protocol.md` (new, F2a) writes down 1.3.0's actual behaviour:
- **`GET /health`:** v1 defines 200 as "serving requests". 1.3.0 answers 200 with `{status: "ok",
  model}` as soon as the server listens, even while warming up or after a failed warm-up, which is
  retried on the first request (`server.py:135-137, 220-228`). Deploy's health wait is `probe_http`,
  which polls for a 200 for 30 s (`runtime.py:566-572`).
- **`POST /invoke`:**
  - The request is `{message, session_id?, customer_id?}` as JSON. A missing `customer_id` means the
    shared guest.
  - The reply has 1.3.0's fields (`agent.py:564-578`): `reply`, `session_id`, `customer_id`,
    `model`, `framework`, `runtime`, `turn_id`, `trace_id`, `cost_usd`, `fallbacks`, `served_by`,
    `rate_limit_wait_s`, `sources` (titles), `tools`, `web`, `tickets`, `memory_saved`,
    `kb_searches`, `model_calls`, `retrieval`, `prompt_tokens`, `output_tokens`,
    `mcp_unavailable`.
  - The statuses (`server.py:143-200`) are 400 (bad JSON or ids, and an oversize body), 409
    (`SessionBusy`), 413 (a message over the length limit), 415 (not JSON), 429, 402 and 502 (the
    coded model errors), 503 (the database unavailable or misconfigured), 504 (the turn deadline),
    and the fixed 500.
- **Host check:** `localhost`, `127.0.0.1` and the alias `agent`.
- **Auth:** none. That is the documented 1.3.0 limit; the endpoint is published on loopback only.

### 10.2 The v1 kit profile

The kit serves v1 and adds, as **profile `kit`** (declared in the manifest, §6.2):
- **`/health` readiness:** 503 until the agent is built, 503 with a fixed body after a failed
  start, and 200 only when ready. This is stricter than v1, not different: a kit runtime's 200 still
  means "serving". Deploy's 30 s `probe_http` budget is the hard limit: F0 measures each framework's
  start-up against it, and a framework that needs longer needs a deploy change first.
- **A SIGTERM drain:** the turn in flight finishes or times out within Docker's 10 s stop grace,
  then MCP sessions close and any CLI process group is killed.
- **`MODEL_STEP_LIMIT`** (§7.2.4).

These are additions. A profile `kit` runtime still passes every v1 check.

### 10.3 Conformance kit (#759)

`agentbreeder conformance IMAGE [--json]` runs a black-box suite against an image on the local
Docker.
- **The environment:**
  - a scripted fake gateway (OpenAI and Anthropic shapes, scripted tool calls, cost and fallback
    headers, every error status, timeouts, and a response that echoes back any guardrail field it
    receives);
  - a fake MCP relay;
  - a throwaway Postgres;
  - the image run with **the same flags deploy uses** (`runtime.py:480-525`).
- **The network:** an internal Docker network. A DNS and TCP log records any other connection
  attempt, and any such attempt fails the run.

| Group | Checks (profile) |
|---|---|
| Serving | `/health` (v1: 200; kit: readiness states); 400, 413, 415; Host check; SIGTERM drain (kit) |
| Reply | every v1 field and type; `framework` and `runtime` match the image labels where present |
| Knowledge | pre-model retrieval: `sources` in the first 3 for a scripted question; `search_knowledge_base` respects `extra_searches` |
| Tools | the tool gets the server-side customer; one audit row and one TOOL span per call; a failing tool gives one `ok: false` row |
| Step cap | the Nth call has function calling off; the (N+1)th is never sent; the turn's reply follows §7.2.4 (kit) |
| Model errors | each gateway failure gives 1.3.0's code, status and `Retry-After`; the gateway's text is absent from the reply, logs and spans; retries are bounded; a framework that swallows the error still gives the coded error (kit) |
| Proxy (kit) | no guardrail, `api_base`, `metadata` or `mock_response` field reaches the fake gateway; no inbound header is forwarded; `/v1/../x`, query strings and unknown paths are 404; a token from a finished turn is refused; another model alias is refused |
| Cost | `cost_usd` is the sum of the scripted headers, or `null`; `fallbacks`, `served_by`, token counts and the `model_called` rows |
| Sessions | history carried across turns; concurrent turns on one session give 409; a second customer with the same session id gets an empty history; customers never see each other's notes |
| Hygiene | no gateway key, relay token, turn token or masked key fragment in logs, replies or spans; no connection outside the fakes |
| Traces | one root `agent_turn` span with the §12 attributes; child LLM and TOOL spans |
| Framework surfaces | per runtime: Deep Agents `execute` and host-path reads (§8.3); Claude built-in tools, environment, orphaned process (§8.4) |

- **Report:** JSON, one row per check, with the image digest, runtime ref and profile. It is always
  written to the **default** state directory, whatever `--stack` or `--state-dir` says, so Build and
  the runtime detail read one place (console spec §8):
  - `~/.agentbreeder/conformance/<runtime>/<version>/<digest>.json` for a declared-mode image;
  - `~/.agentbreeder/conformance/code/<name>/<version>/<digest>.json` for a code-mode image, which
    never sets the runtime's status.

  The mode comes from the build record: an agent with `entry:` is code mode.
- **The baseline:** `runtime/adk@1.3.0` is run first, against profile v1. Every check it fails is
  listed in the F2a record (D14). These are either fixed in a later adk version or recorded as ADK
  exceptions, never relaxed silently.
- **"Supported" claims:** a runtime version is called supported only when its report is all-pass
  **and** Northwind on it has passed (§15).

### 10.4 Reserved for v1.1+

- **Streaming:** `Accept: text/event-stream`, with typed events `delta`, `tool`, `result` and
  `error`.
- **Bearer auth** (#780).
- **An `interrupted` turn state with `/resume`**, bound to the authenticated caller.

## 11. Build, release and deploy

- **`build`:** unchanged in shape, apart from F1's data-driven checks (§5.3).
- **The Dockerfile pattern for kit runtimes:**
  - a digest-pinned base;
  - `pip install --require-hashes --only-binary=:all:`;
  - no `ARG`, and no `ENV` carrying configuration, prompt or secret;
  - `USER 65532:65532`, `/app` not writable by the runtime user, `HOME=/tmp`;
  - the static labels from §6.2.
- **The agent project.** `build` and `release` use `agent_path.parent` as the project
  (`build.py:373`) and its `registry/` and `agent.lock` (`registry.py:112-119`). The fixed names
  `agent.yaml` and `agent.lock` are also assumed by:
  - `release.py:86,89,237,266,338,597`;
  - the agent package, whose members are named `agent.yaml` and `agent.lock`
    (`release.py:273-293`, `registry.py:1313`);
  - `registry.py:1722` `USAGE_FILES`, so `used-by` reads only those names;
  - the console's project model (`center/onboard.py`, `center/files.py`, `center/agents.py`,
    `center/reads.py`).

  D13 chooses between two layouts:
  - **(a) One directory per port** (recommended): `src/agentbreeder/examples/northwind-<framework>/`
    holds `agent.yaml` and its `agent.lock` as source (`release` adds `registry/agent/…` and `deploy/`
    there, as for any project). It uses Northwind's registry through
    `AGENTBREEDER_REGISTRY_PATH` (`registry.roots`) and Northwind's stack through `--stack
    ../northwind`. **No engine change.** The cost is that the registry path must be set wherever
    these ports are built, and each port is a separate console project.
  - **(b) Several agent files in one project** (`agent.<framework>.yaml` and `<stem>.lock`). That
    is an engine change across every place above. Package member names stay `agent.yaml` and
    `agent.lock` whatever the source names, and the console learns more than one agent per project.
    It would be its own F1 sub-slice with its own review.
- **`release`:** unchanged under D13 (a). Protocol v1 has the fields the eval gate reads.
- **`deploy`, local Docker and `kind`:** unchanged. The manifest is image-in. Container flags and
  limits are the engine's (§8.4, D16).
- **Which framework a deployment runs.** The console needs the runtime of each deployment
  (console spec §6.1). `list --json` gains `runtime`, taken from:
  1. the manifest's `runtime` field (§5.4), for kit runtimes on either target;
  2. otherwise the image's `org.agentbreeder.runtime` label, on local Docker;
  3. otherwise the build record (`deploy/<name>.build.json`, `build.py:457-470`) or the release
     package, matched by image digest;
  4. otherwise `null`.

  Existing ADK deployments on Kubernetes use 3, because their images are not inspected locally. This is part of
  F2a.
- **`new agent` and the console** gain `--runtime` (§5.3). `agentspec.VERIFIED` becomes keyed by
  runtime (console spec §4.3).

## 12. Traces

Every kit runtime writes the span shape `phoenix_traces.py` and the console read:
- **The root span `agent_turn`** (`AGENT`), with:
  - `openinference.project.name` = the agent name;
  - `session.id`, `user.id`, `agent.turn_id`;
  - `agent.framework`, `agent.name`;
  - `agentbreeder.deployment_id`;
  - `agentbreeder.fallbacks`, `agentbreeder.served_by`.

  These are 1.3.0's (`agent.py:519-531`).
- **A `retrieve_knowledge` span** (`RETRIEVER`), as 1.3.0 writes.
- **LLM spans** with `llm.model_name` and token counts, from the instrumentor or the proxy. Each
  model call is counted once (#1009's rule: an outer span relabelled `CHAIN`).
- **TOOL spans:** one per call, from the kit (§7.3).
- **Framework-internal spans** (graph nodes, crew tasks) are kept. The console shows them as nested
  rows (console spec §6.2).
- **Export goes only to `AGENTBREEDER_SERVICE_TRACES_URL`.** Every framework's own exporter is off,
  and the conformance egress check catches one that is not.
- **Content in spans.** As with ADK today, spans carry `input.value` and `output.value` (customer
  messages, tool arguments and results) to the stack's Phoenix. Instrumentors may add more, such as
  full prompts. That is the same data class, going to the same operator-run collector.
  `phoenix_traces.py` never reads it into CLI or console output. The proxy never logs request or
  response bodies. A per-environment option to drop content from spans
  (`OPENINFERENCE_HIDE_INPUTS` and similar) is D18.

## 13. Security (including cloud security)

Each requirement names the 2.x finding (§3.2) it answers.

### 13.1 What does not change

- **The deploy path's controls act on the image and the deployment:**
  - the identity gate, approvals, kill switch and audit chain;
  - per-deployment gateway keys (aliases, budget, rpm);
  - MCP grants and relay tokens;
  - secret files.
- **The documented limits stay:**
  - `/invoke` is unauthenticated until #780;
  - tools and code-mode code run in the agent's process;
  - the eval gate is a quality gate.

### 13.2 Model access only through the gateway (2.x finding 6)

- **The kit reads only `LLM_GATEWAY_KEY_FILE`.** Two kit components use it: the proxy, and the
  kit's own retriever for embeddings (§7.2.3). Frameworks get only a turn token and a loopback URL.
- **What exists today is narrower than this needs.** Deploy refuses the G1a reserved provider-key
  names only when the environment requires guardrails (`gate.py:327-335`), and only by name
  (`gate.py:331`). `build` has no such check.
- **This design adds:**
  - a `build` refusal of reserved provider-key names in a code-mode project's `.env` and declared
    secrets (`PROVIDER_KEY_RESERVED`);
  - a deploy refusal of those names for kit runtimes in every environment, not only
    guardrails-required ones.

  Both are name checks. A key under another name is only stopped by egress (§13.4).

### 13.3 Framework-executed code is off, and stays off

| Surface | Library default | In the runtime | Checked by |
|---|---|---|---|
| Claude Agent SDK built-ins (Bash, Read, Write, Edit, Glob, Grep, WebFetch, WebSearch, Task, …) | on | off (§8.4) | conformance: each one asked for, refused, audited |
| Claude CLI settings, hooks, plugins, `CLAUDE.md`, slash commands, `@` mentions | on | off (§8.4) | conformance |
| Deep Agents filesystem, store and sandbox backends; `execute` | backend-dependent | state backend only | conformance (§8.3) |
| CrewAI code execution, `crewai-tools` | off but installable | off; not installed | lock check |
| LangChain `allow_dangerous_*` tools | off | not installed | lock check |

Turning any of these on is a new design with its own review. It would need the skills spec's
sandbox (#1059 K6).

### 13.4 Egress (2.x finding 9)

**Today egress is open:**
- a local deployment without a stack network uses the default bridge (`runtime.py:485-488`);
- the Kubernetes NetworkPolicy allows `0.0.0.0/0` except `blocked_cidrs` (`kubernetes.py:633`).

The conformance kit's egress check covers only its own test network.

**How deploy knows a deployment is a kit runtime.** The gate sees only the manifest, and the image
label is self-declared and not inspected for Kubernetes. So `build` writes a new optional manifest
field, `runtime: {ref, profile}`, which is in the revision digest (§5.4, §18). A hand-written
manifest without it is treated as before. That makes the field an operational signal written by
`build`, not a security boundary: an operator can omit it, as they can deploy any image today. D9's
later gate allow-list would use the same field.

**Required before code mode (F8) and the Claude runtime (F7), and recommended before F3** (D11), for
manifests with `runtime.profile: kit`:
- **Local Docker:** kit runtimes run on the stack's internal network with routes only to the
  gateway, the relay, the trace collector, the database and the tools proxy. A gateway reached
  through `host.docker.internal` (`runtime.py:491-495`) has no route from an internal network; that
  needs a forwarding sidecar in the `egress/boundary.py` pattern. This is engine work, slice F1b.
- **`kind`:** a default-deny NetworkPolicy with the same destinations.
- **Acceptance (§15)** asserts egress on the real deploy network, not only in conformance.

Until then, every framework's telemetry switch (§8) is the only control, and it is tested only in
conformance.

### 13.5 Supply chain (2.x findings 5 and 7)

- **Locks:** each runtime version has a `uv`-generated, hash-locked lock for `linux/amd64` and
  `linux/arm64`. Wheels only, with the two-week rule and recorded exceptions.
- **Package names are checked** (`deepagents`, not 2.x's npm `deepagent`).
- **Never in the image:** the control-plane package, `crewai-tools`, test tools.
- **`pip-audit` per lock** in CI; a High finding blocks a version.
- **The Claude Code CLI** is proprietary, and `pip-audit` does not cover a bundled binary or an npm
  tree.
  - F0 records its licence terms and how it is shipped (bundled in the wheel, or npm).
  - If npm: a lockfile with integrity hashes, installed with `--ignore-scripts`.
  - The binary's hash is pinned.
  - It is not placed under `builtin_registry` before D8.
- **Code mode never merges user requirements by line.** It resolves them into the hash-locked lock
  after the confirm (§9.2).

### 13.6 Container and cluster hardening (2.x medium)

**Already true for every deployed image:**
- Local Docker: non-root, `--read-only`, `--cap-drop ALL`, `no-new-privileges`, memory 512 MB, 1
  CPU, 128 pids, and a 64 MB `noexec` `/tmp` tmpfs (`runtime.py:507-521`).
- Kubernetes: the equivalent security context, memory and an emptyDir `/tmp`
  (`kubernetes.py:655-682`), but **no pids limit**.

**For kit runtimes:**
- They must run within these limits. The conformance kit runs them with the same flags, and F0
  measures each framework against them.
- Raising them for one runtime is D16.
- A Kubernetes pids limit is a separate engine change, for every image.

### 13.7 Sessions and stored text (2.x finding 3)

- Sessions are keyed by (customer, session) (§7.5).
- History is replayed as structured messages. Note text keeps 1.3.0's neutralisation.
- Each deployment has its own database credentials.

### 13.8 Logs

The kit's redaction covers every framework logger, with no tracebacks, as 1.3.0's
`quiet_library_logs` does. F0 names each framework's loggers. Turn tokens are remembered values
(§7.2.2). The proxy never logs bodies.

### 13.9 Threats specific to this design

| # | Threat | Control | Residual |
|---|---|---|---|
| T1 | A framework calls a model the agent did not declare | proxy allow-set (§7.2.5); the key's aliases | none beyond the gateway mapping |
| T2 | A framework retries a billed call many times | proxy owns retries; fails the rest of the turn fast (§7.2.7) | bounded by the step cap |
| T3 | The model asks the Claude CLI for a built-in tool | §8.4 controls, tested | a CLI bug past every control reaches same-uid secret files (D15) |
| T4 | Prompt injection through tool or MCP output | as `runtime/adk` | as today |
| T5 | A turn token reused in another turn or after it | random, per turn, deleted at turn end; sent as an API key and redacted | in-process code can read it while the turn runs, and it already runs as the agent |
| T6 | The CLI outlives the turn | process group killed at turn end, timeout and drain | none |
| T7 | The agent switches off gateway guardrails or redirects a call through body fields or headers | requests built from scratch (§7.2.3) | none known; conformance checks each field |
| T8 | Path tricks against the proxy (`/v1/../key/generate`, absolute-form targets, request smuggling) | h11 parser, fixed routes, loopback only (§7.2.1, §7.2.3) | none known |
| T9 | Code-mode code bypasses the kit | key limits, egress (§13.4), identity gate | stated in §9.2 and in the console confirm |
| T10 | Customer messages abuse CLI syntax (`/command`, `@/path`) | §8.4 | F0 per CLI version |

## 14. Northwind on each framework

- **Files (D13 (a), §11):** `src/agentbreeder/examples/northwind-<framework>/agent.yaml`, the ADK
  file with `name: northwind-support-<framework>` and `runtime:` changed. Each port shares
  Northwind's registry (through `AGENTBREEDER_REGISTRY_PATH`), its stack (`--stack ../northwind`,
  whose `stack.yaml` gains each agent's identity) and its eval set.
- **Model:** each uses `support-model`, so a difference in results is the framework's. The Claude
  runtime is the exception if D6 picks another alias.
- **Running:** `agentbreeder release northwind-langgraph/agent.yaml --stack ../northwind`, from `examples/`, runs one port. Six agents can
  run side by side in one stack.

## 15. Acceptance

**Per framework**, recorded in a verification record under `docs/superpowers/verification/`:
1. **Conformance:** `agentbreeder conformance` on the built image, all checks for profile `kit`
   passing, with the report attached.
2. **Evals:** Northwind on that runtime passes **61/61** through `release` on local Docker, against
   the real gateway and model.
3. **Tools and traces:** one `invoke` per Northwind tool. Each tool's audit row and TOOL span
   appear, Phoenix shows the §12 shape, and the console's Traces view opens the turn with no
   console change.
4. **Cost:** `cost_usd` summed over the run equals LiteLLM's spend log for the key's **chat** calls,
   to the cent (embeddings excluded, as for 1.2.0, `docs/cost.md`), or the documented `null` case.
5. **Coded errors:** at least `MODEL_ACCESS_DENIED` (an alias outside the key) and
   `MODEL_BUDGET_EXCEEDED` (a tiny budget), against the real gateway.
6. **`kind`:** `promote` the release to `kind` and one `invoke` there.
7. **Egress and framework surfaces:**
   - egress on the deploy network is limited to the §13.4 destinations (once D11 is implemented);
   - the framework-surface checks pass;
   - for the Claude runtime, the built-in-tool refusals pass against the real CLI.

**Once:**
8. Northwind and the `new agent` starter on `runtime/adk` still build byte-identically, with the
   same refusals, after F1.
9. One code-mode agent (F8) passes conformance and a small eval set.

Website and README say a framework is supported only after 1–7 for it.

## 16. Slices

| Slice | Contents | Lane | Blocked on |
|---|---|---|---|
| F0 | Probes, no product code: per framework, exact versions and hash locks on py3.12 amd64 and arm64; every *(F0)* fact; contextvar propagation under two concurrent sessions; hidden model calls per Northwind turn; start-up time, memory, pids and `/tmp` use against the engine's limits; LiteLLM 1.108.3 `/v1/messages` with `support-model` (tool use, `tool_choice` none, cost header, guardrail enforcement and the bypass fields, D17); start-up time against deploy's 30 s health wait; Kubernetes' memory-backed `/tmp` against the 512 Mi limit; instrumentor availability and tool spans; the Claude CLI's environment, tool list, slash commands, licence and network use. One record per framework | V | — |
| F1 | Engine data-driven (§5.3): manifest keys, `LEGACY_ADK`, `modules.json`, `tool_check`, `new agent --runtime`, `registry runtimes --json` (console spec §8); the manifest `runtime` field; ADK golden tests incl. refusals | A | — |
| F1b | Egress for `runtime.profile: kit` deployments (D11): internal network and a gateway-forwarding sidecar on local Docker; default-deny NetworkPolicy on `kind`; tested on the real deploy network | A | F1 |
| F2a | `docs/agent-protocol.md` (#758), `agentbreeder conformance` (#759), the ADK baseline record, `list --json` `runtime` (§11) | A | F1 |
| F2b | `runtime_kit` with parity tests, the model proxy | A | F2a |
| F3 | `runtime/langgraph@1.0.0` + Northwind + acceptance; the #760 acceptance | A, then V | F0, F2b |
| F4 | `runtime/openai-agents@1.0.0` + Northwind + acceptance | A, then V | F3 |
| F5 | `runtime/deepagents@1.0.0` + Northwind + acceptance | A, then V | F3 |
| F6 | `runtime/crewai@1.0.0` + Northwind + acceptance | A, then V | F3 |
| F7 | `runtime/claude-agent-sdk@1.0.0` + Northwind + acceptance, with its own `/security` review of the built image | A, then V | F0, F1b, D6, D8, D15, D16, D17 |
| F8 | Code mode (§9.2) | A | F3, F1b |
| F9 | Docs, `what-ships` and website per framework, after its acceptance | A, website | each framework's acceptance |
| S* | Console (#1085) | B | F1, F2a, F3, F8 (console spec §11) |

- **F2b ships with its first consumer.** It merges with F3, so the kit is never on `main` unused.
- **Shared files.** F3–F7 each add their own runtime directory and agent file, but all of them edit
  `stack.yaml`, `agentspec.VERIFIED` and the docs. Whichever merges second rebases on the first.
  They run one at a time on `main`, though they can be developed in parallel worktrees.

## 17. Owner decisions (open)

| # | Decision | Recommendation |
|---|---|---|
| D1 | How frameworks reach the gateway: a loopback proxy in the kit, or per-framework client hooks | **The proxy** (§7.2): one implementation of cost, retries, coded errors, the step cap, the alias allow-set and guardrail protection, and the only option for the Claude CLI subprocess |
| D2 | How runtimes share code | **A vendored kit copy per runtime version**, with a byte-equality test |
| D3 | Framework-specific `agent.yaml` fields in v1 | **None** |
| D4 | Code mode in this epic | **Yes, as F8**, after declared mode passes and after egress is closed |
| D5 | History replay | **User and assistant text from `turns`**, adding the previous turn's tool results only if F3's evals need them (§7.5) |
| D6 | The Claude runtime's model | **`support-model` if F0 shows LiteLLM's `/v1/messages` works with it**; otherwise an Anthropic-backed alias, recorded as not like for like |
| D7 | Streaming requests from frameworks | **Upstream non-streaming, with SSE synthesised by the proxy** (§7.2.3), keeping cost |
| D8 | Shipping the Claude Code CLI inside an image AgentBreeder builds | **Decide after F0 records its licence**; otherwise the user installs it into their own build |
| D9 | Per-environment runtime allow-list at the gate | **Later** (needs a provenance attribute in the manifest and digest) |
| D10 | Deep Agents' `task` sub-agent | **Keep**, bounded by the turn's cap and audited |
| D11 | Default-deny egress for `runtime.profile: kit` deployments, keyed by the new manifest field (local internal network with a gateway-forwarding sidecar, `kind` NetworkPolicy) | **Yes, as F1b**: required before F7 and F8, recommended before F3 |
| D12 | Framework order; ADK onto the kit | **LangGraph, then OpenAI Agents, Deep Agents, CrewAI, Claude**; ADK onto the kit later |
| D13 | Where the Northwind ports live | **(a) One directory per port** sharing Northwind's registry and stack, no engine change; (b) several agent files per project is an engine change listed in §11 |
| D14 | ADK baseline gaps against protocol v1 and the kit profile | **List every one in the F2a record**: `/health` always 200, no SIGTERM drain, no step-limit code, and anything conformance finds. Fix in a later adk version, never by relaxing the check |
| D15 | Claude CLI as a same-uid subprocess that could reach the secret files | **Ship only if F0 shows every §8.4 control holds against the pinned CLI, with the residual stated in the docs**; otherwise wait for a two-uid deploy contract (a separate design) |
| D16 | Runtime-declared resource limits (memory, pids, `/tmp` size and exec) | **Only if F0 shows a framework cannot fit the engine's limits**; then a deploy-template change with its own review |
| D17 | `/v1/messages` and G1's `allowed_routes` | **Conditional on the F0 probe** that LiteLLM enforces guardrails on `/v1/messages` with the same bypass fields. If it passes, add `/v1/messages` to keys of deployments with `model_api: anthropic-messages` (`model_access.api`). If not, the proxy translates Anthropic requests to chat completions, so guardrails act on the chat path |
| D18 | Dropping message content from spans per environment | **Later**, as one operator option for every runtime, ADK included |

## 18. Cross-repo

- **Schema:**
  - `agent.yaml` is unchanged in v1 (the runtime ref's values grow).
  - `docs/manifest.schema.json`, `DeployTemplate`, the release package's deploy template and the
    revision digest gain the optional `runtime` field (§5.4, §13.4), and `model_access.api` if D17
    is taken; both in one schema change. Cedar is unchanged
    until D9.
  - Code mode (F8) adds `entry:` and the `code` kind, with its own `docs/agent-yaml.md` and
    `docs/registry.md` changes.
- **Cloud:** a `CLAUDE.md` note only.
- **Website:** no change until a framework passes §15.

## 19. Related work this changes

- **#756, #757–#760:** F2a delivers #758 and #759. F3's acceptance is the #760 acceptance; #760
  closes only after that run.
- **#1058 sub-agents:**
  - its `SubAgentTool` is ADK-specific;
  - on kit runtimes a sub-agent is a kit function running a second adapter turn under the same turn
    token, so the cap and cost carry over, and its alias joins the proxy's allow-set;
  - Deep Agents maps it to its own `subagents=`.
- **#1059 skills:** kind (a) is prompt material and portable. A kit runtime declares `skills` once
  conformance covers them. Kind (b) needs #1059 K6's sandbox, whatever the framework.
- **#1024 guardrails:** §7.2.3, §7.2.8, D17.
- **#780:** protocol v1.1.

## 20. Review log

| Round | Reviewer | Result |
|---|---|---|
| prior art | Three `claude-fable-5-2` read-only subagents over `v2.7.3` (runtime and build contract; server templates and tracing; security incl. cloud) | §3; security requirements folded into §13 |
| r1 | `/architect` (Fable, read-only) | CHANGES REQUESTED, 4 blockers. Folded in: knowledge is pre-model retrieval plus `search_knowledge_base`, `sources` are titles (§5.2, §7.1, §10.3); append-only kept with a `LEGACY_ADK` profile, `ADK_RESERVED_TOOLS` named, and refusals in the golden test (§5.3); protocol v1 written as 1.3.0 behaves, with a separate kit profile and every ADK gap in D14 (§10.1, §10.2); a stable URL with contextvar turn binding instead of per-turn URLs (§7.2.2). Majors: `tool_choice: none` instead of stripping tools, hidden calls counted, the over-cap result defined (§7.2.4); raw-HTTP reimplementation with parity tests, the 90 s timeout, swallowed errors still coded, fixed bodies to the framework (§7.1, §7.2.7); the alias allow-set (§7.2.5); embeddings excluded from cost and acceptance scoped, `model_called` rows and token counts (§7.2.6, §15); sessions keyed by (customer, session) from `turns` (§7.5); every ADK coupling in the engine listed (§5.3); per-file lock names (§11, D13); `list` runtime from the label or the build record (§11); engine resource limits (§8.4, D16); F2 split, F7 gated (§16); the `code` kind's registry changes (§9.2) |
| r1 | `/security` incl. cloud (Fable, read-only) | CHANGES REQUESTED, 5 high. Folded in: the Claude CLI as a same-uid subprocess, environment scrub, per-turn config directory, strict MCP config, slash-command and `@` checks, `/proc` environ check, D15 (§8.4); egress open today, D11 a prerequisite, reserved-name checks corrected (§13.2, §13.4); code mode's guarantees stated honestly (§9.2); static factory discovery, lock after the confirm, wheels only, index URLs refused (§9.2); requests built from scratch, guardrail fields stripped, G1 `allowed_routes` conflict as D17 (§7.2.3, §7.2.8). Mediums: the token as an API key and redacted (§7.2.2); h11, loopback bind, connection caps (§7.2.1); structured history or no Claude runtime (§8.4); session binding is not authentication (§7.5); the CLI supply chain and CrewAI's LiteLLM cost map (§13.5, §8.5); resource limits and no Kubernetes pids limit (§13.6). Lows: process group kill (§8.4), content in spans (§12, D18) |
| r2 | `/architect` + `/security` (Fable, read-only) | CHANGES REQUESTED, 4 major; every r1 item confirmed in the body and its citations checked. Folded in: the step-limit result applies only after a refused (N+1)th call and only to a main-loop Nth call, retries do not count, and it is not a §7.2.7 terminal failure (§7.2.4); deploy knows a kit runtime by a new optional manifest `runtime` field written by `build` (in the digest), egress is its own engine slice F1b with the `host.docker.internal` sidecar, and F7/F8 block on it (§5.4, §13.4, §16, §18, D11); guardrails on `/v1/messages` are unknown until an F0 probe, and D17 is conditional with translation as the fallback (§7.2.8, D17); every fixed `agent.yaml`/`agent.lock` use listed and D13 changed to one directory per port, no engine change (§11, §14, D13). Minors: 7 of 61 cases are multi-turn (§7.5); v1 `/health` defined, the 30 s `probe_http` budget, 503/413/400 meanings (§10.1, §10.2); `agentspec.py` lines (§5.3); the retriever also uses the key (§13.2); token `forget()` (§7.2.2); the connection cap sized (§7.2.1); provider-key names only in `.env` and secrets (§9.2, §13.2); Kubernetes `/tmp` measured in F0 (§16). Also added, from the console r2 review: code mode's CLI surface and the `next` conformance step (§9.2) |
| r3 | All four lenses over both specs (Fable, read-only) | CHANGES REQUESTED, 2 major (text fixes), every r2 item confirmed in the bodies. Folded in: the conformance report location and code-mode keying copied from the console spec (§10.3); the manifest `runtime` field written only for `protocol` runtimes, carried in `DeployTemplate` and the package so `promote` keeps it, and the first `list --json` source (§5.4, §11, §18). Minors: `LEGACY_ADK`'s display fields (§5.3); `top_level_names` in Find (§9.2); `--stack` path (§14); D13 (a) directory contents (§11); console slice dependencies (§16) |
| r4 | Narrow check of the r3 fixes (Fable, read-only) | **APPROVED** (no blocker or major). Minor folded in: the manifest field's writer rule names kit runtimes and scopes the ADK statement to existing versions (§5.4) |
| r4 delta | Fable, read-only, over the text folded in after r4 (head `097b292a`) | CHANGES REQUESTED (minor), folded in: kit runtimes are defined by `profile: kit`, not by declaring `protocol` (§5.4); re-wrapped |
