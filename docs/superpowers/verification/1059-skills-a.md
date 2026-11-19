# #1059 AC verification: skills kind (a), instruction packs, on `runtime/adk@1.5.0`

Status: **verified from `main`** (160b7d10, after #1149 (RT) and #1145 (EV) merged, 2026-11-19,
macOS 13.7.8 x86_64, Docker Desktop engine 27.5.1, LiteLLM 1.106.2 from
`service/litellm@1.0.0`). Items 1 to 5 of the skills spec §15 (kind (a)) passed, on one provider
(`gemini/gemini-3.8-flash`) with one sample per question. Kind (b), script skills (items 6 to 8),
is not built and was not run. Docker was torn down to the pre-run snapshot, and the leak scan
found nothing.

This is on `main`, not in a published preview. It is slice AC of
`docs/superpowers/plans/2026-11-16-sub-agents-and-skills.md` §8. The wheel, sandbox, keys,
keepalive, leak scan and teardown are the ones in `1058-subagents.md`, which also records the
Northwind run (item 5).

## The skills and the project

Two instruction packs, scaffolded with `registry new skill`, edited (the scaffold's
`references/notes.md` removed) and published to a sandbox registry with `registry publish`:

| Ref | `sha256` | Description (in every turn) | Phrase only it has |
|---|---|---|---|
| `skill/returns-policy@0.1.0` | `ee9b227f…` | "How to return a product bought from Acme: deadlines, the return form and refunds. Load it for any return or refund question." | **Form R-77**; 45 days; refund within 6 business days |
| `skill/warranty-policy@0.1.0` | `15a824d2…` | "Acme warranty coverage and repairs for faulty products. Load it for any question about a broken or faulty product or a repair." | **Plan W-12**; 2-year warranty |

Each `SKILL.md` body is four or five short lines and tells the model to name its phrase exactly.

`agentbreeder new agent acme-help --from-spec help-spec.json` (spec version 2), with
`AGENTBREEDER_REGISTRY_PATH` on that registry:

```json
{
  "schema_version": 2, "name": "acme-help", "model": "gemini", "max_model_calls": 3,
  "runtime": "runtime/adk@1.5.0",
  "prompt": {"text": "You are Acme's customer help agent. Acme's support team is open 8am to 6pm Central, Monday to Saturday. Answer briefly. Use a skill when a question is about its topic."},
  "skills": ["skill/returns-policy@0.1.0", "skill/warranty-policy@0.1.0"],
  "evals": {"threshold": 1.0, "retries": 1, "cases": [
    {"id": "return-blender", "tags": ["returns"], "turns": ["How do I send back an unopened blender I bought from you?"],
     "expect": {"must_match": ["R-77"], "skills_loaded": ["returns-policy"], "skills_not_loaded": ["warranty-policy"]}},
    {"id": "hours", "tags": ["plain"], "turns": ["When is your support team open?"],
     "expect": {"must_match": ["8"], "skills_not_loaded": ["returns-policy", "warranty-policy"]}}
  ]}
}
```

`new agent` reported `combination.parts` `[model, prompt, evals, skills]`, `verified: false`
(before this PR). The same two hand edits as for `acme-desk` added Phoenix, the trace proxy,
`traces: otlp` and `agent_events: postgres`.

## Run

| Step (UTC) | Command | Exit | Result |
|---|---|---|---|
| 14:39 | `services up --environment dev --env-file <file> --yes` | 0 | postgres, litellm, phoenix, proxy created |
| 14:42 | `release agent.yaml --stack . --environment dev` | 0 | `Released sha256:d74e2a92…: 2/2 evals passed`; `Tag plain: 1/1`, `Tag returns: 1/1`. `return-blender`: `loaded_skill:returns-policy`, `did_not_load_skill:warranty-policy`, `matches:R-77`, `judge_correct` all true. `hours`: `did_not_load_skill` both, `matches:8`, `judge_correct` true |
| 14:45 | `invoke acme-help 'I want to return an unopened toaster I got last week. What do I do?' --customer cust-010 --json` | 0 | below |
| 14:45 | `invoke acme-help 'Are you open on Saturdays?' --customer cust-011 --json` | 0 | below |
| 14:45 | `traces --trace <id> --json` per turn; the agent's `audit_events`; Phoenix's `spans` table | 0 | below |
| 14:46 | the refused skill (item 4) | 2, 2, 2 | below |
| 14:47 | the edited image (item 4) | — | below |
| 14:48 | `remove <production> --local`, `services down --purge --yes` | 0 | volumes, proxy image and state removed |

## Acceptance (skills spec §15, kind (a))

| # | Check | Result |
|---|---|---|
| 1 | An agent with two instruction packs and a short prompt, built and deployed locally on a verified provider | **PASS.** As above; `release` built (`--locked`) and deployed the candidate and production |
| 2 | A question that needs one skill: the reply's `skills` and the trace show that skill loaded and the other not, and the answer follows the skill (an eval case with `skills_loaded`, `skills_not_loaded` and a `must_match` on a phrase only that skill has) | **PASS.** The eval case passed in `release` (above). The live toaster turn: `tools: [load_skill]`, `skills: [{name: returns-policy, version: 0.1.0, resources: []}]`, 2 model calls, `cost_usd` 0.002868; the reply named "**Form R-77** at acme.example/returns", 45 days and 6 business days, all from the skill. Trace: one root `agent_turn` with `call_llm`, `execute_tool load_skill` [TOOL], `call_llm`. Audit rows: a `skill_loaded` event and a `tool:load_skill` row, each with `skill: returns-policy`, `version: 0.1.0` and the skill's `sha256`, and the row's `args` `{"skill_name": "returns-policy"}`. Nothing names `warranty-policy` |
| 3 | A question that needs neither: no load | **PASS.** "Are you open on Saturdays?": `tools: []`, `skills: []`, 1 model call; the reply gave 8:00 AM to 6:00 PM Central from the prompt. Trace: `agent_turn` with one `call_llm` and no tool span; audit: `message_received`, one `model_called`, `reply_sent` |
| 4 | A skill whose front matter has `metadata.adk_additional_tools` is refused at publish, and when placed by hand, at build; an image whose placed skill was edited after build fails start | **PASS.** A `skill/sneaky@0.1.0` whose front matter had `metadata: {adk_additional_tools: [google_search]}`: `registry publish` exit 2, `[SKILL_FIELD_UNSUPPORTED] … metadata is not supported (only compatibility, description, license, name) …`. Copied by hand into the project's `registry/skill/sneaky/0.1.0` and added to `agent.yaml`: `build` exit 2 with the same code (and `registry verify` reports it `FAIL`, exit 2). The files were removed and `agent.yaml`/`agent.lock` restored. Edited image: below |
| 5 | Northwind (no skills) still passes 61/61 on the new runtime, byte-identical `agent.config.json` and manifest apart from the runtime ref | **PASS**; see `1058-subagents.md`, item 8 |

### Item 4: the edited image

The production container's root file system is read-only, so `docker cp` into the stopped
container was refused ("container rootfs is marked read-only"). Instead a derived image was
built: `FROM` the released image (`sha256:d74e2a92…`, tagged `ab-ac1058-base:1` for the build)
with one `COPY` of `/app/artifacts/skill/returns-policy/SKILL.md` in which "within 45 days" became
"within 450 days". Two containers were started the way the deployment runs (read-only, `/tmp`
tmpfs, user 65532, the deployment's two secret files mounted read-only, on the stack's agents
network): a control from the released image and one from the derived image.

| Container | Log | `GET /health` | `POST /invoke` |
|---|---|---|---|
| control (released image) | starts | 200 `{"status": "ok", "model": "chat"}` | not sent |
| edited image | `runtime start failed: SKILL_TAMPERED: skill/returns-policy@0.1.0: its files changed after the build` | **200** `{"status": "ok", "model": "chat"}` | **503** `{"error": "Agent is misconfigured: SKILL_TAMPERED: skill/returns-policy@0.1.0: its files changed after the build"}` |

The edited text appears in neither the log nor the reply. Both containers, the derived image and
the tag were removed.

**Finding:** `/health` answers 200 after a failed start. `runtime/adk@1.5.0` answers `/health`
from the server alone (as every `runtime/adk` version does; `F2a-adk-baseline.md` lists it as a
kit-profile gap), so a deploy that waits on `/health` would report such a deployment healthy
while every turn is refused with 503.

## Skill text in traces

Phoenix's `spans` table (26 spans: both candidate eval turns and both production turns) was
searched for a sentence only the `returns-policy` body has ("Always name Form R-77"): **0 spans**
held it. 4 spans held the exporter's `withheld` marker. The two `execute_tool load_skill` spans
have the `agentbreeder.skill` attributes `name`, `version` and `sha256` and nothing else from the
skill. The model's own reply, which quotes "Form R-77", is in its LLM span as any reply is.

## Not run

- Kind (b), script skills: items 6 to 8 (not built; K6).
- `load_skill_resource` (neither skill has a reference file), the per-turn caps
  (`SKILL_LOAD_LIMIT`) and "already loaded" against a real model.
- Skills with tools or knowledge, more than two skills, another provider, Linux, and the console
  (UI-B).
- The skills' spend rows were not read before the stack came down; the reply's `cost_usd` is the
  only cost recorded.
