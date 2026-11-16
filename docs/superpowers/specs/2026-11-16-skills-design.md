# Skills: packaged capabilities an agent loads on demand (#1059): design

- Status: **r3, reviews converged** (round 3 APPROVED). The r1–r3 architecture and `/security`
  reviews (Claude Sonnet, read-only) are folded in (§18). Owner decisions D1–D12 are open (§16). No code in this PR.
- **Status note (2026-11-16):** the owner accepted every recommendation (D1–D12). The kind (b)
  deploy contract is fixed in `2026-11-16-skills-script-runner-addendum.md`, which **supersedes
  §9.2–§9.5 where they differ**: the gate's policy request does not carry the manifest, so in v1
  no policy can match `skill_sandbox` (§9.5's "through the manifest field" is corrected there); the
  runner never sends its token; the Linux transport is a mounted socket directory; and `remove`
  keeps the sandbox image. Implementation plan: `docs/superpowers/plans/2026-11-16-sub-agents-and-skills.md`.
- Issue #1059, part of epic #1057. Sibling spec: `2026-11-16-sub-agents-design.md` (#1058).
- **Scope from the owner (2026-11-15): both kinds.** (a) **Instruction packs:** text only, loaded
  on demand. (b) **Agent-Skills-style folders:** a `SKILL.md` plus scripts, which are code and take
  the code rules. This spec designs both. Kind (a) is ready to plan once approved. Kind (b) changes
  the deploy contract (a second image and a host-side runner, §9), so its §9 design is fixed here
  but its slice K6 needs a short deploy-contract addendum, reviewed on its own, before code.
- Builds on (on `main` at 4079ddb8): `registry.py` (`KINDS`, `REF`, `CODE_KINDS`/`runs_code`,
  `verify`, `show|diff|used-by`, `clean`, `scaffold`), `strict_yaml.py`, `agentfile.py`,
  `build.py`, `lock.py`, `stackfor.py`, `agentspec.py` (`VERIFIED`, `TOOL_PROVIDERS`),
  `deployment.py` (`_kubernetes_requirements`), `egress/host.py` (a host-side service started by
  deploy), `runtime/adk@1.3.0` (`agent.py`, `tools.py`, `server.py`), and the pinned
  `google-adk==2.12.0` (`google/adk/skills`, `tools/skill_toolset.py`).
- **Nothing in this document has been run.** The ADK behaviour quoted was read from the 2.12.0
  wheel's source on 2026-11-15, not exercised.

## 1. Problem

Every instruction an agent might need sits in its one prompt and is sent on every model call,
whether the question needs it or not. Long procedures (a refund policy, a returns workflow, a tone
guide for complaints) make the prompt long, expensive and harder to evaluate, and cannot be shared
between agents as a versioned unit. Small deterministic helpers (compute a pro-rated refund, parse
a table) either live in a registry tool, which is code with full agent privileges, or are left to
the model.

The open Agent Skills format (a folder with `SKILL.md`: a name and description in front matter, a
Markdown body, optional reference files and scripts) addresses both, and ADK 2.12.0 has a
`SkillToolset` for it. AgentBreeder has no skills concept: no registry kind, no `agent.yaml` field,
no runtime support.

## 2. Goal

- An agent lists pinned `skill/<name>@X.Y.Z` versions in `agent.yaml`. Until a skill is loaded the
  model sees only its name and description; it loads the full instructions, and if needed one
  reference file, only when a question needs them.
- A skill version is a hash-locked registry version like a prompt: published, listed, verified,
  shown, diffed and traced to the agents that use it.
- (b) A skill version may carry declared Python scripts. Each run happens **outside the agent
  container**, in a fresh container with no network, no secrets and no access to the agent's data,
  under the same import confirm, verify and VERIFIED rules as other code.
- Every load and every script run shows in the reply, the agent events and the trace.

## 3. Scope and non-goals

In scope: the registry kind (§5), `agent.yaml` (§6), build and lock (§7), the runtime for (a) (§8)
and (b) (§9), the token budget (§8.4), evals and VERIFIED (§10), traces (§11), the console (§12),
security (§13).

Not in scope:

- Skills that add tools to the agent (ADK's `adk_additional_tools` front-matter key) or read session
  state (`adk_inject_state`). Both are refused (§5.3): a skill must never raise what an agent can do.
- Skills fetched at run time (ADK's GCS loaders, `SkillRegistry`/`search_skills`). Skills are baked
  into the image from the lock, like every other artifact.
- Scripts in any language but Python, shell scripts, and scripts with third-party dependencies (D7).
- Agent Skills `assets/` (D8).
- Skill scripts on the Kubernetes target (§9.6).
- Skills on sub-agents (#1058); a follow-up once both land (D10).

## 4. Terms

| Term | Meaning |
|---|---|
| skill | A registry version `skill/<name>@X.Y.Z`. |
| L1 | A skill's name and description, always in the model's context. |
| L2 | The `SKILL.md` body, returned when the model calls `load_skill`. |
| L3 | A reference file, returned by `load_skill_resource`. |
| instruction pack | A skill with no scripts: kind (a). Text only. |
| script skill | A skill with scripts: kind (b). Code. |
| script runner | The host-side service that runs one script per fresh container (§9.2). |

## 5. The `skill` registry kind

### 5.1 Layout

```
registry/skill/refund-policy/1.2.0/
  manifest.yaml          # kind: skill, name, version, description, [scripts]
  SKILL.md               # front matter (name, description) + Markdown body (L2)
  references/            # optional L3 text files, flat
    eu-rules.md
  scripts/               # kind (b) only; every file declared in manifest.yaml
    prorate.py
```

```yaml
kind: skill
name: refund-policy
version: 1.2.0
description: "How to decide and explain refunds, including EU cooling-off rules."
scripts:                       # kind (b); absent for an instruction pack
  - name: prorate
    path: scripts/prorate.py
    description: "Pro-rated refund for a subscription cancelled mid-period."
    parameters:                # JSON Schema, the subset tool.yaml allows
      type: object
      properties: {price: {type: number}, days_used: {type: integer}, days_in_period: {type: integer}}
      required: [price, days_used, days_in_period]
```

### 5.2 One kind; code decided by the files (recommended, D2)

| Option | Effect |
|---|---|
| **One `skill` kind**; a version with any file under `scripts/` is code | Matches the Agent Skills format, where scripts are optional. The ref alone does not say whether it is code, so every code check reads the version's files. |
| Two kinds (`skill` text only, plus a code kind) | The ref says it and `CODE_KINDS` stays per kind, but adding one script to a skill becomes a rename. |

Recommendation: **one kind, fail closed.** `registry.runs_code(kind)` becomes
`runs_code(kind, path)`:

- true for every `CODE_KINDS` kind, as today;
- for `skill`: true when the version directory has a `scripts/` entry of any kind, **or** its
  manifest declares `scripts:`, **or** the directory or manifest cannot be read;
- calling it for `skill` without a path raises (a programming error, never a silent false).

The callers today pass only the kind: `registry.py:590` (the listing row), `stackfor.py:222` and
`agentspec.py:431` (the `runs_code` summaries the console's onboarding and builder read),
`center/builder.py:233` (the builder catalogue; `:364` reads the row flag back),
`center/registryimport.py:174` (the console import cross-check, which today recomputes the flag from
the kind and raises `BadOutput` on a mismatch), and `commands/registry.py:343` (`_notice`, which
picks the "code" or "steers the agent" notice). Each passes the resolved, staged or fetched version
path, so the import confirm and its cross-check decide from the files, never the kind alone. The
`CODE_KINDS` mention in `registryimport.py`'s docstring is updated. A test enumerates every
`runs_code(` call in `src/` and fails on one not in its list.

### 5.3 What `publish`, `verify`, `build` and every read check

All existing per-version checks apply (no symlinks, size limits, no hidden files, cleaned text,
fail-closed resolution). In addition:

- `SKILL.md` is UTF-8, ≤ 32 KiB. Its front matter is parsed with the repository's strict YAML
  loader (`strict_yaml.py`: no anchors, aliases, merge keys or duplicate keys, a size cap) and has
  exactly `name` (equal to the manifest's) and `description` (equal to the manifest's, 1–1024
  characters, ADK's `Frontmatter` limit, passed through `registry.clean`). `license` and
  `compatibility` are allowed and shown.
- **Refused front-matter keys** (`SKILL_FIELD_UNSUPPORTED`): `metadata` (which carries ADK's
  `adk_additional_tools` and `adk_inject_state`), `allowed-tools`/`allowed_tools`, and anything not
  listed above. ADK's `Frontmatter` accepts extra keys (`extra="allow"`, `skills/models.py`), so the
  check is ours: at publish, `verify`, `build`, and again in the runtime (§8.1).
- `references/` holds at most 20 files, each ≤ 64 KiB, `.md` or `.txt`, UTF-8, flat; names match
  `^[a-z0-9][a-z0-9._-]{0,63}\.(md|txt)$`.
- **Kind (a):** no `scripts/` and no `scripts:` key.
- **Kind (b):** `scripts:` has 1–10 entries. Each `name` matches `^[a-z][a-z0-9_]{0,23}$`; each
  `path` is `scripts/<module>.py` with a valid module name, exists and is ≤ 64 KiB; every file in
  `scripts/` is declared. Anything else is `SKILL_UNDECLARED_SCRIPT`.
- No other top-level entries (`assets/` included).
- The skill `name` matches both `registry.NAME` and ADK's kebab-case rule
  `^[a-z0-9]+(-[a-z0-9]+)*$` (`skills/models.py`), so a name that passes publish never fails at
  runtime start and `skill_<name>__<script>` is unambiguous; for kind (b) it is ≤ 24 characters so
  script tool names fit (§9.4).

### 5.4 Everything `KINDS` touches

Adding `skill` to `KINDS` (`registry.py:42`) extends the `REF` pattern (`:50`) and so `Ref.parse`
and the `REF_INVALID` text, the `list` and `verify` loops (`:851`, `:870`), `show`, `diff`,
`used-by`, `publish`, `scaffold` and the lock (a skill ref is a directory copy and hash, as in
`lock.materialise`, `lock.py:120`). Tests pin: existing refs parse as before; the `REF_INVALID` text
lists `skill`; `list` and `verify` include `skill/` rows; `scaffold skill/<name>` writes an
instruction pack and `--scripts` adds one example script and its declaration.

Views:

- `registry list` shows `skill/` rows with a `code` column.
- `registry show skill/...` renders the manifest and front matter as cleaned fields, the `SKILL.md`
  body and references as files, and each script's description and parameters.
- **Import confirm.** The console's import (and `new agent --from-spec`'s `runs_code` summary) asks
  the "this code runs" confirm for any version `runs_code(kind, path)` says is code, and always shows
  every skill's L1 description, since that text is in every turn of every agent that pins it
  (§13.1). For a version from a remote source (M9b) the decision comes from the fetched files, not
  only the publisher's manifest.

## 6. `agent.yaml`

```yaml
skills:
  - ref: skill/refund-policy@1.2.0
  - ref: skill/complaint-tone@1.0.0
```

- At most 10 entries, refs of kind `skill` only, one version per name (the existing rule). The field
  is optional; files without it are unchanged and `schema_version` stays 1. An older CLI refuses a
  file with `skills:` with its existing unknown-field `AGENT_INVALID` message.
- `build` refuses `skills:` on a runtime without the `skills` capability (`RUNTIME_NO_SKILLS`), and a
  script skill on a runtime without `skill_scripts` (`RUNTIME_NO_SKILL_SCRIPTS`).
- **Naming collision with A2A.** The A2A spec's `a2a.skills` is the Agent Card's list of advertised
  skills, a different thing. A status note added to that spec in this PR records the rename to
  `a2a.card_skills` (D11) before A2A is built, so `skills:` in `agent.yaml` means only this.

## 7. Build, lock, manifest

- **Lock.** Ordinary lock entries (`{"sha256", "source"}`); no format change. A remote skill from an
  M9b source keeps M9b's source label, shown on import and in `registry show`.
- **Build context.** Each skill is placed at `artifacts/skill/<name>/` (`build.py:394`) **without
  its `scripts/` directory**: the agent image never contains skill scripts.
- **`agent.config.json`** gains `skills: [{name, version, dir, sha256, scripts: [{name, tool,
  description, parameters}]}]`; `sha256` is the hash of the placed files.
- **Kind (a): no deploy template or manifest change.** No `skills:` means byte-identical output.
- **Kind (b): a deploy contract change** (§9.5), designed in the K6 addendum.

## 8. Runtime, kind (a): instruction packs (§14)

### 8.1 ADK's `SkillToolset` with our own `Skill` objects (recommended, D3)

At start the runtime checks each placed skill directory's hash against `agent.config.json`
(`SKILL_TAMPERED` fails start), then builds `google.adk.skills.Skill` objects **itself** (not with
`load_skill_from_dir`): front matter with only `name` and `description`, the body, and
`resources.references` from the validated flat file names. It never fills `resources.scripts` or
`resources.assets`. It re-applies the §5.3 front-matter refusals. It adds one
`SkillToolset(skills, code_executor=None, discovery_mode=EAGER, tool_filter=["load_skill",
"load_skill_resource"])` to the agent's tools:

- With no code executor ADK neither offers `run_skill_script` nor describes it
  (`skill_toolset.py:578`). `RunSkillScriptTool` is still constructed inside the toolset, so a unit
  test asserts the declarations the model receives contain exactly `load_skill` and
  `load_skill_resource`.
- `load_skill_resource` accepts `references/`, `assets/` and `scripts/` paths
  (`skill_toolset.py:~1077-1090`). With empty `assets` and `scripts` the latter two return not-found;
  the plugin (§8.3) also refuses any path not of the form `references/<flat name>`, and a test
  covers `scripts/x`, `assets/x` and `references/../SKILL.md`.
- `EAGER` puts the L1 catalog (`<available_skills>`, HTML-escaped, `skills/prompt.py`) into the
  system instruction, so the first `load_skill` needs no `list_skills` turn.

ADK's skills classes are recent and partly marked experimental. The runtime pins `google-adk`
exactly; a later ADK pin is a new runtime version with its own tests, so API drift cannot reach a
deployed agent unseen. The alternative, our own ~150-line loader with two tools, avoids ADK's
surface and front-matter extensions at the cost of duplicating it (D3).

### 8.2 What the model sees

| Before any load | After `load_skill("refund-policy")` | After `load_skill_resource(...)` |
|---|---|---|
| The prompt, ADK's short skill instruction, and the L1 catalog | A function response with the `SKILL.md` body | A function response with one reference file |

Loaded content is a function response, not part of the system prompt. It stays in the persisted
session history, and ADK records activated skills in session state (`_adk_activated_skill_<agent>`,
`skill_toolset.py:102-120`), which `DatabaseSessionService` persists, so a later turn of the same
session knows a skill is loaded. The prompt and the customer-memory block keep their positions.

### 8.3 Caps and the "already loaded" rule (in `AgentPlugin`)

These are not ADK features; `AgentPlugin.before_tool_callback` enforces them and returns a tool
error result to the model (no tool runs):

- at most 3 `load_skill` and 3 `load_skill_resource` calls per turn (`SKILL_LOAD_LIMIT`);
- a `load_skill` of a skill already active in the session returns "already loaded" instead of the
  body;
- the path rule of §8.1.

Each call also counts as a tool call inside the agent's existing `max_model_calls`.

### 8.4 Token budget

- L1: at most 10 × (name ≤ 64 + description ≤ 1024 characters) plus ADK's instruction, about 3,500
  tokens at worst, on every model call. `build` prints the L1 size; the docs advise short
  descriptions.
- L2 ≤ 32 KiB (~8k tokens); L3 ≤ 64 KiB per file (~16k tokens). With the caps, a turn adds at most
  about 72k tokens of skill content, bounded further by the model's context and the key budget.

### 8.5 Events and reply

- A load is an ordinary tool event (`tool:load_skill`, `tool:load_skill_resource`) with `skill`,
  `version`, `sha256` and the resource name, never the content. A `skill_loaded` agent event carries
  the same fields.
- The reply gains `skills: [{name, version, resources: [...]}]` for the turn.

## 9. Runtime, kind (b): script skills

### 9.1 Where a script runs (D6)

| Option | Secrets | Network | Agent data | Other customers' runs | Verdict |
|---|---|---|---|---|---|
| In the agent process (ADK's default executors) | readable | yes | DB, memory | shared | refused |
| Subprocess in the agent container | the secret files are readable by the same user | yes | `DATABASE_URL` file | shared | refused |
| A long-lived sandbox container beside the agent on an internal network | none | can reach the agent's unauthenticated `/invoke` on the shared network | through `/invoke`, as any customer | shared `/tmp` and processes | refused (r1 security review) |
| **One fresh container per run, `--network none`, started by a host-side runner** | none | none (no interface but loopback, no DNS) | none | nothing shared | **recommended** |
| A microVM or gVisor runtime | none | none | none | nothing shared | not available on Docker Desktop; later |

### 9.2 The script runner

- **A host-side service per deployment**, like `egress/host.py`: started by `deploy` when the
  manifest has a script sandbox (§9.5), stopped by `remove`, the kill switch and `reconcile`, and
  listening only where the agent reaches host services today (`host.docker.internal`,
  `runtime.py:490-493`). The agent authenticates with a per-deployment 256-bit token from a secret
  file mounted only into the agent; the runner holds the token's SHA-256 in a file in its private
  state directory and compares in constant time.
- **A request** is `{"turn", "skill", "version", "script", "args"}`. The runner checks the skill,
  version and script are declared for this deployment, validates `args` against the declared
  `parameters`, and runs one container:

  ```
  docker run --rm --network none --read-only --tmpfs /tmp:size=16m \
    --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges \
    --pids-limit 32 --memory 256m --cpus 0.5 --ulimit nofile=64 --ulimit cpu=10 \
    -i <sandbox image> python -I -S /skills/<skill>/scripts/<module>.py
  ```

  with `args` as JSON on stdin, an empty environment, and the working directory set to that skill's
  read-only copy. The runner kills the container at 10 s wall clock, reads stdout while streaming and
  stops at 64 KiB, and keeps the last 4 KiB of stderr.
- **No sharing between runs or customers:** each run is a new container from the image, removed
  after; nothing persists. The image holds only skill files, so the worst a script can read is
  other skills' scripts and references of the same deployment, which are not secret (they are in
  the registry). The runner, its token hash and the agent are outside the container.
- **Limits:** at most 5 runs per turn (`AgentPlugin`), at most 2 concurrent runs per deployment
  (further ones queue for 5 s, then `SKILL_SANDBOX_BUSY`), and a host-wide limit on concurrent
  sandbox containers.
- **Reply to the agent:** `{"ok", "stdout", "stderr_tail", "exit_code", "duration_ms"}`.

### 9.3 The sandbox image

A second image built by `agentbreeder build` from a generated Dockerfile: a base image pinned by
digest (the same Python as the runtime), the standard library only, and each script skill's
`scripts/` and `references/` at `/skills/<name>/`. No pip packages. Its image ID is recorded in the
deploy manifest (§9.5) and the M13 package. `build` today runs one `docker build` and moves runtime
files into a single context (`build.py:405-418`); the sandbox is a second, separate build of its own
context, not a stage of the agent's Dockerfile.

### 9.4 What the model calls

Not ADK's free-form `run_skill_script(file_path, args)`. The runtime declares one tool per script,
named `skill_<skill with - as _>__<script>` (letters, digits and `_` only, ≤ 64 characters, valid
for every provider's function names), with the manifest's description and parameters, as
`tools.py` declares registry tools. `build` refuses a name that clashes with a registry tool, MCP
tool, built-in tool or sub-agent tool (`SKILL_NAME_CLASH`). The reply's `tools` and the evals'
`tools_include` use this same name; `registry show` and the console show it beside
`skill/<name>:<script>`.

The script's stdout goes back to the model as the tool result, treated as untrusted like any tool
result. Scripts never receive the customer id, session, database or any `ToolRuntime` field: they
are pure functions of their arguments and their skill's files. Data access belongs to tools.

### 9.5 Deploy contract (outline; the K6 addendum fixes the details)

- The v2 deploy manifest gains optional `skill_sandbox: {image: "sha256:<id>"}`; `Manifest` validates
  and preserves it; `docs/manifest.md` and `docs/manifest.schema.json` gain it; the revision digest
  covers it.
- `deploy` mints the runner token, starts the runner, and records the runner's process in the
  deployment state (as for the egress host). `status` reports the runner's health; a dead runner
  makes script tools return `SKILL_SANDBOX_UNAVAILABLE` to the model and is drift for `reconcile`.
  `remove` stops the runner and removes the sandbox image only if no other deployment uses it.
- The gate sees the presence of a script sandbox and the deploy audit event records every skill's
  name, version, hash and whether it is code. No Cedar attribute is added in v1, so a policy can deny
  "any script skill" only through the manifest field, not a named skill (D12).
- The egress runtime boundary mode already refuses every built agent (`MODEL_ACCESS_UNSUPPORTED`,
  `deployment.py:562-566`: an agent on the boundary's internal network has no route to the gateway or
  host services), so script skills under enforcement are refused by the same check.
- **What the runner trusts.** `skill_sandbox` carries the image ID **and** each script's skill,
  version, tool name and parameter schema (or their SHA-256, with the schemas in the image), so the
  runner's "declared for this deployment" list comes from the gate-approved, digest-covered manifest,
  never from the agent's request.
- **Image permissions.** The sandbox image's `/skills` files are world-readable (and directories
  world-searchable), since runs use uid 65534 on a read-only root file system.
- **Stopping a run.** Killing the `docker run` client does not stop the container, so the runner names
  each container (`agentbreeder-skill-<deployment>-<run>`), `docker kill`s it by name at the time
  limit, and on start removes any leftover container with its deployment's prefix.

### 9.6 Kubernetes

`deployment.py`'s `_kubernetes_requirements` (the single place where the cluster target refuses what
it cannot do yet) refuses a manifest with `skill_sandbox` (`SKILL_SCRIPTS_LOCAL_ONLY`), so every
path to a cluster (`deploy --cluster`, `promote`, `agentbreeder up` and the console, which call the
same engine) is covered; a test drives each entry point. A cluster design is a later issue.

## 10. Evals and VERIFIED

- New rule checks in `evals.py`: `skills_loaded` / `skills_not_loaded` (names, from the reply's
  `skills`). Script runs appear in `tools` under their tool names (§9.4).
- `agentspec.VERIFIED` gains `("skills",)` (and with tools/knowledge) only after the kind (a)
  acceptance run, and `("skill-scripts",)` only after the kind (b) run, each with the providers it ran
  on (`SKILL_PROVIDERS`, like `TOOL_PROVIDERS`). The console enables only verified combinations.
- `release` needs nothing beyond the new rules.

## 11. Traces and cost

- `load_skill` and `load_skill_resource` are `TOOL` spans with `agentbreeder.skill.name`, `.version`,
  `.sha256` and the resource name. `AgentPlugin.after_tool_callback` replaces the span's
  `output.value` with `{"chars": <n>}` for these tools, and a runtime test with an in-memory exporter
  asserts no span attribute holds the body (ADK's own tool span included).
- A script run is a `TOOL` span with the same attributes plus `agentbreeder.skill.script`,
  `exit_code` and `duration_ms`; its stdout is the span output, capped like tool output.
- Cost is unchanged: loaded content adds input tokens, which the gateway prices.

## 12. Console (lane B)

- **Registry browser:** `skill/` rows with the code column. The detail renders `SKILL.md` with an
  allow-list Markdown renderer (for example markdown-it with `html: false`): no raw HTML, no images,
  link schemes limited to `https:` and `mailto:`, bidi and zero-width characters stripped, links
  opened with `rel="noopener noreferrer"`. A test renders an XSS corpus.
- **Builder:** a "Skills" picker (agentspec v2, shared with #1058), enabled only for verified
  combinations; the import confirm of §5.4.
- **Agent detail:** the pinned skills; the Traces tab shows loads and runs per turn.
- The visual and UX lenses (`frontend-design`, `ui-ux-pro-max`) run on this section before lane B
  plans K5 (§18).

## 13. Security

### 13.1 Skill text is prompt input

A skill's body and references steer the model as the prompt does, and its L1 description is in the
system instruction of **every** turn, so it is the stronger injection position: it acts without the
model choosing to load anything. Skill text gets the prompt's controls: hash-locked versions,
`registry show`/`diff` before pinning, source labels for remote versions, the L1 descriptions shown
at import, and a hash check of the placed files at start (§8.1). What a skill **cannot** do: add
tools, read session state or secrets, change limits or models, or reach the network (§5.3, §8.1).
What it **can** do is what a malicious prompt can do, including steering the model to call the
agent's own tools, write tools among them; the docs say so.

### 13.2 Injection through loading

Customer text can influence which skill the model loads. That only selects among pinned, reviewed
text; skills are build-time artifacts and cannot contain customer text.

### 13.3 Scripts are code

Kind (b) scripts are arbitrary Python from the skill's publisher. The per-run container (§9.2) is
the boundary: no network interface, no secrets, no agent data, nothing shared between runs, resource
limits. The residual risks, stated in the docs:

- a container escape through a kernel or Docker bug (the same class as any container);
- crafted output that steers the model (an untrusted tool result);
- resource exhaustion on the host, bounded by the per-run limits, per-turn and concurrency caps;
- the runner's port: on Linux any local process (and other containers via the host gateway) can
  reach it, and the token is the only control there. It runs only declared scripts of that
  deployment with schema-checked arguments, so a stolen token gains script runs, nothing else.

The claims "no network" and "nothing shared" are checked by the acceptance run on a real
deployment (§15), on Docker Desktop and, before any claim beyond it, on Linux.

### 13.4 Rendering

Skill text is untrusted for display: `registry.clean` in the terminal and the allow-list renderer in
the browser (§12).

## 14. Runtime versions

Each runtime slice takes the next free `runtime/adk` version when it merges (#1057); the
direct-providers spec already assigns the next one to its slice. This spec names no number. Kind (a)
adds the `skills` capability; kind (b) adds `skill_scripts`. Recommendation (D11 below): one runtime
version carries both sub-agents (#1058) and skills kind (a); kind (b) comes in a later version with
its deploy addendum.

## 15. Acceptance

**Kind (a)**, a recorded real run (verification record plus the console's Playwright video):

1. An agent with two instruction packs and a short prompt, built and deployed locally on a verified
   provider.
2. A question that needs one skill: the reply's `skills` and the trace show that skill loaded and
   the other not, and the answer follows the skill (an eval case with `skills_loaded`,
   `skills_not_loaded`, and a `must_match` on a phrase only that skill contains).
3. A question that needs neither: no load.
4. A skill whose front matter has `metadata.adk_additional_tools` is refused at publish, and when
   placed by hand, at build; an image whose placed skill was edited after build fails start.
5. Northwind (no skills) still passes 61/61 on the new runtime, with byte-identical
   `agent.config.json` and manifest apart from the runtime ref.

**Kind (b)**, a later recorded run on a real deployment:

6. An agent computes an answer with a script; the trace shows the run and its exit code.
7. Scripts that try to open a socket, resolve a name, read `/run/secrets` or the agent's files,
   leave a file for a later run, and run for 60 s all fail (no interface, no resolver, no file,
   nothing persisted, killed at 10 s), recorded on Docker Desktop.
8. The cluster paths refuse the manifest (`SKILL_SCRIPTS_LOCAL_ONLY`).

## 16. Owner decisions (open)

| # | Decision | Recommendation |
|---|---|---|
| D1 | Which kinds | **Both, (a) first** (owner, 2026-11-15). Recorded for completeness. |
| D2 | One `skill` kind or two | **One kind**, code decided by the files, fail closed (§5.2). |
| D3 | ADK `SkillToolset` or our own loader | **ADK's**, with our own `Skill` objects, refusals and caps (§8.1). |
| D4 | Catalog disclosure | **Eager** (names in the system instruction), given ≤ 10 skills. |
| D5 | `unload_skill` / lifecycle | **Off in v1.** |
| D6 | Where scripts run | **One fresh `--network none` container per run, from a host-side runner** (§9.1). |
| D7 | Script languages and dependencies | **Python, standard library only.** |
| D8 | Agent Skills `assets/` | **Refused in v1.** |
| D9 | Per-turn caps | **3 loads, 3 resource loads, 5 script runs.** |
| D10 | Skills on sub-agents (#1058) | **Later**: `subagents[].skills` as a subset of the file's skills. |
| D11 | Runtime versions | **One version for sub-agents and skills (a)**; skills (b) later. Also rename A2A's `a2a.skills` to `a2a.card_skills`. |
| D12 | Gate visibility of script skills | **Presence only in v1** (manifest field, audit detail); a Cedar attribute later if asked. |

## 17. Slices and cross-repo

| Slice | Contents | Lane |
|---|---|---|
| K1 | `skill` kind in `registry.py` (§5.3, §5.4, `runs_code(kind, path)` and its callers), `docs/registry.md` | C (`stackfor.py`/`agentspec.py` caller edits with the coordinator) |
| K2 | `agentfile.py` `skills:`, build placement without `scripts/`, config, `docs/agent-yaml.md` | A |
| K3 | Runtime: hash check, `Skill` construction, `SkillToolset`, plugin caps and path rule, events, spans, reply | A |
| K4 | `evals.py` rules; acceptance (a); `VERIFIED` | A, V |
| K5 | Console: registry browser, builder, agent detail | B |
| K6 | Kind (b): deploy-contract addendum (reviewed), then sandbox image, runner, manifest field, lifecycle, cluster refusal, script tools; acceptance (b) | A |

K2 and the sub-agents spec's S1 edit the same files; whichever is second rebases, or they land as
one slice under D11.

Plan deltas: the A2A spec renames `a2a.skills` to `a2a.card_skills` and takes the next free runtime
version (a status note is added to it in this PR); M9b's remote kinds list gains `skill`, with the
code decision made from fetched files.

- **JSON Schema:** `docs/manifest.schema.json` gains `skill_sandbox` in K6 only. There is no
  `agent.yaml` JSON Schema today.
- **Cloud:** a `CLAUDE.md` note only (no code reads `agent.yaml`, the registry or the manifest).
- **Website:** no change until release R (#836).

## 18. Review log

| Round | Reviewer | Result |
|---|---|---|
| r1 | `/architect` (Sonnet, read-only) | CHANGES REQUESTED. Folded in: dotted script tool names are invalid function names (§9.4); the sandbox deploy plumbing was not designed against the single-image build and manifest (second build §9.3, contract outline §9.5, K6 gated on an addendum); runtime numbering collides with direct-providers (§14); everything `KINDS` touches and every `runs_code` caller listed (§5.2, §5.4); scripts excluded from the agent image (§7); `run_skill_script` construction, ADK activation state and where caps live (§8.1–§8.3); experimental ADK surface and the pin (§8.1); A2A rename recorded in that spec; Northwind 61/61 and unchanged output in acceptance (§15). |
| r1 | `/security` (Sonnet, read-only) | CHANGES REQUESTED. Folded in: a shared sandbox could call the agent's unauthenticated `/invoke` and leak data between customers (replaced by a per-run `--network none` container from a host-side runner, §9.1, §9.2); `runs_code` failed open (fail-closed by files, §5.2); front matter parsed strictly and L1 descriptions treated as always-on injection surface (§5.3, §5.4, §13.1); script source exposure via `load_skill_resource` (§8.1); runner token handling and Linux reachability stated (§9.2, §13.3); span content (§11); write tools in the worst case (§13.1); start-time hash check (§8.1); Markdown sanitiser named (§12); cluster refusal at the single engine check (§9.6); gate visibility and audit (§9.5, D12); base image by digest (§9.3). |
| r2 | `/architect` + `/security` (Sonnet, read-only) | CHANGES REQUESTED. Folded in: the real `runs_code` callers (`center/builder.py`, `center/registryimport.py`, `commands/registry.py`; not `center/onboard.py`) and the import cross-check deciding from files (§5.2); skill names follow ADK's kebab rule (§5.3); enforcement mode already refuses built agents (§9.5); the runner's declared-scripts source and stopping a run by container name (§9.5); ADK's activation key name (§8.2). |
| r3 | `/architect` + `/security` (Sonnet, read-only) | **APPROVED** (no blocker or major). Minor folded in: sandbox image file permissions for uid 65534 (§9.5). |
| — | `frontend-design`, `ui-ux-pro-max` | For §12, run by lane B before the K5 plan. |
