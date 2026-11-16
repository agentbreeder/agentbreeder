# Plan: sub-agents (#1058) and skills (#1059), epic #1057

- Specs: `docs/superpowers/specs/2026-11-16-sub-agents-design.md` (r3, "the sub-agents spec") and
  `docs/superpowers/specs/2026-11-16-skills-design.md` (r3, "the skills spec"). Kind (b) script
  skills also need `docs/superpowers/specs/2026-11-16-skills-script-runner-addendum.md` ("the
  addendum"), which must pass its own `/architect` and `/security` review before any K6 code.
- **Owner decisions (2026-11-16): every recommendation accepted.** Sub-agents D1–D10: inline,
  agent-as-tool only, ≤ 5 and depth 1, any registry tool ref, `tools` unqualified plus
  `subagents[].tools`, optional `evals.tag_threshold`, LiteLLM tags after the S0 probe, turn cap
  `min(40, parent + 6)` and `max_calls_per_turn` 2 (max 3), acceptance on local Docker, our own
  `SubAgentTool`. Skills D1–D12: both kinds with (a) first, one `skill` kind with code decided by
  the files (fail closed), ADK `SkillToolset` with our own `Skill` objects, eager catalog, no
  unload, scripts in a fresh `--network none` container per run from a host-side runner, Python
  standard library only, `assets/` refused, caps 3/3/5, skills on sub-agents later, one runtime
  version for sub-agents plus skills (a) and the A2A `a2a.card_skills` rename, script skills
  visible to the gate by presence only.
- **Nothing in this plan has been run.** It sequences work; every claim about behaviour is the
  spec's, to be proven by the slice tests and the acceptance runs below.
- Plan of record for lanes and priority: `docs/superpowers/plans/2026-11-11-console-epic.md` and
  `ROADMAP.md`. This plan does not reorder the priority path; it says what to do when #1057 is
  scheduled.

## 1. Slices at a glance

| Slice | Issue | Contents | Lane | Depends on | Docker |
|---|---|---|---|---|---|
| S0 | #1058 | LiteLLM request-tags probe and record | V | — | yes (LiteLLM proxy) |
| K1 | #1059 | `skill` registry kind, `runs_code(kind, path)` and its callers | C | — | no |
| AY | #1058, #1059 | Authoring: `agentfile.py` (`subagents:`, `limits:`, `skills:`), build config, alias union, skill placement without `scripts/`, capability refusals (S1 + K2 as one slice) | A | K1 for the skills half only (§5) | no (stub builder) |
| EV | #1058, #1059 | `evals.py` rules (`subagents_*`, `subagent_tools_include`, `tags`, `skills_loaded`/`_not_loaded`), `release` per-tag report and `tag_threshold`, `cost` grouping by sub-agent (S3 + K4 rules) | A | the specs' reply contract (tests on fixture replies); acceptance after RT | no |
| RT | #1058, #1059 | **One new `runtime/adk` version**: `turn_for` and one turn id, `SubAgentTool`, caps, reply fields, spans, cost by agent and tags; skills (a) hash check, `Skill` objects, `SkillToolset`, plugin caps, events, spans (S2 + K3) | A | AY; S0 for the tag half | no (stub model) |
| AC | #1058, #1059 | Acceptance runs (a) and sub-agents, verification records, `VERIFIED`/`SUBAGENT_PROVIDERS`/`SKILL_PROVIDERS` (S4 + K4 acceptance) | V | RT, EV | yes |
| UI-R | #1059 | Console registry browser for `skill/` rows and the safe `SKILL.md` renderer (K5 part 1) | B | K1 | no |
| UI-B | #1058, #1059 | Console builder (agentspec v2: Sub-agents section, Skills picker) and agent detail cards and trace splits (S5 + K5 part 2) | B | AC (verified combinations), design lenses | no (Playwright) |
| K6 | #1059 | Script skills, after the addendum is approved: K6a–K6f (§9) | A, V | RT, addendum review | yes (K6f) |

Critical path: K1 → AY → RT → AC → UI-B. S0, EV and UI-R run beside it.

## 2. Rules for every slice

- Branch from `origin/main`, one PR per slice (the RT exception is in §6), tests first, the full
  suite green, coverage ≥ 95% on new modules and ≥ 80% on changed files, `ruff check` and
  `ruff format --check src tests scripts`, the touched tests plus `tests/test_cli_help_golden.py`
  on Python 3.11, 3.12 and 3.13, the `/launch` gate, then a Sonnet read-only review looped until
  it converges, with a review row naming the head SHA in the PR body.
- Docs in the same PR: the reference page the slice changes (`docs/agent-yaml.md`,
  `docs/registry.md`, `docs/commands/<cmd>.md`, `docs/evals.md`, `docs/manifest.md`) and a
  `changelog.d/<issue>.<type>.md` fragment marked "on `main`, not in a published preview". No
  README, `website/` or what-ships change until release R (#836), and then only for what an
  acceptance run proved.
- **Unchanged happy path.** Every slice that touches `agentfile.py`, `build.py` or the runtime
  keeps a golden test: Northwind and a minimal agent without `subagents:`, `limits:` or `skills:`
  produce byte-identical `agent.config.json`, deploy template and manifest.
- Files owned by other lanes (`build.py`, `release.py`, `evals.py`: lane A; `registry.py`
  and the registry commands: lane C; `center/**`: lane B) are edited only by their lane or with the
  coordinator's agreement, named per slice below.
- Error codes are added to the code list the CLI uses for `next --json` hints where one exists;
  messages name the location (`subagents[1].tools[0].ref`), never the value.

## 3. S0: LiteLLM request-tags probe (lane V, Docker)

Goal: decide how per-sub-agent cost is matched (sub-agents spec §10.2) before RT writes it.

- Against the pinned `service/litellm` (the version `main`'s Northwind stack uses), with a key minted
  the way `gateway_keys` mints deployment keys:
  1. a chat completion with `extra_body={"metadata": {"tags": ["agentbreeder-subagent:billing"]}}`;
  2. read `/spend/logs/v2` (the endpoint `gateway_keys.py` reads for `llmcost.py`) for that key and check the row carries
     the tag;
  3. a second key sends the first key's tag and its spend is still billed to the second key;
  4. a request without tags still logs normally (no regression for existing agents).
- Output: `docs/superpowers/verification/1058-S0-litellm-tags.md` with the LiteLLM version, the
  request and response field names observed (no key values), and the verdict: **tags** (RT sends
  them, AC groups by tag) or **request id** (RT keeps attribution runtime-side, AC matches per
  request id).
- Acceptance: the record exists and names one verdict; the RT PR cites it.
- No code change. Teardown removes only containers and images the probe created (never below the
  pre-run snapshot).

## 4. K1: the `skill` registry kind (lane C)

Skills spec §5. Files:

- `src/agentbreeder/registry.py`: `KINDS += ("skill",)`; `REF` and `Ref.parse`; per-version checks of
  §5.3 (strict front matter through `strict_yaml.py`, refused keys `SKILL_FIELD_UNSUPPORTED`,
  `references/` rules, kind (a) vs (b) layout, `SKILL_UNDECLARED_SCRIPT`, ADK kebab name and the
  24-character limit for kind (b), no `assets/` or other top-level entries); `runs_code(kind, path)`
  with the fail-closed rules of §5.2 (raises without a path for `skill`); `list` (code column),
  `verify`, `show` (cleaned manifest and front matter, body and references as files, each script's
  description and parameters and its tool name `skill_<name>__<script>`), `diff`, `used-by`,
  `publish`, `scaffold skill/<name> [--scripts]`.
- Callers of `runs_code`, each passing the resolved version path: `registry.py` listing row,
  `stackfor.py` (with the coordinator), `agentspec.py` (with the coordinator),
  `center/builder.py` and `center/registryimport.py` (lane B agrees; the import cross-check decides
  from the fetched files and its docstring drops `CODE_KINDS`), `commands/registry.py` `_notice`.
- `src/agentbreeder/lock.py`: a skill ref materialises as a directory copy and hash (the existing
  path, `lock.materialise`); a test pins it. The lock hash covers the **whole** registry version,
  `scripts/` included (see AY for the second, placed hash).
- M9b's remote-kinds list gains `skill` (skills spec §17) **later**: M9b is paused on the priority
  path; when it resumes, its plan takes `skill` with the code decision made from fetched files.
- Docs: `docs/registry.md` (the kind, layout, limits, refused keys, "a script skill is code"),
  `docs/commands/registry.md` (`scaffold --scripts`), `changelog.d/1059.added.md`.
- Tests (`tests/test_registry_skill.py`, plus edits to `tests/test_registry.py`,
  `tests/test_center_registry_import.py`, `tests/test_center_builder.py`):
  - existing refs parse unchanged; `REF_INVALID` text lists `skill`;
  - each §5.3 refusal, one test per rule, including `metadata.adk_additional_tools`,
    `allowed-tools`, a front-matter anchor, a duplicate key, an undeclared script, a symlink under
    `references/`, `assets/`, a 25-character kind (b) name;
  - `runs_code`: kind (a) false; a `scripts/` directory with no `scripts:` key true; a `scripts:`
    key with no directory true; an unreadable manifest true; `runs_code("skill")` raises;
  - **caller census:** a test greps `src/` for `runs_code(` and fails on a call site not in its list;
  - `scaffold skill/x` writes a valid instruction pack; `--scripts` writes one declared script that
    `verify` accepts;
  - `list`/`verify` include `skill/` rows; `show` output never contains raw control or bidi
    characters (`registry.clean`).
- Acceptance: a published instruction pack and a published script skill both verify, show and
  appear in `used-by`; every refusal of §5.3 has a passing test; the import confirm asks the code
  question for the script skill and not for the pack.

## 5. AY: authoring, build and config (lane A; S1 + K2 as one slice)

Sub-agents spec §5–§6, skills spec §6–§7. One slice because both edit the same functions; the
owner took D11. Only the skills half needs K1 (a `skill/` ref must parse); if K1 is late, AY lands
the sub-agents half first and the skills half rebases on K1, rather than waiting.

Files:

- `src/agentbreeder/agentfile.py`:
  - `SubAgent` model (`name`, `description`, `model` without `budget_usd`/`rpm` →
    `SUBAGENT_FIELD_UNSUPPORTED`, `prompt`, `tools` ≤ 20, `mcp` subset of the parent's entry,
    `knowledge: bool`, `max_calls_per_turn` 1–3 default 2; nested `subagents` → `SUBAGENT_DEPTH`;
    `tool/customer-notes` → `SUBAGENT_NOTES`); `subagents:` ≤ 5, names unique;
  - `Limits(turn_model_calls: 1–40)`, valid only with `subagents:`, default
    `min(40, parent.max_model_calls + 6)` resolved at build;
  - `skills: [{ref}]` ≤ 10, kind `skill` only;
  - the duplicate rule change: the same ref twice is placed once, a different version of the same
    name is still refused;
  - `refs()` parent first, then sub-agents in file order, then skills, deduplicated; `paths()` adds
    sub-agent prompt paths; `aliases()` (parent, sub-agents, then the knowledge embedding alias,
    de-duplicated in that order).
- `src/agentbreeder/build.py`: capability checks (`RUNTIME_NO_SUBAGENTS`, `RUNTIME_NO_SKILLS`,
  `RUNTIME_NO_SKILL_SCRIPTS` for a code skill until K6); name clash checks (`SUBAGENT_NAME_CLASH`
  for `ask_<name>` against the parent's function, MCP and built-in tool names, and within each
  child; `SKILL_NAME_CLASH` reserved for K6); sub-agent prompts through `_prompt_as_prompt_md`;
  skills placed at `artifacts/skill/<name>/` **without** `scripts/`, hashed after placement. **Two
  hashes exist on purpose:** the lock entry's hash covers the full registry version (from
  `lock.materialise`, before stripping), and `agent.config.json` `skills[].sha256` covers the
  stripped, placed copy the runtime checks at start. Stripping happens in `build` after
  `materialise` and before `_config`; K6b later takes the sandbox context from the full,
  lock-verified copy before it is stripped;
  `_config` gains `subagents`, `limits`, `skills` (only when present, so old files produce the same
  bytes); the deploy template's `aliases` from `AgentFile.aliases()`; `build` prints the L1
  catalog size when skills are present.
- `src/agentbreeder/stackfor.py` (coordinator agrees) and `src/agentbreeder/guide_checks.py`: use
  `AgentFile.aliases()`.
- Deploy audit detail: `deployment.py` records `detail.subagents` (names, aliases) and
  `detail.skills` (name, version, sha256, code) from the build record. The build record
  (`<name>.build.json`) gains the same two lists.
- Docs: `docs/agent-yaml.md` "Sub-agents", "Limits" and "Skills" sections, including the §12.2
  statement that tool separation is not isolation and the §13.1 statement that skill text steers
  the model like the prompt; `changelog.d/1058.added.md`, `changelog.d/1059.added.md`.
- Tests (`tests/test_agentfile_subagents.py`, `tests/test_agentfile_skills.py`,
  `tests/test_build_subagents.py`, edits to `tests/test_build.py`, `tests/test_build_template.py`,
  the stackfor and guide-check tests):
  - every refusal code with the location in the message and no value;
  - an older-CLI simulation: a file with `subagents:` against the current `AgentFile` minus the new
    fields gives `AGENT_INVALID` naming the field;
  - `refs()` order and dedupe; identical ref in parent and child placed once; a different version
    refused;
  - alias union order and the fallback aliases reaching `key_models` (unit, with the environment
    model);
  - placed skill has no `scripts/`; `agent.config.json` `skills[].sha256` equals a re-hash of the
    placed files, and the lock's hash equals a re-hash of the registry copy (the two differ for a
    skill with scripts);
  - **golden:** Northwind and a minimal agent: byte-identical `agent.config.json`, template and
    manifest versus `main`;
  - stub builder (`builder=` injection), so no Docker.
- Acceptance: a file with two sub-agents and two instruction packs builds against a runtime
  manifest that lists `subagents` and `skills` (a test fixture runtime), and is refused against
  1.3.0 with the two capability codes; the golden test passes.

## 6. RT: the runtime version (lane A)

Sub-agents spec §7, §10; skills spec §8, §11. **One new `runtime/adk` version: the next free
number at merge** (each runtime slice takes the next free version, #1057). Today that is after
1.4.0, which is in flight for #1056, so RT copies 1.4.0 once it lands (or the newest version on
`main` when RT starts) and expects 1.5.0. Its `manifest.yaml` lists `capabilities: [mcp, subagents,
skills]` and its description says what it adds. K6e later takes yet another full copy.

Because a published runtime version is a hash-locked copy that must not change after it lands, RT
is **one PR with three reviewed commits** (the exception to one PR per slice), not three PRs. A
version may change freely inside its PR; only a merged version is immutable. **Every commit**
re-records the version in `tests/fixtures/builtin_hashes.json` (`tests/test_builtin_append_only.py`
requires every built-in version to be recorded and unchanged) and regenerates
`src/agentbreeder/_content.json` with `scripts/content_manifest.py` (`tests/test_builtin_content.py`),
so the gate passes on each commit:

1. **RT-1, refactor inside the new version directory (no behaviour change):** copy the previous
   version to the new number; add `Runtime.turn_for(context)` with the fail-closed rules
   (invocation id, then session state `agentbreeder:turn`, else `TurnUnknown`); the turn dict gains
   `id`; `_audit`, `tool_runtime`, `mcp_unavailable`, `instruction`, `KnowledgeSearchTool` and every
   `AgentPlugin` callback use `turn_for` and `turn["id"]`; a test fails on `self.turns.get(` or
   `self.runtime.turns.get(` outside `turn_for`. The previous version's runtime tests pass
   unchanged against this commit.
2. **RT-2, sub-agents:** `SubAgentTool(BaseTool)` (declaration `ask_<name>(request)` with the 4,000
   character cap; child `Runner` with `InMemorySessionService` holding only `agentbreeder:turn`; no
   state copy back; parent `user_id`; `RunConfig(max_llm_calls=turn_model_calls + 1)` with the
   child's `LlmCallsLimitExceededError` caught; per-turn `asyncio.Lock`); child construction in
   `Runtime.__init__` (own `LiteLlm` with `CostClient`, own instruction without notes, own tools,
   `KnowledgeSearchTool` when `knowledge: true`, one `RelayToolset` per MCP entry filtered, the
   shared `AgentPlugin`, closed by `close_mcp`); caps in `before_model_callback` (per-agent
   per-turn, turn-wide, `max_calls_per_turn` as "already consulted"); cost by agent in
   `after_model_callback` (`tally.by_agent`), tags per the S0 verdict; spans
   (`agentbreeder.subagent`); `model_called` carries `agent` and that agent's alias; reply
   `subagents[]`; `writes_requests` rows record the sub-agent and the request's SHA-256.
3. **RT-3, skills kind (a):** start-time hash check (`SKILL_TAMPERED` fails start); `Skill` objects
   built from validated front matter, body and flat references only; the §5.3 refusals re-applied;
   one `SkillToolset(..., code_executor=None, discovery_mode=EAGER, tool_filter=[load_skill,
   load_skill_resource])`; `AgentPlugin.before_tool_callback` caps (3 loads, 3 resource loads,
   `SKILL_LOAD_LIMIT`), "already loaded", and the `references/<flat name>` path rule;
   `skill_loaded` events and tool events with name, version, sha256 and resource, never content;
   `after_tool_callback` replaces the span output with `{"chars": n}`; reply `skills[]`.

Files: `src/agentbreeder/builtin_registry/runtime/adk/<new>/` (`manifest.yaml`, `Dockerfile` header
comment, `runtime/agent.py`, `runtime/tools.py`, new `runtime/subagents.py` and
`runtime/skills.py`, `runtime/server.py` reply fields, `runtime/telemetry.py`), its
`runtime_tests/` (`test_subagents.py`, `test_skills.py`, `test_turn_for.py`, plus the copied tests),
the runtime section of `docs/agent-yaml.md`, `docs/traces.md` (sub-agent and skill spans),
`docs/events.md` (`skill_loaded`, `model_called.agent`), `tests/fixtures/builtin_hashes.json`,
`src/agentbreeder/_content.json`, `changelog.d/1058.added.md` and `changelog.d/1059.added.md` lines
for the runtime.

Tests (stub model, in-memory span exporter, no Docker):

- `turn_for`: parent lookup, child lookup by state key, `TurnUnknown` in a child fails only that
  call with an audit event, in the parent ends the turn with the fixed reply; the census test.
- **one turn id:** every audit, ticket and `writes_requests` row a child writes carries the
  parent's turn id.
- `turn_for` fails closed in the edge cases: a callback after its turn ended (entry deleted, never
  a recycled id) raises `TurnUnknown`; an exception mid-turn still deletes the entry; an invocation
  id that is present but unknown and one that is absent both fail closed; a model-written request
  or tool argument containing `agentbreeder:turn` or another turn's id selects nothing (the key is
  read only from session state `SubAgentTool` set).
- **two customers concurrently,** each turn calling a sub-agent tool: each tool row has its own
  customer and the child's trace additions are not attributed to a sibling; child A's session never
  contains B's request (a fresh child session id per call; no child `Runner` or session service
  shared across turns or customers).
- a child called twice in one turn carries no history from the first call into the second.
- the customer id a child's tool sees comes from the server-side turn, even when the model passes a
  different `user_id`-like argument.
- the turn is found when the child runs in a task created with `asyncio.create_task` whose context
  lacks any turn variable (lookup is by session state, never a context variable).
- no state crossing: a child cannot read a key the parent set, and a key the child sets does not
  appear in the parent's state.
- caps: a child with `max_model_calls` above the parent's gets its own count; the turn-wide cap
  turns tools off for every agent and the turn ends within `turn_model_calls` + 2 calls; a third
  `ask_billing` with the default cap returns "already consulted" without running the child; two
  `ask_*` calls in one model response run one after the other.
- cost: `by_agent` sums equal the turn total; a call without the cost header makes that agent
  `cost_unknown`; tags carry only the validated name (or none, per S0).
- spans: the child's agent and LLM spans reach the root `agent_turn` span through their parents.
- reply: `tools` lists child tools unqualified; `subagents[]` in first-call order; an existing
  `tools_exclude` case catches a child's forbidden tool.
- skills: the model's declarations contain exactly `load_skill` and `load_skill_resource`;
  `scripts/x`, `assets/x` and `references/../SKILL.md` refused; a fourth load refused; reloading an
  active skill returns "already loaded"; a placed file edited after build fails start; no span
  attribute contains the body (ADK's own span included); events never carry content.
- the previous version's full runtime test suite passes on the new version (Northwind's
  behaviour).

Acceptance: all runtime tests pass in CI on the pinned runtime requirements; a test fixture
agent with two sub-agents and two packs starts in-process with the stub model and returns the
documented reply shape.

## 7. EV: evals, release and cost (lane A)

Sub-agents spec §9; skills spec §10. The reply fields are the specs' contract (sub-agents spec
§7.6, skills spec §8.5), so EV can start now with tests against fixture replies; its acceptance on a
real reply waits for RT.

- `src/agentbreeder/evals.py`: `subagents_include`, `subagents_exclude`,
  `subagent_tools_include` (`{name: [tool]}`), `skills_loaded`, `skills_not_loaded`; a case may
  carry `tags` (`^[a-z][a-z0-9_-]{0,31}$`, ≤ 5); unknown rule keys still refused; a rule that needs
  a reply field the runtime did not send fails the case with a clear reason, never passes.
- `src/agentbreeder/release.py`: per-tag pass rates in the report; optional
  `evals.tag_threshold` (0–1) fails the release when a tag is below it; off by default, so
  existing releases are unchanged.
- Cost by sub-agent (`agentbreeder cost`, #906): `src/agentbreeder/gateway_keys.py` reads
  `/spend/logs/v2` and whitelists row fields in `_SPEND_FIELDS`, which has no tags today, so it
  gains the tags field (validated, only `agentbreeder-subagent:<name>` values kept) when S0's
  verdict is "tags"; `src/agentbreeder/llmcost.py` (`_call`, `_bucket`) and `commands/cost.py`
  group by sub-agent (by tag, or by request id per S0); unknown-cost calls shown as unknown, never
  zero. `gateway_keys.py` is lane A's.
- **Deviation from the sub-agents spec §16 S3,** which says "usage grouping": `usage.py` is
  container resource usage (`services usage`) and is not touched; the grouping belongs to `cost`.
  A status note in that spec records this.
- Docs: `docs/evals.md`, `docs/commands/release.md`, `docs/commands/cost.md`, `docs/cost.md`;
  changelog fragments.
- Tests: each rule pass and fail; tags report; threshold off vs on; a reply from 1.3.0 (no
  `subagents`) against a `subagents_include` case fails with the reason; cost grouping with known
  and unknown rows.
- Acceptance: Northwind's eval set produces the same report as before (no tags), and a fixture set
  with tags reports per-tag rates.

## 8. AC: acceptance runs and VERIFIED (lane V, Docker)

Sub-agents spec §15; skills spec §15 items 1–5. One Docker session, two verification records:
`docs/superpowers/verification/1058-subagents.md` and `docs/superpowers/verification/1059-skills-a.md`.

- Project: a fixture agent under `tests/fixtures/` or `src/agentbreeder/examples/` (the coordinator
  decides; an example is shipped content), on a verified provider, built with `agentbreeder build`
  on the RT version and deployed locally.
- Sub-agents items 1–8: routing per question; audit row names the sub-agent; Phoenix shows one root
  span per turn with the `ask_<name>` and child spans; per-sub-agent cost equals LiteLLM spend to the
  cent with zero unknown-cost calls; `release` passes with `subagents_include` and a deliberately
  wrong routing case fails; an alias outside the environment's `models` is `MODEL_NOT_ALLOWED`; the
  injection case recorded either way; Northwind 61/61 on the RT version with byte-identical config
  and manifest apart from the runtime ref.
- Skills (a) items 1–5: one skill loaded and the other not (`skills_loaded`, `skills_not_loaded`,
  `must_match` on a phrase only that skill has); no load for an unrelated question; the refused
  front matter at publish and at build; an image whose placed skill was edited fails start;
  Northwind as above (one Northwind run covers both).
- Northwind's `agent.yaml` and `agent.lock` are re-locked to the RT version in this PR, as for
  earlier runtime versions (`tests/test_builtin_append_only.py::test_shipped_examples_lock_to_recorded_builtins`
  requires shipped examples to lock to recorded built-ins), with the 61/61 run as its evidence.
- Then, in the same PR: `agentspec.VERIFIED` gains `("subagents",)`, `("subagents", "tools")`,
  `("skills",)` and the skills combinations actually run; `SUBAGENT_PROVIDERS` and
  `SKILL_PROVIDERS` name the provider used. Nothing else is marked verified.
- Evidence: records with commands, versions and outcomes, no keys, home paths or usernames; the
  console's Playwright video belongs to UI-B.
- Acceptance: both records committed; `VERIFIED` changes only for what ran.

## 9. K6: script skills (after the addendum)

**Gate: no K6 code until the owner answers the addendum's open decisions (A1–A7).** The addendum's
`/architect` and `/security` reviews (Sonnet, read-only) converged at r4 (APPROVED). The addendum fixes the contract; these
sub-slices follow it in order:

| Sub-slice | Contents | Lane |
|---|---|---|
| K6a | Contract: `Manifest.skill_sandbox` and its exclusion when unset from both the spec digest and `gate.revision_digest`, `docs/manifest.md`, `docs/manifest.schema.json`; `DeployTemplate.skill_scripts` (omitted when empty); `bind(..., sandbox_image)` and every caller (`build.py`, `deploy_ref.py`, `release.py`, the `center/` deploy path); package v2 `sandbox_image`; `_kubernetes_requirements` and `_deploy_requirements` refusals; the environment opt-in | A (B for `center/`) |
| K6b | Build: the sandbox context and second `docker build`, the build record field, `SKILL_NAME_CLASH`, lifting `RUNTIME_NO_SKILL_SCRIPTS` for a runtime with `skill_scripts` | A |
| K6c0 | `hostservice.py`: the shared host-service mechanics moved out of `egress/boundary.py`, egress tests unchanged, nothing else; its own PR so the verified egress path is reviewed alone | A |
| K6c | The runner module (`src/agentbreeder/skills_runner/`, with `reap.py`): pin, listen, signed requests and replies with clock offset, parser and schema check, image inspection against the approved `Env`, container run under Docker's default seccomp profile, the per-run reaper as the authoritative deadline, limits and the timeout budget, `SKILL_SCRIPT_KILLED` vs timeout, start/end audit rows | A |
| K6d | Lifecycle in `deployment.py`: every `_run`, status, reconcile, kill switch, enable, remove, failed-attempt cleanup, leftover reaping | A |
| K6e | Runtime: the next `runtime/adk` version after RT with the `skill_scripts` capability, one tool per script, the signing runner client, the 5-run cap | A |
| K6f | Acceptance (b): skills spec §15 items 6–8 and the addendum's escape checks, on Docker Desktop; on Linux before any claim beyond it | V |

Files, tests and acceptance per sub-slice are in the addendum (§12 there). K6a–K6d can merge before
K6e: without a runtime that has `skill_scripts`, `build` still refuses a code skill, so no behaviour
reaches a user. What does ship unreachable is code and contract: the manifest field, the operator
`skill_scripts` field, package v2 and the runner. That is accepted because each has its own tests;
the `egress/boundary.py` refactor is the risky part and is isolated as K6c0.

## 10. Console (lane B)

- **Before planning:** run `frontend-design` and `ui-ux-pro-max` on sub-agents spec §11 and skills
  spec §12 and fold findings into lane B's plan (both specs' review logs leave this to lane B).
- **UI-R (after K1):** `skill/` rows with the code column; the detail renders `SKILL.md` with an
  allow-list Markdown renderer (raw HTML off, no images, `https:`/`mailto:` links only with
  `rel="noopener noreferrer"`, bidi and zero-width characters stripped); script descriptions and
  tool names; import confirm per §5.4 (the code question decided from files, every L1 description
  shown). Tests: an XSS corpus through the renderer; the import confirm for a pack and a script
  skill; Playwright for the browser view.
- **UI-B (after AC):** `agentspec.SPEC_VERSION` 2 shared by both features (`subagents`, `skills`),
  v1 files unchanged; the builder's Sub-agents section and Skills picker, enabled only for
  `VERIFIED` combinations; agent detail sub-agents card and pinned skills; per-sub-agent split on
  Traces and cost; skill loads and runs per turn on Traces. Tests: spec v1 and v2 round trips,
  disabled unverified combinations, Playwright video of building and running the AC fixture agent.
- Acceptance: each PR's tests and Playwright video; the UI never offers a combination `VERIFIED`
  does not list.

## 11. Sequencing and lanes

```
lane V:  S0 ─────────────────────┐                     AC ──────────────────── K6f
lane C:  K1 ──┐                  │                      ▲                       ▲
lane A:  AY (sub-agents half) ──┴─ AY (skills half) ── RT (RT-1..3) ─┤── K6a, K6b, K6c0, K6c, K6d ── K6e ─┘
         EV (fixture replies) ──────────────────────────────────────┘
lane B:       └─ UI-R                                                 └── UI-B
addendum review (this PR) and owner answers ───────────────────────────▶ gate K6a
```

- Start now: S0 (V), K1 (C), AY's sub-agents half and EV (A), the console design lenses (B).
- RT waits for AY (config shape), S0 (tag verdict) and the runtime version in flight (#1056); its
  version number is chosen at merge.
- AC waits for RT and EV; UI-B waits for AC so the builder enables only verified combinations.
- K6 waits for the addendum approval and RT (it builds on the RT version).
- Coordination points: `build.py` is shared by AY and K6b (both lane A); `stackfor.py`,
  `agentspec.py`, `center/builder.py`, `center/registryimport.py` are touched by K1 and need the
  coordinator's and lane B's agreement; one kind cluster per machine is not needed (no K8s work).

## 12. Test plan summary

| Layer | Where | What |
|---|---|---|
| Unit | `tests/` (3.11, 3.12, 3.13) | registry kind, `agentfile`, build, evals, release, cost, manifest, package, runner logic with a fake Docker |
| Runtime | the RT version's `runtime_tests/` (pinned requirements) | stub model, in-memory spans, concurrency, caps, skills tools |
| Golden | `tests/test_build*.py` | Northwind and a minimal agent unchanged byte for byte |
| UI | `tests/ui/` with `AGENTBREEDER_UI_TEST=1` | renderer XSS corpus, builder v2, agent detail; Playwright video |
| Docker | verification records | S0, AC, K6f; Northwind 61/61 on each new runtime version |

## 13. Cross-repo and docs

- No `agent.yaml` JSON Schema exists; `docs/manifest.schema.json` changes only in K6a.
- Cloud: a `CLAUDE.md` note per side when AY and K6a land (no code reads `agent.yaml`, the registry
  or the manifest there).
- Website: nothing until release R; then `what-ships` lists only what AC or K6f ran.
- `CLAUDE.md` gains a per-side paragraph for #1057 when AY lands (OSS, Website, Cloud), like the
  other cross-repo entries.

## 14. Review log

| Round | Reviewer | Result |
|---|---|---|
| r1 | `/architect` (Sonnet, read-only) | CHANGES REQUESTED. Folded in: built-in hash fixture and `_content.json` per RT commit (§6); per-tag cost needs `gateway_keys.py` `_SPEND_FIELDS` (§7, §3); runtime number is "next free", today after 1.4.0 (#1056), and Northwind's re-lock in AC (§6, §8); EV depends on the reply contract, not AY (§1, §7); AY's sub-agents half need not wait for K1 (§1, §5); "usage" vs `cost` deviation recorded (§7, spec status note); M9b `skill` deferred (§4); lock hash vs placed hash (§4, §5); `hostservice.py` refactor as its own slice K6c0 (§9). |
| r2–r4 | `/architect` (Sonnet, read-only) | r2 and r3 checked the plan against the addendum's changes (K6c row updated twice, K6c0 kept); r4 **APPROVED**: plan §9 matches the addendum's sub-slices, digest exclusions, package rules, lifecycle and K6c scope. |
| r1 | `/security` (Sonnet, read-only) | CHANGES REQUESTED on the addendum (folded in there, §15). For this plan: sub-agent tests extended for `turn_for` edge cases, per-call child sessions, a child called twice, server-side customer id and task contexts (§6). |
