# Fresh-wheel verification: `runtime/langgraph@1.0.0` and `runtime/adk@1.5.0`

Status: **both passed, on macOS 13.7.8 x86_64 only (2026-11-19).** Owner decision 2026-11-19:
mark each runtime verified only after a fresh local run done the way a new user would: a wheel
built from current `origin/main` in a clean sandbox, `agentbreeder new agent`, build, deploy,
invoke and evals, through the CLI and through the console (`agentbreeder up`) driven by
Playwright in Chrome, with a real Gemini key and real Docker. Every step below passed. arm64 was
not run (no arm64 host). The Claude in Chrome extension was not connected in this session, so
there is no extension click-through and no GIF; the console was driven by Playwright only.

What this PR changes because of it: `RUNTIMES_RUN` gains both versions, `VERIFIED` gains
`langgraph` for exactly what ran (the starter, and the starter plus one tool, with `gemini` on its
default model), and two findings are fixed (below).

## Setup

| | |
|---|---|
| Host | macOS 13.7.8 x86_64, Docker Desktop engine 27.5.1, Resource Saver off, `kind` 0.33.0 (not used) |
| CLI wheel | `origin/main` at `7289a14d`, exported with `git archive`, `pip wheel --no-deps`: `agentbreeder-3.0.0a3-py3-none-any.whl`, sha256 `27db9fc0…5c73` |
| Console wheel | this branch's working tree (the flip and both fixes, before commit), the same way: sha256 `a48bc639…1d4f` |
| Python | 3.12.8, a fresh venv per sandbox, `[eval]` extra |
| Sandbox | `env -i` with a fresh `HOME` and `TMPDIR`, `USER=demo`, `PIP_NO_CACHE_DIR=1`, a Docker context created in the sandbox `HOME`. One sandbox for the CLI wheel, a second, empty one for the console wheel |
| Keys | a 0600 file with only `GEMINI_API_KEY` from `~/.env`; deleted at the end |
| Model | `gemini/gemini-3.8-flash` (the provider default) for every agent and judge; `service/litellm@1.0.0` |
| Browser | Playwright 1.64.1, Chrome channel, headless, one tab for the session |

Why the console used the branch wheel: on `main` the console's Create refuses a LangGraph form
(`agentspec.VERIFIED` has no `langgraph`), so the console half of the LangGraph run can only run
with the flip in place. The CLI half ran on the unchanged `main` wheel.

## A. `runtime/adk@1.5.0`

### CLI (main wheel)

Two projects, both from `new agent --from-spec` (spec version 2), with `AGENTBREEDER_REGISTRY_PATH`
on a copy of Northwind's registry (`agentbreeder example northwind`, for
`tool/escalate-to-human@1.0.0`) or on a sandbox registry with two instruction packs made with
`registry new skill` and `registry publish`. The specs are the AC run's (`1058-subagents.md`,
`1059-skills-a.md`) with each case's required `reference` added; the skills spec gained a third
case, a warranty question. Both stayed within the AC run's limits (gemini, default model ids, two
sub-agents with one tool among them; two skills, no tool). No hand edits (no Phoenix).

| Step (UTC) | Command | Exit | Result |
|---|---|---|---|
| 00:28 | `new agent desk-v150 --from-spec desk.json --json` (two sub-agents, `billing` and `tickets` with `escalate_to_human`) | 0 | `runtime/adk@1.5.0`, `combination.parts` `[model, prompt, evals, subagents, tools]`, `verified: true` |
| 00:28 | `services up --environment dev --env-file <file> --yes` | 0 | postgres, litellm created, 149 s |
| 00:31 | `build agent.yaml --stack . --environment dev` | 0 | `sha256:ce6d2382…` |
| 00:35 | `release agent.yaml --stack . --environment dev` | 0 | **2/2**; `Tag billing: 1/1`, `Tag tickets: 1/1`; `agent/desk-v150@0.1.0` |
| 00:36 | `invoke desk-v150 'Do you pay refunds back to my card, and how long does it take?' --json` | 0 | 200; tools `[ask_billing]`, sub-agent `billing` (1 model call), reply "within 10 business days", 3 model calls, $0.000954 |
| 00:36 | `invoke desk-v150 'The lid of my Acme kettle snapped off. … urgent.' --json` | 0 | 200; tools `[ask_tickets, escalate_to_human]`, sub-agent `tickets` with `escalate_to_human` (2 model calls), ticket `T-84E43EE1` quoted, 4 model calls, $0.002061 |
| 00:37 | `eval agent.yaml --endpoint <production> --stack . --environment dev` | 0 | **2/2**, mean correctness 1.0, p50 9,870 ms |
| 00:37 | `remove … --local`, `services down --purge --yes` | 0 | |
| 00:38 | `new agent help-v150 --from-spec help.json --json` (skills `returns-policy`, `warranty-policy`) | 0 | `[model, prompt, evals, skills]`, `verified: true` |
| 00:38 | `services up`, `build` | 0, 0 | `sha256:cc6dbeef…` |
| 00:39 | `release` | 0 | **3/3**; tags `plain`, `returns`, `warranty` 1/1 each |
| 00:40 | `invoke help-v150 'I want to return an unopened toaster …' --json` | 0 | tools `[load_skill]`, skills `[returns-policy@0.1.0]`; reply names Form R-77, 45 days, 6 business days; 2 model calls, $0.00233 |
| 00:40 | `invoke help-v150 'Are you open on Saturdays?' --json` | 0 | tools `[]`, skills `[]`; 8 AM to 6 PM Central from the prompt; 1 model call |
| 00:40 | `eval` against production | 0 | **3/3**, p50 4,425 ms |
| 00:40 | `remove`, `services down --purge --yes` | 0 | |

A first `eval desk-v150 --endpoint …` (the agent's name, as the `Next:` line words other
commands) was `AGENT_INVALID Cannot read desk-v150`: `eval` takes the `agent.yaml` path. Not a
product change here; noted for the docs owner.

### Console (branch wheel)

The console's builder has no sub-agent or skill fields, so neither agent can be created there; the
handoff's "the same through the console builder" cannot be done as written. The closest console
run: the same two specs written with `new agent --from-spec` in the second sandbox, and
`agentbreeder up --no-browser --link` started with `AGENTBREEDER_REGISTRY_PATH` on a shared
registry holding the tool and both skills. Then, in Chrome through Playwright:

| Step | Result |
|---|---|
| Home ▸ Add a project, for each project | both added; Services opens |
| Services ▸ Review and update ▸ `GEMINI_API_KEY` ▸ Confirm and start | `desk-v150: Start services` succeeded; `help-v150` the same |
| Build and release ▸ Run release (`desk-v150`) | succeeded: **2/2**, tags billing and tickets 1/1, `sha256:ce6d2382…` (the same image ID as the CLI build: deterministic) |
| Agents ▸ desk-v150 ▸ Try it, two messages | refund: "within 10 business days", tools `ask_billing`, 3 model calls, $0.001320; kettle: ticket `T-1B9A6D06`, tools `ask_tickets, escalate_to_human`, 4 model calls, $0.002705; Runtime `runtime/adk@1.5.0`, Framework Google ADK |
| Run release (`help-v150`) | succeeded: **3/3**, `sha256:cc6dbeef…` (same as CLI) |
| Try it, two messages | toaster: Form R-77, tools `load_skill`; Saturdays: tools none |

Try it's Turn details lists tools but not the reply's `subagents` or `skills` entries; a
possible console follow-up, not a failure.

### The console's starting version (fixed in this PR)

Labelling 1.5.0 run end to end would have made `builder.js`'s `startingVersion` (the newest
version run end to end) start every new form on 1.5.0, where a plain spec is not a verified
combination, so Create would be refused. `startingVersion` now prefers `STARTER_RUNTIME`
(`newagent.RUNTIME`, `runtime/adk@1.4.0`, checked against Python by
`test_the_consoles_starter_runtime_is_new_agents_default`). In the live console with the flip,
the form started on `runtime/adk@1.4.0` (Playwright read the version select). Since the form has
no sub-agents or skills, no form starts on 1.5.0; a user can still pick it.

## B. `runtime/langgraph@1.0.0`

### CLI (main wheel)

| Step (UTC) | Command | Exit | Result |
|---|---|---|---|
| 00:40 | `new agent lg-help --model gemini --runtime runtime/langgraph@1.0.0 --json` | 0 | `runtime/langgraph@1.0.0`, `verified: false` on `main` (the flip makes it true) |
| 00:40 | `services up --environment dev --env-file <file> --yes` | 0 | |
| 00:44 | `build agent.yaml --stack . --environment dev` | 0 | `sha256:8a73802f…` |
| 00:44 | `conformance sha256:8a73802f…` | 1 | **fail: 22 pass, 2 fail, 15 not run.** The two failures are `traces.root_span` and `traces.llm_span`: the starter has `traces: none`, so the image sends no spans. Finding, fixed below |
| 00:47 | `release` | 0 | **2/2** (`hours`, `unknown`); `agent/lg-help@0.1.0` |
| 00:48 | `invoke lg-help 'Hi, my name is Dana. When is support open?' --session s-lg-1 --json` | 0 | 200, "9:00 AM to 5:00 PM Pacific", `framework: langgraph`, `runtime: runtime/langgraph@1.0.0`, 1 model call, $0.000155 |
| 00:48 | `invoke lg-help 'What name did I give you?' --session s-lg-1 --json` | 0 | "You said your name is Dana." (kit history across turns) |
| 00:48 | `eval` against production | 0 | **2/2**, p50 1,893 ms |
| 00:49 | the same project with `traces: otlp` (one line changed), `build`, `conformance` | 0, 1 | **incomplete: 26 pass, 0 fail, 13 not run**, as the F3 record's Northwind image; the trace checks pass |
| 00:51 | `new agent lg-desk --from-spec lgtool.json` (LangGraph, gemini, one tool `escalate-to-human`) | 0 | `[model, prompt, evals, tools]`, `verified: false` on `main` |
| 00:51 | `services up`, `build`, `release` | 0, 0, 0 | **2/2**; tags `plain`, `tickets` 1/1; `sha256:c4ff5bd5…` |
| 00:53 | `invoke lg-desk 'The handle of my Acme mug cracked. …' --json` | 0 | tools `[escalate_to_human]`, ticket `T-01A3719A`, 2 model calls, $0.00107 |
| 00:53 | `eval` against production | 0 | **2/2** |
| 00:54 | `conformance sha256:c4ff5bd5…` | 1 | fail 22/2/15, the same two trace checks (`traces: none`) |
| 00:55 | `remove`, `services down --purge --yes`, both projects | 0 | |

`new agent` could pick the runtime with `--runtime` and with a spec's `runtime`; no fix needed.

### Console (branch wheel)

`agentbreeder up --no-browser --link` in the empty second sandbox; sign-in through the one-time
link file. In Chrome through Playwright:

| Step | Result |
|---|---|
| Home ▸ Build an agent | the form started on Google ADK, version `runtime/adk@1.4.0` |
| Framework ▸ LangGraph, name `lg-console`, a prompt, Preview | "Writes … on LangGraph (runtime/langgraph@1.0.0)", **"Run end to end: model, prompt, evals."**; `agent.yaml` pins `runtime/langgraph@1.0.0` |
| Create agent and add project | created; Home lists `lg-console` |
| Services ▸ Review and update ▸ `GEMINI_API_KEY` ▸ Confirm and start | succeeded, 124 s; postgres healthy, litellm running |
| Build and release ▸ Build only | succeeded, `sha256:39b2aaa8…` |
| Run release | succeeded: **1/1** (the builder's `hello` case); production deployed |
| Agents | `lg-console`, badge "LangGraph runtime/langgraph@1.0.0", ready |
| Agent ▸ Try it, customer `cust-console-1`, two messages | "Hi Dana. Support is open Monday to Friday, from 9:00 AM to 5:00 PM Pacific Time." ($0.000143), then "You gave the name Dana." ($0.000092): the session continued |

### What the flip claims

`runtime_verified` (`registry runtimes`) for `runtime/langgraph@1.0.0`, and `VERIFIED["langgraph"]
= {(), ("tools",)}` with `RUNTIME_PROVIDERS["langgraph"] = {gemini}` (default model only) and
`newagent.STARTER_RUNTIMES`. Not claimed: arm64, any other provider or model id, knowledge, notes,
two or more tools, MCP (the runtime has no MCP client), sub-agents or skills (ADK 1.5.0 only).

## Findings and fixes

1. **The console would start forms on 1.5.0** once it is labelled run end to end (above). Fixed:
   `STARTER_RUNTIME` in `builder.js`, node and Python tests, and the real-registry Playwright
   test (`tests/ui/test_builder_framework.py`) now checks that the form starts on 1.4.0 while
   1.5.0 shows "Run end to end (Northwind)".
2. **`conformance` fails any image built with `traces: none`**, the starter's default, on both
   runtimes: two trace checks fail because the image correctly sends no spans. Fixed: the span
   checks (`traces.*`, `hygiene.spans`, `model_errors.text_spans`) are `not_run` with the reason
   when the project's `agent.yaml`, while it is the file the build record hashed, has
   `traces: none`. Unit-tested against the stub agent and the build-record reader; **not re-run
   live** on the fixed wheel (the `traces: otlp` build above shows the checks pass when spans are
   on).
3. The console's Try it does not show a reply's sub-agents or skills (follow-up, not fixed).
4. `eval` takes the `agent.yaml` path, not the agent name (as above).

## Spend

LiteLLM's spend log was not read before the stacks were purged, so there is no total from the
gateway. Reported turn costs (`cost_usd`) of the live invokes and Try it turns were under $0.02 in
all; the four CLI releases, four CLI evals and three console releases (9 + 9 + 6 eval cases, each
with a judge call) add to that, of the order of the 1058 AC run's cents.

## Leak scan and teardown

- Both sandboxes (`HOME`, work directories, `TMPDIR`) and the console's step logs, screenshots
  and aria snapshots were scanned for the Gemini key's value: **0 files**. The key was typed into
  the console's key field by the Playwright step from the 0600 file and never logged. The key
  files were deleted.
- Removed in order: every production deployment (`remove --local`), `services down --purge --yes`
  per stack (volumes and state), the console processes, then the images missing from the pre-run
  snapshot: those created after the snapshot (00:26:45 UTC) under `<none>` or `agentbreeder-*`,
  plus the three pulled base images this run brought in (`python`, `pgvector/pgvector`,
  `ghcr.io/berriai/litellm-database`), by ID. Nothing was pruned.
- After: the 4 snapshot images (same IDs), no networks or containers of this run, the default
  bridge ID unchanged, build cache 90 entries as before. **One container, one volume and one image
  not in the snapshot were left alone**: `cloud-concierge-v1-pg` (`postgres:16-alpine`, created
  01:37 UTC with an anonymous volume) was started by something other than this lane during the
  run, and was not touched.

## Not run

- arm64 (no host).
- A Chrome click-through with the Claude in Chrome extension, and its GIF (the extension was not
  connected: two `tabs_context` attempts failed).
- `conformance` with the fix on a real engine (unit tests only).
- `kind` for these agents (Northwind on LangGraph already ran there, F3-langgraph.md).
- Phoenix, cost-to-the-cent and gateway-limit checks (F3-langgraph.md and the 1058 AC record
  cover them for these runtimes).
