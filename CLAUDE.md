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

**What ships today (`3.0.0a2` preview on PyPI, `3.0.0-alpha.2` on npm; a bare `pip install agentbreeder` still gets 2.7.3; verified end to end on macOS + Docker Desktop). From the public registries on macOS, `quickstart` ran on pip/uvx/npx (a1 and a2), Northwind 61/61 from pip (a1), and `up` with Northwind 61/61 on local Docker and `kind` through npx (a2); on Linux only install, `--version` and `doctor` ran (a1):**
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

**`agentbreeder up` (#783), per side.** OSS: `center/`, `kubernetes.py`, `cluster.py`, `promote.py`; the
operator config gains `Environment.kubernetes`. Website: `what-ships` and `command-center.mdx`. Cloud: no
code; it has no OSS version pin, and the center's API is private and unversioned, not the #775 contract.

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
