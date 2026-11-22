> **Focused rebuild branch (2026-09-15):** the legacy implementation below is historical context. Active source is `src/agentbreeder/`; manifest validation, doctor and the local deploy/status/remove lifecycle are implemented; the real local Docker lifecycle and fresh-install sample demo have passed on macOS. Read `ARCHITECTURE.md` and `docs/manifest.md` for the current contract. Do not restore removed product surfaces or claim production deployment/governance is shipped. Existing privacy and delivery/review rules remain in force. Engineering tracking: #648.

# CLAUDE.md — AgentBreeder

> **This file is a thin router.** Only always-on invariants + standing
> instructions live here; the reference bodies (project structure, agent.yaml
> spec, architecture, coding standards, MCP servers, API conventions, etc.)
> were moved to `docs/` to cut the per-turn token cost (~12.2k → ~2.5k).
> **Read the linked doc BEFORE you touch its area** — the pointers are not
> optional; a rule you skip is still binding.

## 🧠 What is AgentBreeder?

AgentBreeder is an **open-source preview** that deploys an agent container you already built,
and only after a **deploy-time identity gate** confirms the agent in your identity provider.

**Tagline (site):** Know the agent. Gate the deploy. Audit every attempt.

**What ships today (`3.0.0a3` preview on PyPI, `3.0.0-alpha.3` on npm; a bare `pip install agentbreeder` still gets 2.7.3; verified end to end on macOS + Docker Desktop). From the public registries on macOS, `quickstart` ran on pip/uvx/npx (a1, a2 and a3), Northwind 61/61 from pip (a1 and a3), `up` with Northwind 61/61 on local Docker and `kind` through npx (a2), and `up` starting through npx (a3); on Linux only install, `--version` and `doctor` ran (a1 and a3):**
- `validate` and `doctor`; a local Docker `deploy`/`status`/`remove` lifecycle with SQLite history
- The identity gate (Entra, Keycloak, Okta (experimental), AWS, static file; only the static
  adapter is live-verified), Cedar/OPA/AuthZEN policy, second-person approval, a kill switch,
  `reconcile` drift checks and a hash-chained audit log
- An opt-in MCP egress sidecar that applies policy to MCP Streamable HTTP tool calls only
- Preview: building ADK agents from `agent.yaml` with versioned, hash-locked registries
  (`agentbreeder build`), local secret files, per-deployment LiteLLM keys, and an eval-gated
  `agentbreeder release`. Verified only with `src/agentbreeder/examples/northwind`, which passed 61/61.
- Preview, in `3.0.0a2` (#783): `agentbreeder up`, a loopback single-user
  command center that runs CLI child processes, and a Kubernetes target for local `kind` clusters
  (`cluster`, `promote`, `deploy --cluster`, `forward`). Northwind 61/61 on both targets, macOS,
  from a checkout 2026-11-10 and from `npx agentbreeder@3.0.0-alpha.2 up` 2026-11-11 (driven
  through its API by a script, no browser). No production cluster has been tested; see
  `docs/command-center.md`.
- Local-only preview: backing services from `stack.yaml` (`agentbreeder services up|status|logs|
  env|down`, built-in postgres-pgvector, litellm and phoenix, project services built from source;
  see `docs/services.md`). Verified with `src/agentbreeder/examples/northwind` from scratch (61/61, 2026-11-08).

**On `main`, not in a published preview (merged since `3.0.0a3`; coming in 3.0.0a4, not yet
released).** macOS x86_64 + Docker Desktop only; see `docs/runtimes.md` and the verification
records it links:
- Runtimes on the runtime kit (#1083): `runtime/langgraph@1.0.0` (verified for the starter and one
  tool with Gemini), `openai-agents@1.0.0`, `deepagents@1.0.0`, `crewai@1.0.0` (each Northwind
  61/61 on Docker and `kind`, not verified); `claude-agent-sdk@1.0.0` in the example's own
  registry, not shipped; `runtime/adk@1.5.0` (sub-agents, skills), `1.4.1`, `1.5.1`; agent
  protocol v1 and `agentbreeder conformance` (26 of 39 checks built, no runtime conformant).
- Gateway `service/litellm@1.3.0` (request filter), `@1.4.0` (Messages route, stub providers
  only), `@1.5.0` (Vertex, config-only); git registry sources and `agent.lock` v2 (loopback git
  only); console sign-in and notifications; design system v2.
- Kubernetes K0 to K3 (provider interface, `token` provider that refuses after its checks,
  `cluster up --restricted` with seven policies, `services render --cluster`): K1's checks and K2
  ran on local `kind` (Kubernetes 1.37); K0 and K3 unit-tested only; no production cluster.

**Not shipped:** cloud/production deploy targets, a multi-user control center or versioned API, authenticated
invocation, a final 3.0.0. 2.x (PyPI ≤ 2.7.3) is unmaintained and has known functional and
security issues; it is summarised on the website's Legacy 2.x page.

**Claim rule:** README, docs and the website claim only what has been run end to end. Never
claim tamper-evidence (the audit chain detects corruption; a DB owner can recompute it), and
never claim runtime enforcement beyond MCP Streamable HTTP via the opt-in sidecar.

---

## 🚫 Common Mistakes to Avoid

1. **Never skip RBAC validation** — every deploy MUST check permissions, even in tests (mock it, don't skip it)
2. **Never write to the registry directly** — always use registry service classes
3. **Never hard-code cloud provider names** — use the deployer abstraction
4. **Never put framework-specific logic in `engine/builder.py`** — it belongs in `engine/runtimes/`
5. **Never commit secrets or credentials** — use `.env` and Secrets Manager references
6. **Never use synchronous I/O in async FastAPI handlers** — always `await` or use `run_in_executor`
7. **Never break the `agentbreeder deploy` happy path** — it is the product; protect it like an API contract
8. **Never merge without tests** — CI blocks PRs with < 80% coverage on changed files
9. **Never commit marketing/strategy content** — this is a public OSS repo. Positioning docs, growth/GTM plans, competitive strategy, website-audit artifacts, and similar internal material go in `docs/superpowers/private/` (gitignored) or outside the repo — never into tracked paths like `docs/superpowers/specs/`. Engineering specs/plans remain tracked as usual.

## 🔐 Known security exceptions (read by `/security` and `/gate`; anything not listed blocks)

- **Known exception: `chromadb==1.5.9` in `runtime/crewai@1.0.0`'s lock only**, IDs GHSA-f4j7-r4q5-qw2c, GHSA-36p7-vc44-83pf, GHSA-xph7-9rjv-w5fr, GHSA-2wm9-hf6c-p5cr
  (pip-audit aliases, in order: PYSEC-2026-311/CVE-2026-45829, PYSEC-2026-3814/CVE-2026-45833, PYSEC-2026-3815/CVE-2026-45831, PYSEC-2026-3813/CVE-2026-45830)
  (`src/agentbreeder/builtin_registry/runtime/crewai/1.0.0/runtime/requirements.lock`). Reason: the advisories (GitHub: 2 critical, 2 high) are in ChromaDB's HTTP server, which the runtime never starts; it imports `chromadb` only as a library.
  No fixed version exists; re-check when chromadb ships a fix. Owner decision 2026-11-20 (#1090). Guarded by `tests/test_crewai_chromadb_exception.py`
  and the runtime test `test_chromadb_is_a_library_only_no_server_client_or_port`. Any other ID, package, version or lock is not covered.

---

## 🚦 Standard Delivery Workflow (Definition of Done)

**Every enhancement or fix follows this pipeline end-to-end.** Each step gates the next — don't skip. This is the standard process across the AgentBreeder repos and must stay identical in both `CLAUDE.md` files.

1. **Track it** — create a GitHub **epic + sub-issues** for the change (one epic per enhancement/fix; sub-issues per milestone/slice). No substantial work without an issue.
2. **Spec it** — write a spec *before* code (problem, goal, scope, acceptance criteria, cross-repo split). Store under `docs/superpowers/specs/`. Use the `brainstorming` → `spec` skills.
3. **Design-review the spec** through the required lenses, folding findings back in: **`/architect`** (architecture), **`frontend-design`** (visual/UX), **`ui-ux-pro-max`** (UI/UX Pro Max), and **`/security`**. For a user-facing/marketing surface also run **`marketing-ideas` / `seo-audit` / `ai-seo`**.
4. **Plan it** — write the implementation details (tasks, sequencing, cross-repo split) under `docs/superpowers/plans/`.
5. **Self-review the plan/implementation on a different Claude model** — run the 100xprism review loop (`/pair-loop`, reviewer `claude` on a model other than the coder's, e.g. coder Opus → reviewer Fable or Sonnet), apply valid findings, **re-review in a loop until it converges** (see *AI Harnesses & Code Review* below).
6. **Gate it** — after implementation run the **`/launch` quality gate** (tests ≥ threshold, security 0 critical/high, build, Docker, cloud-security). Enforced by the pre-commit gate hook — a commit/push is blocked until all gates pass for the exact tree.
   - **Exception — internal docs/inert-tooling changes only** (`CLAUDE.md`/`AGENTS.md`/plan/spec markdown, `.gitignore`): skip the full multi-gate suite and instead get a different-model self-review pass over the uncommitted diff, then run `python3 ~/100xprism/hooks/gate-pass.py` to unblock the commit hook. **Never extend this exception to CI workflows (`.github/workflows/*.yml`), `Dockerfile`, `docker-compose.yml`, or anything else that controls build/test/deploy behavior** — those go through the full gate like application code, since a broken pipeline config is exactly what the gate exists to catch.
   - **`.gitignore` specifically:** a bad ignore rule can hide secrets or drop tracked source, so treat any `.gitignore` edit as security-sensitive even under this exception — the self-review must explicitly verify (a) no previously-committed pattern that protected a real secret was weakened, and (b) no newly-untracked file exposes one. A generic "looks fine" pass is not enough; ask the reviewer (or the security workflow) to check both directions before recording the gate pass.
7. **Branch + PR** — conventional-named feature branch; open a PR (stack PRs when milestones build on each other).
8. **Merge gate** — merge **only when CI is green, the different-model self-review has approved, and the owner has given an approving review.** All three are required.
9. **Auto-merge** — once (8) holds, enable **auto-merge** (squash) so it lands as soon as required checks pass.

For changes spanning OSS/Cloud/Website, run this pipeline per the **Cross-Repo Sync Policy** below and keep issues, terminology, and PRs aligned across repos.

## 🤖 AI Harnesses & Code Review (Claude + Codex harnesses; different-model self-review)

This repo is worked on by **two AI coding harnesses**, and both guidance files must stay in sync:
- **Claude Code** reads `CLAUDE.md` (this file).
- **Codex** reads `AGENTS.md` — a **100xprism-generated skills/command catalog** (source: `.agents/skills/*/SKILL.md`), **not** a copy of these rules. Never hand-edit it; refresh it with **`100xprism update`**. Repo rules live in `CLAUDE.md` (the source of truth for **both** harnesses) — Codex reads `CLAUDE.md` directly, so rules added here reach it. Note: `/update-claude` edits *`CLAUDE.md`*; it does **not** regenerate `AGENTS.md`.

**Code review is a self-review on a different Claude model** (replaced the Codex review on 2026-11-09). The coder and the reviewer must run different Claude models, so the review isn't the same model grading its own work:

- **Preferred — 100xprism `/pair-loop`**, configured in `~/.100xprism/config.json`: `"pair_loop": {"coder": "claude", "reviewer": "claude", "reviewer_model": "claude-fable-5-1"}`. The reviewer runs read-only (`claude -p --permission-mode plan --model <reviewer_model>`), loops via `HANDOFF.md`, and refuses a same-model `APPROVED`. `pair-loop start` needs a clean tree, so commit the work to the feature branch first.
- **Ad hoc (uncommitted work, or a second opinion)** — spawn read-only reviewer subagents with a model override (e.g. `model: fable` or `sonnet`, different from the session's model). Give them the code and the spec, not your conclusions; split by area (e.g. security boundary vs. integration) when the diff is large.

Recommended flow: **review, apply valid findings with tests, re-review, and loop until it converges** (no substantive findings left). Verify each finding yourself before fixing it; record what was found and fixed (e.g. in the spec's review log). The `codex` CLI may still be used as an *extra* opinion, but it is no longer required.

---

## 🔄 Cross-Repo Sync (Standing Instruction)

AgentBreeder ships across **three repositories** that must stay in sync at all times:

| Repo | Path | Role |
|------|------|------|
| `agentbreeder` (this repo) | `/Users/rajit/personal-github/agentbreeder` | OSS CLI + engine + connectors — source of truth |
| `agentbreeder-cloud` | `/Users/rajit/personal-github/agentbreeder-cloud` | SaaS managed platform — deploys on top of agentbreeder packages |
| `website` | `website/` subdirectory | agentbreeder.io marketing + docs site |

**Rules — before closing any PR:**
- **Schema change** (`agent.yaml`, API shape, CLI flags): grep `agentbreeder-cloud` for affected fields and update them
- **New connector or feature**: check if `agentbreeder-cloud` needs to expose it
- **Version bump**: update `website/components/footer.tsx` version badge, update features/docs pages
- **Breaking change**: `agentbreeder-cloud` must get a companion PR before or alongside
- **Update the guides in BOTH repos**: any feature that changes cross-repo behaviour MUST update `CLAUDE.md` in **both** `agentbreeder` and `agentbreeder-cloud`, spelling out *what changes on each side* (see "Documenting a cross-repo feature" below). `AGENTS.md` is a 100xprism-generated skills catalog (`# Source of truth: modules/<slug>/SKILL.md`) — **do not hand-edit it**; refresh it with `100xprism update`. It does **not** carry these rules; put them in `CLAUDE.md`, which both harnesses use (Codex reads `CLAUDE.md` directly). `/update-claude` edits `CLAUDE.md`, not `AGENTS.md`.

**Cross-repo detail** (current state, the change-sync table and the
`brand.css` design-system source of truth #583, now at `website/brand.css`) lives in
`docs/cross-repo-sync.md` — read it before any schema/API/branding change.
Since 2026-11-07 the website documents the focused rebuild (`website/content/docs/`); 2.x
is unmaintained and summarised on one page, `website/content/docs/legacy-2x.mdx` (2.x docs
live at the `v2.7.3` tag). Claim only what has been run end to end; see
`website/components/what-ships.tsx`.

**3.0.0a1 release (2026-11-10, #755), per side.** OSS: published to PyPI, npm and GitHub; a new
version also updates the footer badge (`website/components/footer.tsx`) and the pins in the install
docs. Website: `website/content/docs/install.mdx` holds the install commands and the verified
scope. Cloud: no code change, because it has no OSS version pin and installs a local editable
checkout; its `CLAUDE.md` now records the release.

**3.0.0a2 release (2026-11-11, #802), per side.** OSS: PyPI, npm (published by hand, no npm provenance
yet) and GitHub; footer, install pins and `up` claims updated after the public-registry run. Website:
`install.mdx` carries separate a2 and a1 verification tables. Cloud: no code change; its `CLAUDE.md`
records the version.

**3.0.0a3 release (#836, milestone R of #812), per side.** OSS: PyPI `3.0.0a3`, npm `3.0.0-alpha.3`,
cut from `main` with `runtime/adk@1.4.0` (owner decision). The prep PR changes only the version, the
CHANGELOG (fragments folded) and the pins frozen into the artifacts (`README.md`, `npm/README.md`,
the Northwind README); the footer badge, `install.mdx` and `what-ships` change in a follow-up PR
after the public-registry run, and claim only what that run covered. Website: an a3 verification
table in `install.mdx`. Cloud: no code change (no OSS version pin); its `CLAUDE.md` records the
version.

**Brand tokens (#817), per side.** OSS: `website/brand.css` gains a light `:root` palette and new
tokens (`--text-muted`, `--control-border`, status colours), copied to `center/static/brand.css`
and checked by `tests/test_brand_tokens.py`. Website: no visual change (forced dark). Cloud:
re-vendors `tokens.css`; `tokens:check` now also compares `:root`.

**`agentbreeder up` (#783), per side.** OSS: `center/`, `kubernetes.py`, `cluster.py`, `promote.py`; the
operator config gains `Environment.kubernetes`. Website: `what-ships` and `command-center.mdx`. Cloud: no
code; it has no OSS version pin, and the center's API is private and unversioned, not the #775 contract.

**CLI M3 and M8 (#819, #824), per side.** OSS (on `main`, not in a published preview): `migrate`,
`logs --failed`, `registry show|diff|used-by`, launcher-aware `Next:` lines, `redaction.py`,
`evidence.py`, `registry_deployed.py`. Website: no change until the release milestone (R). Cloud: a
`CLAUDE.md` note only; it has no OSS version pin and there is no `agent.yaml`, JSON Schema or
`agent.lock` format change.

**Guardrails floor, #1024 G1a, per side.** OSS (on `main`): the operator environment gains
`guardrails: {required, approved_sha256}`, and a required floor denies every deploy
(`GUARDRAILS_UNVERIFIED`) until G1b. Provider key names are reserved under it, and the Cedar `Agent`
gains a `guardrails` set, so `SCHEMA_VERSION` and every revision digest change once. The manifest,
JSON Schema and `agent.yaml` are unchanged. Website: no change until release R. Cloud: a `CLAUDE.md`
note only. It has no code reference to the operator config, Cedar or revision digests.

**Guardrails, #1024 G1b, per side.** OSS (on `main`): built-ins `service/presidio-analyzer@1.0.0`,
`service/presidio-anonymizer@1.0.0` and `service/litellm@1.1.0`; the gateway config review accepts
a Presidio `guardrails:` list; `stack.yaml` `environment.guardrails` and the operator floor's
`running_sha256` and `effective` (written by `services up`); the gate checks the hash
(`GUARDRAILS_CHANGED`); keys under a required floor get `allowed_routes`. No manifest, JSON Schema or
`SCHEMA_VERSION` change. Website: no change until release R. Cloud: a `CLAUDE.md` note only.

**Ollama, #1025 O1, per side.** OSS (on `main`): built-in `service/litellm@1.2.0` (Ollama at
exactly `http://host.docker.internal:11434`, `reaches_host: [11434]`, an env allow-list launcher),
the `service.yaml` field `reaches_host` (built-in gateway only), `new agent --model ollama`
(verified on macOS + Docker Desktop only, on `runtime/adk@1.3.0`; Linux and 1.4.0 not run) and the `cost` label `no price (local
model)` (`local` in `--json`). No manifest, JSON Schema or `agent.lock` change. Website: no
change until release R. Cloud: a `CLAUDE.md` note only.

**Gateway request filter, #1105, per side.** OSS (on `main`): built-in `service/litellm@1.3.0`
(1.2.0 plus a request filter loaded by the launcher) refuses a listed set of provider-routing,
endpoint and credential parameters at a request's top level, in `extra_body` and in fallbacks
(`GATEWAY_PARAM_REFUSED`), and other call types (`GATEWAY_ROUTE_REFUSED`); every deployment and
eval key now gets `allowed_routes` (chat and embeddings) on any gateway, not only under a
guardrails floor. 1.0.0 to 1.2.0 unchanged (they still honour `custom_llm_provider`); starters and
Northwind not relocked. No manifest, JSON Schema or `agent.lock` format change. Website: no change
until release R. Cloud: a `CLAUDE.md` note only.

**Gateway Messages route, #1089, per side.** OSS (on `main`; unit-tested, and the opt-in Docker test
passed on 2026-11-20 against the real LiteLLM 1.106.2 image with stub providers, PR #1187):
built-in `service/litellm@1.4.0` (1.3.0's image, launcher and `service.yaml`, a new filter)
also serves `/v1/messages` (`anthropic_messages`), with 1.3.0's refusals plus
`mcp_servers`, `container`, `service_tier`, `speed`, `inference_geo`, betas, `compaction`,
`safeguards`, typed tools, provider headers other than `anthropic-version`, and Files API or URL
content (`GATEWAY_PARAM_REFUSED`); chat and embeddings are filtered as on 1.3.0.
`gateway_keys.key_routes(..., model_api)` adds `/v1/messages` for `anthropic-messages`; nothing on
`main` passes it yet (F7, #1177, does). `/v1/messages/count_tokens` is reachable with such a key
and unfiltered (LiteLLM prefix-matches routes). 1.0.0 to 1.3.0, starters and Northwind unchanged.
No manifest, JSON Schema or `agent.lock` change. Website: no change until release R. Cloud: a
`CLAUDE.md` note only.

**Vertex AI, #1025 V1, per side.** OSS (on `main`): built-in `service/litellm@1.5.0` (1.4.0 plus
the gateway-only import `VERTEX_SERVICE_ACCOUNT_JSON`; the launcher accepts only a
`service_account` key, re-serialises it and binds `vertex_project` to the key's project),
`VERTEX_KEY_INVALID` at `services up`, and `new agent --model vertex --vertex-project
--vertex-location` (plain starter only; not `--stack-for`, `--from-spec` or the console).
**Config-only: not run against Vertex AI, `verified` false.** No manifest, JSON Schema or
`agent.lock` format change; 1.0.0 to 1.4.0 unchanged. Website: no change until a recorded run.
Cloud: a `CLAUDE.md` note only.

**Agent guardrails, #1053, per side.** OSS (on `main`): `agent.yaml` and the manifest gain an
optional `guardrails` list (JSON Schema updated; digests, revisions and package bytes are unchanged
when it is unset), `services up` writes `guardrails.optional`, and deploy attaches a LiteLLM policy
to the key alias. Website: no change until release R. Cloud: a `CLAUDE.md` note only; it has no
code reference to the manifest's `guardrails` or the operator config.

**Sub-agents, #1058 AY (epic #1057), per side.** OSS (on `main`): `agent.yaml` gains optional
`subagents:` (≤ 5, depth 1) and `limits:`, and `evals` gains `tag_threshold`; `build` refuses them
until a runtime lists the `subagents` capability (`RUNTIME_NO_SUBAGENTS`). The deploy manifest,
its JSON Schema and `SCHEMA_VERSION` are unchanged; a sub-agent only adds its alias to
`model_access.aliases`. Eval cases gain sub-agent and skill rules and `tags`. Website: no change
until release R. Cloud: a `CLAUDE.md` note only; it has no code that reads `agent.yaml`.

**Design system v2 tokens, #1119 T (epic #1112), per side.** Merges with the website switch (W
#1115), not before (DS9 as amended), because the console's palette changes with it. OSS: `website/brand.css` gets the v2
palette and tokens (`--brand`, glass, `--elev-*`, tints, syntax), the `.glass` utilities in
`@layer utilities` and `@theme` mappings (`--shadow-*` → `--elev-*`, `--blur-glass`, a Geist
`--font-display`, a `clamp` `--text-display`); the console copies the blocks (its palette changes)
and the glass rules (`style.css`). Website: T alone changes nothing visible (`globals.css` pins the
old shadows until W removes the pin). Cloud: C (#1117) re-vendors `tokens.css` from the T PR's head
commit and adds a CI pin at a commit SHA; C blocks the switch, and the pin moves to the `main`
commit when T merges (see `docs/cross-repo-sync.md`).

**Design system v2 website, #1115 W (epic #1112), per side.** Merges with T, L and C at the switch.
Website: light by default, dark by system or a System/Light/Dark toggle (next-themes, now a direct
dependency at the version fumadocs already used), components on the tokens only
(`tests/test_website_tokens.py`), fumadocs `--color-fd-*` mapped to brand tokens, Bricolage
dropped, a dark OG image and a theme-aware `app/icon.svg`. W does not edit `brand.css`, so the
Cloud pin does not move. Cloud: no change from W; its theme and toggle are C. OSS outside
`website/`: none.

**Runtime manifest keys, #1083 F1 (multi-framework), per side.** OSS (on `main`): the engine
reads framework facts from the runtime version (`runtime_profile.py`: manifest keys `protocol`,
`profile`, `capabilities`, `limits`, `reserved_tool_names`, and a generated
`runtime/modules.json`); a manifest without `protocol` gets `LEGACY_ADK`, so `runtime/adk` builds
are byte-identical (`tests/test_runtime_golden.py`). New: `registry runtimes --json`, `new agent
--runtime`, and an optional `runtime: {ref, profile}` in the deploy manifest and its JSON Schema
(written only for `profile: kit`, which no runtime has; `SCHEMA_VERSION` and existing digests
unchanged). `agent.yaml` allows up to 5 `knowledge:` entries; `build` refuses more than the
runtime takes (`RUNTIME_LIMIT`). No runtime other than ADK exists. Website: no change until a
framework passes its acceptance. Cloud: a `CLAUDE.md` note only; it has no code that reads the
manifest's `runtime` or a runtime manifest.

**Agent packages, #763 M13 (slices 1 to 4a), per side.** OSS (on `main`, not in a published
preview): the `agent` registry kind, `release` writing an immutable `agent/<name>@X.Y.Z` package
(strict `package.json`, `package_version` 1, unknown keys refused), `deploy agent/<name>@X.Y.Z
--local`, store schema v4 (`package` column), and a library-only OCI artifact
(`application/vnd.agentbreeder.agent.v1`, pushed to `127.0.0.1` only; no command uses it yet, and
`--cluster` by ref still refuses). No `agent.yaml`, deploy manifest, JSON Schema or `agent.lock`
format change. Website: no change until release R. Cloud: a `CLAUDE.md` note only (the `agent`
kind and the strict `package.json`); a grep of its tracked files on 2026-11-18 for the package,
artifact and registry codes found nothing.

**Kit egress, #1083 F1b (multi-framework), per side.** OSS (on `main`; unit-tested, the opt-in
real-engine tests are written and not yet run): a manifest with `runtime.profile: kit` deploys
with default-deny egress. Local Docker: internal networks only and a forwarding sidecar
(`egress/kit.py`, `egress/forwarder.py`, the phase-4 boundary's pattern) for ingress and for a
gateway at `http://host.docker.internal`; `KIT_EGRESS_UNSUPPORTED` for what it cannot reach.
`kind`: a NetworkPolicy that allows cluster DNS and the new optional operator key
`kubernetes.agents_cidrs` (written by `cluster up`), else `KUBERNETES_KIT_EGRESS_UNCONFIGURED`.
No runtime has `profile: kit`, so every existing deployment is unchanged (goldens). No manifest,
JSON Schema, digest or `agent.lock` change. Website: no change until a framework passes its
acceptance. Cloud: a `CLAUDE.md` note only; it has no code that reads the operator config or
deploys through these paths.

**Agent protocol and conformance, #1083 F2a (multi-framework), per side.** OSS (on `main`):
`docs/agent-protocol.md` (protocol v1, what `runtime/adk@1.3.0` serves) and `agentbreeder
conformance IMAGE` (`conformance/`: 26 of 39 checks built, reports under
`~/.agentbreeder/conformance/`). Run on Docker Desktop (macOS x86_64): the ADK baseline
(`docs/superpowers/verification/F2a-adk-baseline.md`, 2026-11-19) is `fail` on `runtime/adk`
1.3.0 and 1.5.0 (22 pass, 4 fail, 8 not run, 5 n/a), and the kit runtimes are `incomplete`
(26 pass, 13 not run). A report is `incomplete` at best, so no runtime is called conformant. `registry runtimes --json` gains
`conformance`, and `list --json` and `list --all --json` gain `runtime` per deployment (additive;
`schema_version` stays 1). No manifest, JSON Schema, `agent.yaml` or `agent.lock` change.
Website: no change until a framework passes its acceptance. Cloud: a `CLAUDE.md` note only; it
has no code that reads `list --json`, a conformance report or the agent's `/invoke` reply.

**Remote registry sources, #829 M9b slice 1a, per side.** OSS (on `main`, not in a published
preview): `registry.Root` and labelled roots, a strict `Lock.load` (unknown keys and malformed
hashes are `LOCK_INVALID`), `agent.lock` v2 read/write (a `sources` table, `remote:<name>`
entries, per-entry `pin`; nothing writes it yet, v1 is byte-identical), `--locked` reading only
the locked, currently configured root, `sources/` (canonical URL form, `locate`; no network),
and empty directories refused in registry versions. `stack.lock` stays v1. No `agent.yaml`,
manifest or JSON Schema change. Website: no change until release R. Cloud: a `CLAUDE.md` note
only; a grep of its tracked files on 2026-11-19 found no reader of `agent.lock` or
`stack.lock`. Any future Cloud reader must accept v1 and v2 and refuse unknown keys.

**Remote registry sources, #829 M9b slice 1b (git), per side.** OSS (on `main`, not in a
published preview; tested only against a loopback `git http-backend`): `agent.yaml` gains
optional `registries:` (git only), which makes `build` write `agent.lock` v2; `sources/git.py`
(hardened fetcher), `trust.py` (`~/.agentbreeder/sources.yaml`, `AGENTBREEDER_REGISTRY_TRUST`),
`cache.py`, `remote.py`; `registry trust`; `build --offline`; `build.json` and `package.json`
`inputs.sources` carry the sources table (only with `registries:`); `package.py` and the
console's `center/reads.py` accept lock v2 via `Lock.parse_bytes`. New
`docs/agent-lock.schema.json`. Without `registries:` every output is byte-identical. Website: no
change until release R. Cloud: a `CLAUDE.md` note only; a grep of its tracked files on
2026-11-19 found no reader of `agent.lock`, `stack.lock`, `lock_version`, `package.json` inputs,
`build.json`, `sources.yaml` or the trust variable.

**Runtime kit and LangGraph, #1083 F2b + F3 (multi-framework), per side.** OSS (on `main`; run
end to end on macOS x86_64, verified there for what the fresh-wheel run below covered): `src/agentbreeder/runtime_kit/` (protocol v1 `kit`
profile, the turn, the loopback model proxy with 1.3.0's retries and codes and a step cap that
withholds a tool-calling last answer, `MODEL_STEP_LIMIT`), vendored byte for byte as `runtime/kit/` in
`runtime/langgraph@1.0.0` (capabilities `knowledge`, `notes`), and
`examples/northwind-langgraph` (Northwind's registry and stack; Northwind's `stack.yaml` names
the new agent). No `agent.yaml` field, manifest, JSON Schema, `SCHEMA_VERSION` or `agent.lock`
format change; a kit runtime's deploy manifest carries F1's optional `runtime` field. In
`agentspec.VERIFIED` and `RUNTIMES_RUN` since the fresh-wheel run below. The opt-in real-stack
acceptance ran on 2026-11-19 (61/61 local and on
`kind`, conformance 26/39 with 13 not built, no arm64), and Northwind's `commerce.sql` grants the
agent role SELECT on six `turns` columns for kit history. Website: no change
until LangGraph passes its acceptance. Cloud: a `CLAUDE.md` note only; it has no code that reads
a runtime manifest, the reply's `framework`/`runtime` or the deploy manifest's `runtime`.

**Verified LangGraph and ADK 1.5.0 (fresh-wheel run, owner decision 2026-11-19), per side.** OSS
(on `main`): `RUNTIMES_RUN` gains `runtime/adk@1.5.0` and `runtime/langgraph@1.0.0`; `VERIFIED`
gains `langgraph` (the starter and one tool, `gemini` on its default model, macOS x86_64 only;
`BASE_RUNTIMES`, `RUNTIME_PROVIDERS`, `newagent.STARTER_RUNTIMES`); the console's form starts on
`newagent.RUNTIME` (`STARTER_RUNTIME` in `builder.js`); conformance reports the span checks
`not_run`, not `fail`, for an image built with `traces: none`. Record:
`docs/superpowers/verification/verify-langgraph-adk150.md`. No manifest, JSON Schema or
`agent.lock` change; `registry runtimes --json` changes only its `verified` values. Website: no
change until release R. Cloud: a `CLAUDE.md` note only; it has no code that reads `registry
runtimes` or a `new agent` summary.

**OpenAI Agents SDK, #1083 F4 (multi-framework), per side.** OSS (on `main`; run end to end on
macOS x86_64 2026-11-20, not verified): `runtime/openai-agents@1.0.0` (the SDK's `Agent` over
Chat Completions on the vendored kit 1.0.0, kit-function tools only, every `OPENAI_*` variable
removed before import, the instrumentor's exclusive processor replacing the SDK's OpenAI trace
exporter) and `examples/northwind-openai-agents` (Northwind's registry and stack; `stack.yaml`
names the agent). No `agent.yaml` field, manifest, JSON Schema, `SCHEMA_VERSION` or `agent.lock`
format change. Not in `agentspec.VERIFIED` or `RUNTIMES_RUN`. The opt-in real-stack acceptance
(`tests/test_northwind_openai_agents_docker.py`) ran on 2026-11-20 (61/61 local and on `kind`,
conformance 26/39 with 13 not built, no arm64). Website: no change until it
passes its acceptance. Cloud: a `CLAUDE.md` note only (not made by this lane); it has no code that
reads a runtime manifest or the reply's `framework`/`runtime`.

**CrewAI, #1090 (#1083 F6, multi-framework), per side.** OSS: `runtime/crewai@1.0.0` on kit 1.0.0
(unchanged), run end to end on macOS x86_64 (2026-11-20: Northwind 61/61 local and on `kind`,
conformance 26/39 with 13 not built, no arm64), not verified: `Agent.kickoff_async` per
turn (no `Crew`), CrewAI's task-output SQLite store and console output switched off and checked at
start-up, CrewAI's retry loop off per turn, tool schemas as generated pydantic models, one LLM
span per model call written by the adapter;
`examples/northwind-crewai` (Northwind's `stack.yaml` names the new agent; `example.PORTS`); CI job
`runtime-crewai`; opt-in `tests/test_northwind_crewai_docker.py`. Not in
`agentspec.VERIFIED` or `RUNTIMES_RUN`. No `agent.yaml` field, manifest, JSON Schema,
`SCHEMA_VERSION` or `agent.lock` format change. Website: no change until CrewAI passes its
acceptance. Cloud: a `CLAUDE.md` note only, as for F3.

**Claude Agent SDK runtime and two-uid deploys, #1089 F7 (multi-framework), per side.** OSS (on
`main`; run end to end on macOS x86_64 2026-11-20, 61/61 local and on `kind` on a 1.4.0 copy,
conformance blocked by #1199, no arm64; not supported): `runtime/claude-agent-sdk@1.0.0` lives
in `examples/northwind-claude-agent-sdk/registry/`, never `builtin_registry` (D8: AgentBreeder
does not ship the CLI). New runtime manifest key `isolation: two-uid`; the deploy manifest's
`runtime` gains optional `model_api: anthropic-messages` and `isolation: two-uid` (JSON Schema
updated; written only when declared, so digests are unchanged). Deploy runs a second, secret-less
CLI container (uid 65533; `--network none` on Docker, a second pod container on `kind`), keys get
`/v1/messages` for `anthropic-messages`, and the gate refuses that wherever guardrails apply
(`RUNTIME_GUARDRAILS_UNSUPPORTED`). Kit 1.0.0 is unchanged (the Anthropic route is a runtime-local
proxy subclass). It needs `service/litellm@1.4.0` (#1187); Northwind's stack stays on 1.0.0 and
the opt-in acceptance runs on a copy pinned to 1.4.0. Website: no change until the runtime passes its acceptance and D15's support
call. Cloud: a `CLAUDE.md` note only; it has no code that reads the deploy manifest's `runtime`.

**Console sign-in, #818 M2 (epic #1054), per side.** OSS (on `main`): `up --set-password`,
`--reset-password` and `--link`; `center.sqlite3` schema 4 (`users` with scrypt hashes, and
`security_events`); new console routes (`/api/auth`, `/api/setup`, `/api/password`,
`/api/signout`, `/api/signout-everywhere`, `/api/settings`) and `POST /api/session` taking
`{email, password}`. No manifest, JSON Schema, `agent.yaml` or `agent.lock` change. Website: no
change until release R. Cloud: a `CLAUDE.md` note only; it has no code that runs `up`, calls the
console API or reads `center.sqlite3` (the console API is private and unversioned).

**Deep Agents, #1083 F5 (multi-framework), per side.** OSS (on `main`; run end to end on macOS
x86_64 on 2026-11-20, 61/61 local and on `kind`, conformance 26/39, no arm64; not verified):
`runtime/deepagents@1.0.0` on the unchanged kit 1.0.0 (state backend only; reserved tool names
`ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, `task`; the `task`
sub-agent counts against the turn's step cap, D10) and `examples/northwind-deepagents`
(Northwind's registry and stack; Northwind's `stack.yaml` names the new agent). No `agent.yaml`
field, manifest, JSON Schema, `SCHEMA_VERSION` or `agent.lock` format change. Not in
`agentspec.VERIFIED` or `RUNTIMES_RUN`. Website: no change until Deep Agents passes its
acceptance. Cloud: a `CLAUDE.md` note only; it has no code that reads a runtime manifest, the
reply's `framework`/`runtime` or the deploy manifest's `runtime`.

**Token cluster provider, #771 K1 (epic #770), per side.** OSS (on `main`; unit-tested with a fake
kubectl and a local TLS server; the opt-in `kind` check passed once after the #1205 mint fix, on
macOS with kubectl 1.31.4 on 2026-11-21, with a stub policy check and no `--node-cidr`; no other
cluster): `kubernetes_token.py` and `cluster_token.py`; the operator config's `kubernetes` block
accepts `provider: token` with `token:` and `tools:` (and `kubeconfig` becomes `kind`-only);
`cluster connect token` checks a cluster and then refuses with `KUBERNETES_POLICY_UNCHECKED`
(the admission policy check is K2), as do deploys and `promote` to a `token` environment; a
by-reference binding must belong to the deployment's environment; `disable` revokes the gateway
key in `finally`; `doctor` reports leftover session directories. No manifest, JSON Schema or
`agent.lock` change. Website: no change until K9's run. Cloud: a `CLAUDE.md` note only; it has
no code that reads the operator config's `kubernetes` block.

**Cluster provider interface, #771 K0 (#908 G0), per side.** OSS (on `main`):
`kubernetes_providers.py` (`session`, `check`, `registry`, `blocked_cidrs`; `kind` is the code
that already ran, moved), the operator config's optional `kubernetes.provider` (omitted means
`kind`), tagged bindings (`kind|…` inline, other providers by reference in a new
`cluster_bindings` table, `KUBERNETES_BINDING_CHANGED`) and store schema v5 (`migrate`).
`status --json` shows the tagged `context`. No manifest, JSON Schema or `agent.lock` change.
Unit-tested; nothing run on a real cluster. Website: `kubernetes.mdx` labels it "on main,
coming in 3.0.0a4". Cloud: a `CLAUDE.md` note only; it has no code that reads the store or
`status --json`'s `context`.

**Restricted accounts and admission policies, #770 K2, per side.** OSS (on `main`): `cluster up
--restricted` (local `kind`), `cluster policy print` and `cluster rbac print`,
`kubernetes_admission.py` (service accounts, Roles and `ValidatingAdmissionPolicy` objects),
`services up` writing the `ab-grants` ConfigMap, a deployment's Secret owned by its Deployment,
and `AGENT_NAME_RESERVED` for `ab-probe`. Since #1207 (#1204): well-typed CEL, `ab-grants`
written first, a seventh policy `ab-grants-guard`, and a fail-closed install; the `kind` test
passed 80/80 and `cluster up --restricted` installed 7 policies and 7 bindings, on Kubernetes 1.37
only. A cluster from an older `main` needs `cluster down` + `cluster up --restricted` (not run). No manifest, JSON Schema or `agent.lock` change.
Website: `kubernetes.mdx` labels it "on main, coming in 3.0.0a4". Cloud: a `CLAUDE.md` note
only; it has no code that reads the operator config or the cluster's objects.

**Services Helm chart, #772 K3, per side.** OSS (on `main`): `services render DIR --cluster
--environment ENV --out CHART` (`services/chart.py`, from the same plan as `services up`,
`restricted` Pod Security fields on every pod, `CHART_SECRET_LEAK`) and an optional
`resources:` in `service.yaml`. Unit-tested only; never installed, `helm lint` not run. No
manifest, JSON Schema or `agent.lock` change. Website: `services.mdx` labels it "on main, coming
in 3.0.0a4". Cloud: a `CLAUDE.md` note only; it has no code that reads `service.yaml` or a chart.

**Docs sync before 3.0.0a4, per side.** OSS: `docs/runtimes.md` (runtimes, the kit,
conformance), `ARCHITECTURE.md`, `README.md` ("On `main`, not in a published preview") and this
file's "What ships today". Website: each capability merged since `3.0.0a3` is labelled "on main,
coming in 3.0.0a4" (`what-ships.tsx`, the docs pages); install pins and the footer badge are
unchanged until the release. Cloud: no change.

## 🎯 When Adding a New Feature

1. **Check the ROADMAP.md** — is this feature planned? Which priority, lane and milestone?
2. **Check AGENT.md** — which AI skills/agents can help build it?
3. **Use `sequential-thinking` MCP** — plan before coding for anything > 100 lines
4. **Write the test first** — TDD is strongly preferred for engine and API code
5. **Update the JSON Schema** — if you changed `agent.yaml` fields
6. **Update the docs** — if you changed a public API or CLI command
7. **Add an example** — if you added a new framework or deployer, add it to `examples/`
8. **Scaffold with `/agent-build`** — when starting a new agent project, run `/agent-build` in Claude Code. The Advisory Path generates IDE config files (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`, `.antigravity.md`) tailored to the chosen framework, model, and deployment target. These files give Claude and Cursor context-aware guidance for the specific agent being built.
9. **Sync the guides both ways** — if the feature touches Cloud or the website, update `CLAUDE.md` in **both** repos per the Cross-Repo Sync table above (what changes OSS→Cloud, Cloud→OSS, and →Website). `AGENTS.md` is a generated catalog (refresh via `100xprism update`, never hand-edit) and does not hold these rules.

---

## Resume & live state (read FIRST when resuming)

- **`RESOLVE.md`** (gitignored working state) — the live loop cursor (current
  epic / stage / NEXT ACTION). **If present**, read it first; update its
  `## LIVE` block at every stage boundary, then `/clear` (free) rather than
  `/compact` (paid). **If absent** (fresh clone / new contributor), fall back
  to `ROADMAP.md` (the current priority order, lanes and dependencies) + the
  normal onboarding flow, and create RESOLVE.md when you start a loop.
  The 2.x roadmap is history only: `docs/history/ROADMAP-2x.md`.

## Reference router — read the doc before working in its area

| When you are… | Read FIRST |
|---|---|
| navigating the repo layout / finding a module | `docs/project-structure.md` |
| reasoning about tech stack, architecture, naming, packaging | `docs/architecture.md` |
| running dev commands or setting env vars | `docs/commands.md` |
| authoring/validating an `agent.yaml` | `docs/agent-yaml-spec.md` |
| wiring or using an MCP server | `docs/mcp-servers.md` |
| writing Python/TS/React code (style, tests) | `docs/coding-standards.md` |
| designing/changing an API route | `docs/api-conventions.md` |
| a schema/API/branding change that crosses repos | `docs/cross-repo-sync.md` |
| onboarding as a contributor | `docs/contributing.md` |
