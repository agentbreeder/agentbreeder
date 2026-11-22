# Runtimes, frameworks and conformance

**Status:** everything on this page except `runtime/adk` 1.3.0 and 1.4.0 is on `main`, not in a
published preview. Every run named here was on macOS 13 x86_64 with Docker Desktop (and, where
it says so, a local `kind` cluster). No runtime was run on arm64, on Linux or on a production
Kubernetes cluster.

A **runtime** is the agent server `agentbreeder build` puts in the image: a registry version
`runtime/<name>@X.Y.Z` with a fixed `Dockerfile`, a hash-locked `requirements.lock` and a
`manifest.yaml`. The author writes no server code; `agent.yaml` pins the runtime with
`runtime:`. The engine reads every framework fact (protocol, profile, capabilities, limits,
reserved tool names) from that manifest, never from the framework's name (#1083 F1). The keys
are in [Runtime versions](registry.md#runtime-versions-on-main-not-in-a-published-preview);
`agentbreeder registry runtimes [--json]` lists what each installed version supports.

## The runtimes

"Run end to end" means Northwind (`src/agentbreeder/examples/northwind` or its port) passed its
61-case eval gate through `release` against a real model and gateway. "Verified" is narrower: a
`new agent` combination that ran from a wheel in a clean sandbox the way a new user would
(`agentspec.VERIFIED`). A runtime that is neither is built and unit-tested only.

| Runtime | Framework | What ran | Record |
|---|---|---|---|
| `runtime/adk@1.3.0`, `@1.4.0` | Google ADK | In the published previews: Northwind 61/61 (1.3.0); the starter and the 3.0.0a3 public-registry runs (1.4.0). The starter is verified on 1.4.0 | [install docs](https://www.agentbreeder.io/docs/install) |
| `runtime/adk@1.5.0` | Google ADK | Sub-agents and skills (instruction packs), Northwind 61/61, and a fresh-wheel run through the CLI and the console, Gemini only | [1058-subagents](superpowers/verification/1058-subagents.md), [1059-skills-a](superpowers/verification/1059-skills-a.md), [verify-langgraph-adk150](superpowers/verification/verify-langgraph-adk150.md) |
| `runtime/adk@1.4.1`, `@1.5.1` | Google ADK | A dependency update (#1190). Docker rerun: conformance identical to 1.4.0 and 1.5.0, Northwind 61/61 on 1.5.1. Not on `kind`. Not yet in the run-end-to-end list (#1198) | [1190-adk-otel-patch-docker](superpowers/verification/1190-adk-otel-patch-docker.md) |
| `runtime/langgraph@1.0.0` | LangGraph | Northwind 61/61 locally and on `kind`; the starter and the starter plus one tool from a fresh wheel, CLI and console, Gemini. **Verified for exactly those** | [F3-langgraph](superpowers/verification/F3-langgraph.md), [verify-langgraph-adk150](superpowers/verification/verify-langgraph-adk150.md), [runtimes/langgraph.md](runtimes/langgraph.md) |
| `runtime/openai-agents@1.0.0` | OpenAI Agents SDK | Northwind 61/61 locally and on `kind`. **Not verified** | [F4-openai-agents](superpowers/verification/F4-openai-agents.md), [runtimes/openai-agents.md](runtimes/openai-agents.md) |
| `runtime/deepagents@1.0.0` | Deep Agents | Northwind 61/61 locally and on `kind`. **Not verified** | [F5-deepagents](superpowers/verification/F5-deepagents.md), [runtimes/deepagents.md](runtimes/deepagents.md) |
| `runtime/crewai@1.0.0` | CrewAI | Northwind 61/61 locally and on `kind`. **Not verified**. Its lock carries a known `chromadb` advisory exception (the HTTP server is never started) | [F6-crewai](superpowers/verification/F6-crewai.md), [runtimes/crewai.md](runtimes/crewai.md) |
| `runtime/claude-agent-sdk@1.0.0` | Claude Agent SDK | Northwind 61/61 locally and on `kind`, on a copy of its stack pinned to `service/litellm@1.4.0`. **Not shipped and not supported**: it lives in `examples/northwind-claude-agent-sdk/registry/`, because AgentBreeder does not ship the Claude Code CLI | [F7-claude-agent-sdk](superpowers/verification/F7-claude-agent-sdk.md), [runtimes/claude-agent-sdk.md](runtimes/claude-agent-sdk.md) |

`new agent --runtime REF` picks the starter's runtime (default `runtime/adk@1.4.0`); `new agent`
reports `verified: true` only for a combination in `agentspec.VERIFIED`. Only `adk` and
`langgraph` have verified combinations.

## The runtime kit

`src/agentbreeder/runtime_kit/` (kit 1.0.0, #1083 F2b) is the shared server for every runtime
except ADK: protocol v1 with the `kit` profile, the turn (history, notes, knowledge, tool audit
rows), and a loopback model proxy between the framework and the gateway. The proxy keeps the
gateway key away from the framework, retries and codes model errors as `runtime/adk@1.3.0` does,
and enforces the step cap: the last allowed answer is withheld if it calls a tool
(`MODEL_STEP_LIMIT`). Each kit runtime vendors the kit byte for byte as `runtime/kit/`; a test
checks the copies.

A kit runtime's deploy manifest carries `runtime: {ref, profile: kit}` (see
[manifest.md](manifest.md)), and the deploy gives it **default-deny egress** (#1083 F1b): on
local Docker, internal networks only and a forwarding sidecar for ingress and the gateway; on
`kind`, a NetworkPolicy that allows cluster DNS and `kubernetes.agents_cidrs`. What it cannot
reach is refused at deploy (`KIT_EGRESS_UNSUPPORTED`, `KUBERNETES_KIT_EGRESS_UNCONFIGURED`). The
opt-in `kind` test of this egress passed once
([kind-optin-2026-11-19](superpowers/verification/kind-optin-2026-11-19.md)), and the LangGraph
acceptance probed it on local Docker: default deny held, only the gateway and Postgres were
reachable ([F3-langgraph](superpowers/verification/F3-langgraph.md)).

**Two-uid deploys** (#1089, `isolation: two-uid`, `runtime/claude-agent-sdk` only): the deploy
starts a second container from the same image as uid 65533, with no secret mount, for the
framework's CLI subprocess (`--network none` on Docker, a second pod container on `kind`). The
gateway key of such a deployment also gets `/v1/messages` (`model_api: anthropic-messages`),
which needs `service/litellm@1.4.0` or later; the gate refuses it wherever guardrails apply
(`RUNTIME_GUARDRAILS_UNSUPPORTED`).

## Agent protocol v1 and conformance

[agent-protocol.md](agent-protocol.md) is the HTTP contract every runtime serves (`/health`,
`/invoke`, statuses, model error codes, state, tools, traces, the `v1` and `kit` profiles).

[`agentbreeder conformance IMAGE`](commands/conformance.md) starts a built image on local Docker
with stub dependencies and checks it against protocol v1. 26 of its 39 checks are built; the
other 13 report `not_run`, so the best result any runtime can get today is `incomplete`, and no
runtime is called conformant. Reports go to `~/.agentbreeder/conformance/<name>/<version>/`;
`registry runtimes --json` summarises the newest and `registry conformance REF` prints it.

| Runtime | Conformance run | Result |
|---|---|---|
| `runtime/adk@1.3.0`, `@1.5.0` | 2026-11-19, from a wheel of `main` ([F2a-adk-baseline](superpowers/verification/F2a-adk-baseline.md)) | `fail`: 22 pass, 4 fail, 8 not run, 5 not applicable. Three failures are known v1 gaps; the fourth, `traces.llm_span`, disagrees with what Phoenix showed |
| `runtime/adk@1.4.1`, `@1.5.1` | 2026-11-20 ([1190-adk-otel-patch-docker](superpowers/verification/1190-adk-otel-patch-docker.md)) | identical, check for check, to 1.4.0 and 1.5.0 |
| `runtime/langgraph@1.0.0`, `openai-agents@1.0.0`, `deepagents@1.0.0`, `crewai@1.0.0` | 2026-11-19 and 2026-11-20 (each runtime's record above) | `incomplete`: 26 pass, 0 fail, 13 not run |
| `runtime/claude-agent-sdk@1.0.0` | cannot run yet: `conformance` starts no CLI container, so the agent answers 503 (#1199) | not run |

## See also

- [agent-yaml.md](agent-yaml.md): `runtime:`, `subagents:`, `skills:` and `limits:`.
- [registry.md](registry.md#runtime-versions-on-main-not-in-a-published-preview): the manifest
  keys, `modules.json` and legacy versions.
- [local-deployment.md](local-deployment.md) and [command-center.md](command-center.md): how a
  kit or two-uid deployment runs on Docker and `kind`.
