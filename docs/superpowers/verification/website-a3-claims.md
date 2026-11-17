# Website claims for 3.0.0a3 (#1114, epic #1112)

Every user-facing feature merged between the 3.0.0a2 website update (`7b918cba`, 2026-11-11) and
`origin/main` `ae775548` (2026-11-16), with its evidence and the claim the website may make. Every
claim on a page under `website/content/docs/` maps to a row here (column **Row**).

**Claim levels**

- **V**: verified, macOS + Docker Desktop. A real run is recorded, from a wheel built from `main`
  (or a branch, where noted) in a sandbox. None ran on Linux. None ran from the published
  `3.0.0a3`; column **a3 public-registry run** says what the #836 run covered.
- **M**: on main, not run end to end. Unit, fake or opt-in container tests only, or a run that
  covered part of it. The page says what did not run.
- **X**: do not mention.

## The 3.0.0a3 public-registry run (#836, 2026-11-16)

macOS 13 + Docker Desktop, fresh `HOME`s, from PyPI and npm:

- `quickstart` 5/5 through pip, uvx and npx.
- Northwind from pip through the CLI: `services up`, `release` 61/61 on `runtime/adk@1.3.0`,
  `invoke`, `remove`, `services down --purge`. Not run through uvx or npx.
- `npx … up --no-browser`: the one-time sign-in only, scripted through the API, no browser. Nothing
  was driven through `up`, and `kind` was not run.
- Hashed (`pip --require-hashes`, `uv pip`) and `uv tool` installs: `--version` and `doctor`.
- Linux (Ubuntu 24.04): install and `--version` through all three launchers; `doctor` passed every
  check through pip only (uvx and npx reported Docker unreachable as the unprivileged user).

Not run on `3.0.0a3`: the `runtime/adk@1.4.0` `new agent` starter, Ollama, guardrails, sub-agents,
the console beyond sign-in, `kind`/Kubernetes, any browser click-through. Pages therefore say a
feature ran "from `main`" (or a checkout, or `3.0.0a2`) unless this list covers it. Follow-up to
#1120: `index.mdx`, `command-center.mdx` and `your-own-agent.mdx` now attribute their runs this way.

Records are in `docs/superpowers/verification/`. "PR body" means a live run written in that PR's
description.

## Getting started and the CLI

| Row | Feature | PRs | Evidence | Claim | a3 public-registry run |
|---|---|---|---|---|---|
| C1 | `new agent NAME --model gemini` starter | #861 | M4 (CLI half, 2026-11-13) | V | not run |
| C2 | `next` (where a project is, next command; `--json`) | #856, #859, #869, #1044 | M4: followed only the `Next:` lines from empty `HOME` to a reply | V | not run |
| C3 | `invoke NAME MESSAGE\|-` | #862 | M4, M7, M10, M14, M15 | V | ran: `invoke` in the Northwind run from pip |
| C4 | `logs ID [--follow]` local; `logs ID` on kind | #863, #866 | M5 (agent logs were 0 lines: the runtime writes none) | V | not run |
| C5 | `logs --level`, `--since`, `--raw`, `--previous`; `services usage` | #870, #875 | M5 lists them as not exercised; #875 PR body is a real Docker check of `usage` | M | not run |
| C6 | `logs --failed NAME` (saved candidate tail) | #851 | M3, M10 | V | not run |
| C7 | `watch --stack DIR [--json] [--record]` | #872 | M5, M11 | V | not run |
| C8 | `events` (stored events, `--unread`, `--mark-read`) | #888, #949 | M11 | V | not run |
| C9 | `migrate` (state schema v4) | #851, #939 | M3 | V | not run |
| C10 | `list --all` | #911 | M7 | V | not run |
| C11 | `--stack DIR` on enable, disable, approve, reject, approve-call, reject-call | #983 (#860) | unit tests only | M | not run |
| C12 | coded `[CODE]` errors and `--json` error object for `--stack` and `services` | #952, #970 | M3, M12 (codes seen); `PORT_BUSY` not exercised (M6) | V (codes), M (`PORT_BUSY`) | not run |
| C13 | invisible-character escaping in printed text | #953, #956, #959 | unit tests | X (internal hardening) | not run |
| C14 | `new agent --from-spec` combinations: gemini + prompt/evals, + one knowledge version, + one tool, + both; openai + one knowledge version; openrouter prompt/evals | #947, #998, #1008, #1051, #1080 | M17 + addendum; changelog 833.*-verified; providers.md | V for those combinations; two or more tools and any MCP server M | not run |
| C15 | `new agent --stack-for` (onboard an `agent.yaml` alone) | #911 | M7 (console onboarding, then release 2/2) | V | not run |

## Console (`agentbreeder up`)

| Row | Feature | PRs | Evidence | Claim | a3 public-registry run |
|---|---|---|---|---|---|
| U1 | Console foundation: single instance, port policy, schema migration from a2 | #849, #852, #857, #858 | M0a | V | sign-in only (`npx … up --no-browser`, scripted via the API, no browser) |
| U2 | Console shell, every a2 screen, rename to "console" | #871, #873, #874 | M0b (Chrome; onboarding, grant review, release and cluster steps driven by API, not clicked) | V | not run |
| U3 | Guided path checklist, Next pill, Create starter | #900 | M4 console half (Playwright) | V | not run |
| U4 | Try it (chat with production, Turn details) | #918 | M7, M15, M17 | V | not run |
| U5 | Agents list and agent detail with the gate strip | #928, #1073 | M7 | V | not run |
| U6 | Onboard an `agent.yaml` alone (code confirm) | #938 | M7 | V | not run |
| U7 | Registry browser (list, version, diff, used-by) | #915 | M8 console addendum | V | not run |
| U8 | Audit view (paging, agent links) | #934, #937 | M8 console addendum | V | not run |
| U9 | Import by path, fork (code confirm) | #976, #981 | M9a console addendum | V | not run |
| U10 | Prompt editor, Move agent to this version, Build and update agent.lock | #943, #954 | M10 | V | not run |
| U11 | Eval-set case editor | #960 | M10 | V | not run |
| U12 | Gate-failed view | #968 | M10 | V | not run |
| U13 | Tool detail, tool code editor, tool execution log | #955, #961, #963 | M12: console half not run (Playwright specs in CI only) | M | not run |
| U14 | Knowledge index status and document editing | #971, #973 | M14 | V | not run |
| U15 | MCP grants screen, grant and revoke, services up with MCP servers, Review repin | #965, #967, #969, #1007 | M15 real-token addendum | V | not run |
| U16 | Build an agent (form, sticky preview, Create) | #962, #966 | M17 + addendum (gemini prompt+evals, and one tool) | V | not run |
| U17 | Logs viewer and Follow | #910, #995 | M5 console half (API-driven, no click-through) | V | not run |
| U18 | Container events and failed jobs on Home | #1004 | M5 console half (API-driven) | V | not run |
| U19 | Docker-down blocker | #972 | M6: Docker stop step not run (fake CLI only) | M | not run |
| U20 | Service detail with Start/Stop/Restart; Home services table and Usage tab | #974, #975, #980 | M6 (Restart, Usage tab, Home table) | V | not run |
| U21 | Traces tab, Try it trace spans, eval case trace | #940, #999 | 907-tracing (Playwright, live Phoenix) | V | not run |
| U22 | LLM cost cards, Try it per-turn cost | #933, #1003 | 906-cost | V | not run |
| U23 | Eval report viewer | #945 | 907-tracing (Releases → Eval report → case trace) | V | not run |
| U24 | Model error codes, fallback, cached tokens and guardrail block in Try it/Traces | #1104 | fake CLI only (changelog 1062) | M | not run |
| U25 | Coded failures keep their cause in job rows | #1063 | unit and UI tests | M | not run |

## Models and gateway

| Row | Feature | PRs | Evidence | Claim | a3 public-registry run |
|---|---|---|---|---|---|
| G1 | Gemini through `service/litellm` | (a2) | Northwind 61/61 in M5, M7, M8, M11, M12, M14 | V | ran: Northwind 61/61 from pip |
| G2 | OpenRouter, default `openrouter/google/gemini-3.8-flash` | #917, #1060 | providers.md (release 2/2, invoke 2/2) | V (default model only) | not run |
| G3 | OpenAI, default `openai/gpt-5.6`; `text-embedding-3-small` knowledge | #917, #1080 | providers.md re-run | V (default model; a tool on openai not run) | not run |
| G4 | Anthropic | #917 | providers.md: blocked, key not workspace-scoped | M: config-only, not verified | not run |
| G5 | Ollama via `service/litellm@1.2.0`, `new agent --model ollama` | #1108 | ollama.md, PR body: `runtime/adk@1.3.0`, macOS only | V on `runtime/adk@1.3.0` only; Linux, kind and 1.4.0 not run | not run |
| G6 | Vertex AI, Bedrock, Azure OpenAI | #1096 | review rules only; `up` refuses them (`GATEWAY_PROVIDER_UNSUPPORTED`) | M: config review only, not served | not run |
| G7 | Gateway config review: provider allow-list, `model_info` only `mode`, logging pinned off | #1035, #1038, #1096 | unit tests; M4 read the spend logs (no messages kept) | V (logging off), M (review rules) | not run |
| G8 | Fallbacks must be granted (`GATEWAY_FALLBACK_UNGRANTED`); granted fallback served | #1046 | gateway-limits re-check; #1095 PR body (OpenRouter fallback served a Northwind turn) | V | not run |
| G9 | Budget (`budget_usd`) refused once spent | (a2), #1066 | gateway-limits (refused); #1095/#1066 PR bodies: `MODEL_BUDGET_EXCEEDED` on `runtime/adk@1.3.0` | V | not run |
| G10 | `rpm` cap | (a2), #1066 | gateway-limits: 429 on direct calls, `invoke` on 1.2.0 waited 67.6 s; docs/agent-yaml.md `runtime/adk@1.3.0`: `MODEL_RATE_LIMITED` seen live once (0.8 s) | V (direct 429; 1.2.0 delay; 1.3.0 code seen once) | not run |
| G11 | `runtime/adk@1.3.0`: coded model errors, bounded retries, `fallbacks`/`served_by` | #1066 | PR body live run; Northwind 61/61 on 1.3.0 (#1095) | V | ran: Northwind 61/61 on `runtime/adk@1.3.0` (fallback and error codes not exercised) |
| G12 | `runtime/adk@1.4.0`: `cached_prompt_tokens`, unknown tools refused, `MODEL_BLOCKED` | #1107 | PR body live run and docs/agent-yaml.md (cached tokens on OpenAI from turn 2, on Gemini only past ~5,000 prompt tokens, none on OpenRouter; SSN block; starter 2/2); unknown-tool path tests only | V (cached tokens, `MODEL_BLOCKED`, starter); M (unknown tools) | not run |
| G13 | `runtime/adk@1.2.0`: per-turn `cost_usd`, tool event detail | #927 | 906-cost | V | not run |
| G14 | Key-reserved names under a floor and gateway-only credentials (`SECRET_RESERVED`) | #1061, #1096 | unit tests | M | not run |

## Guardrails

| Row | Feature | PRs | Evidence | Claim | a3 public-registry run |
|---|---|---|---|---|---|
| R1 | Gateway floor: Presidio services, `litellm@1.1.0`, `environment.guardrails`, `GUARDRAILS_UNVERIFIED`/`CHANGED`/`INEFFECTIVE` | #1061, #1081 | #1081 PR body and guardrails spec: `tests/test_guardrails_docker.py` passed on real Docker (macOS, 2026-11-15) with a scripted provider | V (real Docker, scripted provider) | not run |
| R2 | Agent-level guardrails (`agent.yaml` `guardrails:`), LiteLLM policy per key, `DRIFT_GUARDRAILS` | #1102 | #1102 PR body: `tests/test_agent_guardrails_docker.py` passed on real Docker (macOS, 2026-11-16): two keys minted and attached with the functions `deploy` uses | V (keys and policy on real Docker); M (`deploy`, `status`, `reconcile` paths, unit tests) | not run |
| R3 | A blocked turn answered `MODEL_BLOCKED` by `runtime/adk@1.4.0` | #1107 | PR body: one Northwind run, US SSN blocked | V (one run) | not run |
| R4 | What guardrails do not cover (tool arguments, agent's own storage, detection quality) | #1081 | services.md | stated as limits | not run |

## Cost and traces

| Row | Feature | PRs | Evidence | Claim | a3 public-registry run |
|---|---|---|---|---|---|
| T1 | `cost` by deployment and model, equal to LiteLLM's spend log | #924 | 906-cost (Northwind, 260 calls) | V; an estimate, not a bill; a key's budget being reached not run in that record | not run |
| T2 | `cost` `CACHED IN` column | #1107 | PR body (matched spend logs) | V | not run |
| T3 | `cost` `no price (local model)` for Ollama | #1108 | ollama.md | V | not run |
| T4 | `traces` per deployment, span tree, no message content | #931, #1045 | 907-tracing + addendum | V | not run |
| T5 | Phoenix read-only VIEWER key (`phoenix@1.1.0`) | #1013 | opt-in real-image test (PR body) | V (single-container test) | not run |
| T6 | "still arriving" mark in the CLI | #1045 | 907 addendum: not seen in the CLI | M | not run |

## Registry, packages and release

| Row | Feature | PRs | Evidence | Claim | a3 public-registry run |
|---|---|---|---|---|---|
| K1 | `registry show`, `diff`, `used-by` | #850 | M8 | V | not run |
| K2 | `registry import` (`--dry-run`, `--expect-digest`), `registry fork` | #887, #894 | M9a | V | not run |
| K3 | `registry edit`, `agent set-ref` | #904, #990 | M10, M12, M14 | V | not run |
| K4 | Tool checks at write (`TOOL_INVALID`), `registry show` functions | #930 | M12 | V | not run |
| K5 | `tool log` | #944 | M12 | V | not run |
| K6 | `knowledge status` | #948 | M14 | V | not run |
| K7 | `release` writes `agent/<name>@X.Y.Z` package; `--version`; `VERSION_EXISTS` | #923, #926, #935 | M8 addendum (`agent/northwind-support@0.1.0`), M15 (`agent/gh-reader@0.1.0`), M12 (`VERSION_EXISTS`) | V | ran: `release` in the Northwind run from pip |
| K8 | `deploy agent/<name>@X.Y.Z --local` | #939 | M15 ran a refusal only (`MCP_TOOL_NOT_GRANTED`); success path tests only (PR body: "Not run") | M | not run |
| K9 | Skill kind (`skill/…`, `registry new skill`) | #1103 | unit tests | M (author and lock only; no runtime loads skills) | not run |
| K10 | Eval `tags`, `tag_threshold`, unknown `expect` keys refused | #1109 | unit tests | M | not run |
| K11 | `evals list\|show` | #936 | 907-tracing: the console's viewer runs `evals show --json`; no record runs the CLI text output | V (through the console); CLI text M | not run |
| K12 | Eval `must_match` patterns time-limited (5 s) | #957 | unit tests | M | not run |

## MCP

| Row | Feature | PRs | Evidence | Claim | a3 public-registry run |
|---|---|---|---|---|---|
| P1 | `mcp grant\|revoke` | #942 | M15 (stub and real token) | V | not run |
| P2 | `services pins`, `up --repin --expect-pins` | #1001 | M15 real-token addendum, on drift simulated in the sandbox's pin files; opt-in Docker test against a mock upstream | V (simulated drift) | not run |
| P3 | Revoke applied at the next `services up` while in use | #1092, #1082 | #1082 PR body: live GitHub run, revoke + `services up` while deployed, then `TOOL_NOT_GRANTED` | V | not run |
| P4 | MCP relay tool calls in the audit log (`mcp-relay@1.0.2`, `mcp_tool_call` rows) | #1082 | PR body: live run with the real GitHub MCP server | V; limits: authenticated calls only, written after the reply, imported at `audit list`/`verify`, agent unverified | not run |
| P5 | An agent calling an MCP tool with a real model | (a2 #748), #942 | M15: `invoke` with Gemini listed and called `list_issues`; `mcp/playwright` only with a scripted model | V (GitHub, Gemini); Playwright M | not run |

## Services

| Row | Feature | PRs | Evidence | Claim | a3 public-registry run |
|---|---|---|---|---|---|
| S1 | `services start\|stop\|restart NAME`, per-stack lock `SERVICES_BUSY` | #941 | M6 (`restart litellm`) | V | not run |
| S2 | `services usage` | #875 | M6 (console Usage), #875 PR body | V | not run |
| S3 | `postgres-pgvector@1.2.0` console role | #921 | M12 | V | not run |
| S4 | `services down` removes leftover networks | #1091 | fake Docker only | M | not run |
| S5 | longer health wait for a cold service (#979), deploy port race fix (#978) | #985, #988 | M7/M12 saw #978 before the fix | X (fixes) | not run |

## Sub-agents and skills

| Row | Feature | PRs | Evidence | Claim | a3 public-registry run |
|---|---|---|---|---|---|
| A1 | `agent.yaml` `subagents:`, `limits:`; build refuses (`RUNTIME_NO_SUBAGENTS`) | #1109 | unit tests | M: schema and build only, no runtime runs them | not run |
| A2 | `skills:` in `agent.yaml`; build refuses (`RUNTIME_NO_SKILLS`) | #1103 | unit tests | M: schema and build only | not run |

## Identity gate and boundary

| Row | Feature | PRs | Evidence | Claim | a3 public-registry run |
|---|---|---|---|---|---|
| I1 | Guardrails floor in the gate, Cedar `guardrails` set | #1061, #1081 | R1 | V (as R1) | not run |
| I2 | Egress decision protocol v2 (derived keys) | #1099 | PR body: `tests/test_egress_docker.py` 2 passed on Docker Desktop | V | not run |
| I3 | Egress engine probe timeouts (#732, #991) | #1000, #1002 | fixes | X | not run |

## Kubernetes on kind

| Row | Feature | PRs | Evidence | Claim | a3 public-registry run |
|---|---|---|---|---|---|
| Q1 | `cluster up`, `promote --to kubernetes`, `forward`, `remove --cluster` on kind | (a2 #783) | a2 records; M3 (`cluster up/down`), M5 (`promote`, `logs` on kind), M0b (61/61) | V on local `kind` only | not run |
| Q2 | `cluster down` removes the registry volume | #855 | M3 finding, fixed | M | not run |
| Q3 | Production Kubernetes, GKE | specs #950, #951, #946 | specs only | X | not run |

## Not mentioned at all

Specs and plans (#1029, #1032, #1079, #1093, #1098, #1100, #885, #886, #891, #919, #842, #946,
#950, #951), multi-framework probes (#1110, F0: no product command ran), internal tests and
refactors (#840, #865, #895, #929, #1006, #1012, #1067), A2A, authenticated invocation and every
cloud target.
