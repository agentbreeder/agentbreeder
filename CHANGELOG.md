# Changelog

All notable changes to AgentBreeder are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **Multi-cloud deploy parity (epic [#505](https://github.com/agentbreeder/agentbreeder/issues/505))** — the same `agent.yaml` now deploys to AWS ECS Fargate, GCP Cloud Run, and Azure Container Apps with identical Track J (sidecar: guardrails/cost/tracing) and Track K (secret mirroring) governance.
- **Azure Key Vault auto-mirror ([#425](https://github.com/agentbreeder/agentbreeder/issues/425), [#197](https://github.com/agentbreeder/agentbreeder/issues/197))** — secrets declared in `agent.yaml` are mirrored to Key Vault at deploy with an idempotent "Key Vault Secrets User" RBAC grant (falls back to an access policy when the vault is in access-policy mode). The deploy uses a per-agent **user-assigned managed identity** so the principal is known before the app exists (no create-time grant race).
- **Cross-cloud parity matrix tests ([#198](https://github.com/agentbreeder/agentbreeder/issues/198))** — `tests/unit/test_multicloud_parity.py` asserts Track J + Track K wiring is equivalent across ECS, Cloud Run, and Container Apps.
- **Studio UX simplification (PR [#519](https://github.com/agentbreeder/agentbreeder/pull/519))** — Studio is now dark-only with the Bricolage Grotesque display font and the marketing type scale; Home leads with a 4-step **Get Started** checklist; `/models` is reframed around *"How do you want to run models?"* (Local / Gateway / Direct); a new guided `/agents/new` wizard (Goal → Workflow → editable recommended stack → Name & create) emits a schema-valid `agent.yaml`; and the catalog "Failed to load" banner is replaced by a non-blocking inline retry.
- **Agent-builder endpoints** — `POST /api/v1/builders/recommend` returns a deterministic stack recommendation from the advisory heuristics (extracted into a pure, tested `engine/recommend.py`), and `POST /api/v1/builders/chat` drives a BYO-key conversational builder (`engine/agent_chat_builder.py`) that emits a validated `agent.yaml` via Anthropic tool-use.
- **Anthropic prompt caching** — `AnthropicProvider` now auto-attaches `cache_control` breakpoints to the large static parts of a request (the tools array and/or the system prompt, gated by Anthropic's ~1024-token minimum). Repeated calls that re-send a large static prefix — e.g. the conversational builder's ~6K-token tool schema — read from cache instead of reprocessing. Cached input tokens are folded into `prompt_tokens` so cost attribution stays accurate.

### Fixed
- **GCP Cloud Run inbound now routes through the sidecar ([#203](https://github.com/agentbreeder/agentbreeder/issues/203))** — the sidecar is the ingress container on `:8080` and reverse-proxies to the agent on internal `:8081`, so bearer-token auth and guardrail egress can no longer be bypassed. Previously external traffic hit the agent directly.
- **Azure Key Vault name round-trip ([#499](https://github.com/agentbreeder/agentbreeder/issues/499))** — the mirrored secret name now matches the deployer's `keyVaultUrl` reference via a shared `sanitize_secret_name()`, making Track K functional on Container Apps.
- **`env` secrets backend now falls back to a writable location** — `agentbreeder secret set` (and the Studio Configure modal) failed with `[Errno 13] Permission denied: '/app/.env'` when the API ran in its container, because the `env` backend always wrote to `$CWD/.env` and the image runs as a non-root user under a root-owned `/app` WORKDIR. `_find_env_file()` now writes to `~/.agentbreeder/.env` when the working directory isn't writable (matching the behavior already documented in the CLI reference). Project-local `.env` files in a writable directory are unaffected.
- **Docs: local-dev `docker compose` commands now point at the compose file** — there is no root-level compose file, so bare `docker compose up` failed with `no configuration file provided: not found` when run from the repo root. CONTRIBUTING and the gateway/graphrag/how-to docs now use `docker compose -f deploy/docker-compose.yml ...`, matching the convention already used elsewhere.
- **Quickstart/standalone Docker images now pull from the published `rajits/` namespace** — `agentbreeder quickstart`, the `agentbreeder studio` CLI generator, and the standalone compose referenced `agentbreeder/agentbreeder-{api,dashboard}:latest`, but those repos aren't provisioned on Docker Hub (HR-7), so the pull failed with `pull access denied` (quickstart silently fell back to a from-source build). Repointed the quickstart/standalone compose, the `studio` command, and the docs `docker pull`/`run`/`rmi` commands to `rajits/agentbreeder-*`, where `release.yml` actually publishes. (The injected sidecar default image is still `agentbreeder/agentbreeder-sidecar` — tracked separately since it affects production deploys, not just local dev.)

### Changed
- **App Runner fails fast at validate-infra ([#501](https://github.com/agentbreeder/agentbreeder/issues/501))** — when `guardrails:` or `secrets:` are declared, App Runner (single-container, can't host the sidecar) now errors at validation instead of silently deploying without governance. ECS Fargate is the AWS parity target.
- **Hardened the injected sidecar ([#500](https://github.com/agentbreeder/agentbreeder/issues/500))** — pinned the sidecar image to a version tag (dropped `:latest`) and added a `securityContext` across the cross-cloud injectors ([#400](https://github.com/agentbreeder/agentbreeder/issues/400)).
- **Dev `docker compose` stack persists secrets** — the `api` image now pre-creates `/home/appuser/.agentbreeder` (owned by the runtime user) and the `api` service mounts a named `agentbreeder-secrets` volume there, so keys set through Studio/CLI survive `docker compose down`/`up` instead of living only in ephemeral container storage.

## [2.5.1] — 2026-05-22

### Fixed
- **Release pipeline** — rolled `release.yml` Docker image refs back from `agentbreeder/agentbreeder-*` to `rajits/agentbreeder-*`. The canonical `agentbreeder/` org doesn't have repos provisioned on Docker Hub yet (HR-7), so v2.5.0 failed at the `Build & Push Images` step with `insufficient_scope`, cascade-blocking PyPI publish + GitHub Release + Homebrew. v2.5.0 stays as a phantom tag; this patch is the first build to actually land on PyPI + Docker Hub since v2.1.0.

## [2.5.0] — 2026-05-22 — **YANKED (phantom tag, never published)**

> **Do not `pip install agentbreeder==2.5.0` — it does not exist on PyPI.** The git tag `v2.5.0` was cut but `release.yml` failed at the Docker Hub push step (`agentbreeder/agentbreeder-api` → `insufficient_scope`; the canonical `agentbreeder/` org has no repos provisioned yet, see [#479](https://github.com/agentbreeder/agentbreeder/issues/479)). The cascade-failure left publish-pypi, github-release, and homebrew-tap all skipped. **v2.5.1** carries the same payload (security fixes + 5 v2.5 features) and is the first build to actually land on PyPI + Docker Hub since v2.1.0.

### Security — HIGH-severity dependency CVE fixes

> **All four findings were pulled in transitively and predate the v2.4 cycle.** Pinning floors here so every `pip install agentbreeder` picks up the patched releases. CI's `pip-audit` job is now clean against the v2.5.0 lockset.

- **Bumped** `python-multipart>=0.0.27` (was `>=0.0.26`) — fixes [GHSA-pp6c-gr5w-3c5g](https://github.com/advisories/GHSA-pp6c-gr5w-3c5g): DoS via unbounded multipart part headers. Reaches us through FastAPI's form parsing.
- **Pinned** transitive `Mako>=1.3.12` — fixes [GHSA-2h4p-vjrc-8xpq](https://github.com/advisories/GHSA-2h4p-vjrc-8xpq): path traversal via backslash URI in `TemplateLookup` (Windows-only, used by alembic).
- **Pinned** transitive `urllib3>=2.7.0` — fixes [GHSA-mf9v-mfxr-j63j](https://github.com/advisories/GHSA-mf9v-mfxr-j63j) (decompression-bomb safeguards bypassed in streaming API) and [GHSA-qccp-gfcp-xxvc](https://github.com/advisories/GHSA-qccp-gfcp-xxvc) (sensitive headers forwarded across origins on proxied low-level redirects).
- **Pinned** transitive `starlette>=1.0.1` and `idna>=3.15` — picks up [GHSA-86qp-5c8j-p5mr](https://github.com/advisories/GHSA-86qp-5c8j-p5mr) and [GHSA-65pc-fj4g-8rjx](https://github.com/advisories/GHSA-65pc-fj4g-8rjx) (IDNA encode bypass of CVE-2024-3651 fix) alongside the urllib3 bump.

### v2.5 — Realistic quickstart expectations + `--yes` for scripted runs (#468)

> **P3 trust gap closed.** "Two commands. That's it." was technically true and practically misleading — `quickstart` is an interactive wizard with up to six prompts (container runtime, model source, port conflicts, Ollama install, model pull, cloud keys), plus a ~3 GB download if you pick Ollama. The new headline sets realistic expectations, the new callout names what the wizard will ask, and a `--yes` flag gives CI users a true zero-prompt path.

- **Added** a `--yes` / `-y` flag to `agentbreeder quickstart`. Sets a module-level sentinel consulted by 13 `console.input` call sites so every yes/no prompt picks the safe default: model-source falls back to the legacy "Both" path (or Cloud when `--no-ollama` is set), port-conflict killer auto-accepts, Ollama install / rebind / model-pull / Studio rebuild auto-accept, missing env keys are silently skipped. Blocking prompts that require human input (e.g. "press Enter once the daemon is up") **exit fast with a clear error** instead of waiting on stdin. `--yes` deliberately does **not** auto-install Docker (heavy side effect, sudo) and does **not** invent provider keys (export `OPENAI_API_KEY` etc. before running). The recommended scripted recipe is `agentbreeder quickstart --yes --no-ollama --no-browser`.
- **Marketing copy** — `quickstart.mdx` headline changes from "Two commands. That's it." to "Two commands. Three minutes. Done." A new "What to expect" callout names the interactive prompts and the ~3 GB download, plus shows the scripted recipe.
- **Docs sync** — `cli-reference.mdx` `quickstart` table gains a `--yes` row that names every prompt the flag short-circuits and explicitly calls out the two things it does not do (auto-install Docker, invent provider keys). `quickstart.mdx` "Useful flags" table gets a matching `--yes` / `-y` row. The fully-scripted example is added to both the `Examples:` block in the docstring and the docs Examples list.
- **Tests** new `tests/unit/test_quickstart_assume_yes.py` — 9 cases covering the flag toggle, `_ask_model_source` short-circuit (with/without `--no-ollama`, TTY override), `_collect_provider_keys` non-interactive paths (Ollama reachable / unreachable / no keys collected), and a signature-introspection check confirming `quickstart()` exposes both `--yes` and `-y` decls. Existing 9 model-source tests still pass.

### v2.5 — Container-runtime diagnostics: clear cause + fix per failure (#467)

> **P3 UX gap closed.** Quickstart already detected Docker / Podman / nerdctl, but when the daemon was *installed but unreachable* the user got a generic "cannot connect" message with no clue why. Rootless installs, Snap/Flatpak Docker (socket path mismatch), partial installs (daemon up, CLI missing), and permission-denied on `/var/run/docker.sock` were all silently lumped together. Each now produces a specific cause line and the exact fix command.

- **Added** new diagnostic helpers in `cli/commands/quickstart.py`: `_docker_host_socket_path()`, `_docker_socket_candidates()`, `_docker_socket_status()`, `_docker_is_rootless()`, `_docker_info_error()`, `_diagnose_missing_runtime()`, and `_diagnose_runtime_failure(binary)`. These probe `$DOCKER_HOST`, the default socket, `$XDG_RUNTIME_DIR/docker.sock`, and `/run/user/$UID/docker.sock`; classify failures into `daemon_down` / `permission_denied` / `stale_docker_host` / `cli_missing_socket_present`; and emit Rich-formatted hints with cause + fix.
- **Wired** the diagnostics into the Step 1 (Container Runtime) flow: the "daemon not running" branch now routes through `_diagnose_runtime_failure(binary)` so each failure prints a specific cause; the no-runtime-found branch first calls `_diagnose_missing_runtime()` to catch the partial-install case before falling back to the generic install panel.
- **Surfaced** the active runtime + override hint: the `Found docker` line now echoes `via $DOCKER_HOST → <path>` when applicable and prints `Override the daemon target with export DOCKER_HOST=unix://<path>` for the default case, so users always know how to retarget.
- **Added** a rootless-Docker info note printed after the daemon-up confirmation (volume ownership caveat) — non-blocking, surfaced via `docker info --format '{{.SecurityOptions}}'`.
- **Docs** — `cli-reference.mdx` `quickstart` gains a "Container-runtime diagnostics" table mapping each detected condition to its cause line + fix. `quickstart.mdx` "When something goes wrong" table gains five new rows (stale `$DOCKER_HOST`, EACCES on socket, Snap/Flatpak socket path mismatch, partial install, rootless volume permissions).
- **Tests** new `tests/unit/test_quickstart_runtime_diagnostics.py` — 23 cases covering `$DOCKER_HOST` parsing (unix / tcp / unset), socket-candidate ordering and de-duplication, socket-status classification (reachable / permission_denied / missing / PermissionError on `stat`), rootless detection (present / absent / `docker info` fails / no CLI), the partial-install and permission-denied missing-runtime hints, the stale-`$DOCKER_HOST` priority branch, the EACCES branch, the socket-path-mismatch branch, the macOS daemon-down branch, the Podman branch, and the generic fallback for unknown binaries.

### v2.5 — Cloud-only quickstart branch (#466)

> **P3 UX gap closed.** A power user with an existing OpenAI / Anthropic / Google API key used to sit through `quickstart`'s Ollama install + ~3 GB model pull whether they wanted local inference or not. `--no-ollama` existed but was buried in the troubleshooting table. Quickstart now asks up front, and the flag is documented as a first-class cloud-only shortcut.

- **Added** `_ask_model_source()` to `cli/commands/quickstart.py` — early in Step 2 (LLM Providers) the interactive wizard asks: **Local** (Ollama + model pull), **Cloud** (skip Ollama, prompt for cloud keys), or **Both** (default, current behavior). Pressing Enter picks "Both" so muscle-memory keeps working; passing `--no-ollama` short-circuits to "Cloud" without prompting; non-TTY runs keep the legacy "Both" behavior so existing CI scripts don't break.
- **Wired** the new helper into the Step 2 flow: `skip_ollama` is now forwarded to `_ensure_ollama()`, and `skip_cloud_keys` shortcuts past `_collect_provider_keys()` for the Local-only path (Ollama status is still checked via the existing `_ollama_running()` helper so the summary line stays accurate).
- **Improved** the `--no-ollama` help text on `agentbreeder quickstart --help` from a terse one-liner to a description that names the use case, the time/disk it saves, and that it's equivalent to the interactive Cloud choice.
- **Added** an `--no-ollama` example to the quickstart docstring's `Examples:` block so it shows up in `--help`.
- **Docs** — `quickstart.mdx` step-2 description rewritten to name the three model-source options; new "Already have a cloud API key?" callout pointing at `--no-ollama`. `cli-reference.mdx` `quickstart` table updates the `--no-ollama` row, adds the model-source prompt to the "What it does" list, and adds the `--no-ollama` example.
- **Tests** new `tests/unit/test_quickstart_model_source.py` — 9 cases covering the `--no-ollama` short-circuit, the non-TTY short-circuit (with and without the flag), each interactive branch (1 → Local, 2 → Cloud, 3 → Both), the default-on-Enter case, the invalid-input-then-retry case, and whitespace trimming.

### v2.5 — `agentbreeder doctor` prerequisite preflight (#462)

> **P2 UX gap closed.** A first-time user on a machine without Docker, Python ≥ 3.11, or 8 GiB free disk used to discover this *mid-`quickstart`* — sometimes after a 3-minute image pull. The new `doctor` command runs the same checks in under two seconds and prints platform-specific fix commands; `quickstart` now runs it as its first step.

- **Added** `agentbreeder doctor` CLI command in `cli/commands/doctor.py`. Checks Python version, container runtime presence + daemon reachability (reuses the existing detection from `quickstart.py` so Docker / Podman / nerdctl all work), free disk on the working volume (≥ 8 GiB), and total RAM (≥ 4 GiB, best-effort via `os.sysconf` on Linux and `sysctl hw.memsize` on macOS — falls back to a warn-only check on platforms where RAM can't be detected without a new dependency). Each failed check renders a copy-pasteable platform-specific fix block. Exits `0` on pass, `1` on any blocker. Supports `--json` for scripts and CI.
- **Wired** `doctor` into `agentbreeder quickstart` as the first step. Quickstart now bails in ~1.2 s on a machine without a running container runtime instead of running through the welcome panel and dying at "Step 1: Container Runtime" with a less actionable error. Bypassable via the new `--skip-doctor` flag for CI sandboxes that intentionally skip host checks.
- **Updated** welcome panel in `cli/main.py` to surface `doctor` in the "Useful anytime" command list, alongside `list agents`, `chat`, `down`, and `--help`.
- **Docs** added a "Before you start" callout at the top of `quickstart.mdx` listing the three concrete prerequisites and pointing at `doctor`; added a full `doctor` entry to `cli-reference.mdx` (above the `quickstart` entry so it reads in install order); added `--skip-doctor` to the quickstart options table in both docs.
- **Tests** new `tests/unit/test_doctor.py` — 15 cases covering each check in isolation (Python floor, disk-low, runtime-missing, runtime-daemon-down, runtime-happy, RAM-undetectable, RAM-below-min) plus `has_blocker()`, the Typer command's exit codes for pass and fail, and the `--json` payload shape. All 15 pass.

### v2.5 — Install path discovery + pip/pipx standardization (#463)

> **P2 UX gap closed.** The #1 entry in `quickstart.mdx`'s troubleshooting table is `agentbreeder: command not found` after `pip install` — pip's script directory often isn't on `PATH`. Docs now recommend `pipx install agentbreeder` as the primary path (pipx adds the binary to a directory that's already on `PATH` everywhere), with `python3 -m pip install agentbreeder` as the alternative (works regardless of whether `pip`/`pip3` is on `PATH`).

- **Added** install-path hint to the welcome panel (`cli/main.py`). When pip's script directory isn't on `PATH`, `agentbreeder welcome` and the first-run banner now print a yellow panel showing the scripts dir (`sysconfig.get_path('scripts')`), the active shell, and the exact line to append to the user's shell rc (zsh / bash / fish / PowerShell). Suppressed when the scripts dir is already on `PATH`, so pipx and venv users see no noise.
- **Updated** `quickstart.mdx` two-command snippet to `pipx install agentbreeder` (with a callout pointing at `python3 -m pip install agentbreeder` for users without pipx). Same change in the troubleshooting row for `command not found`.
- **Updated** `how-to.mdx` Install section to lead with `pipx install agentbreeder`, then fall back to `python3 -m pip install agentbreeder`. Removed `pip3 install ...` lines.
- **Updated** `faq.mdx` "command not found" section to recommend `pipx` first, then the manual PATH-edit instructions, with the section header switched to `python3 -m pip install agentbreeder`.
- **Standardized** install commands across `full-code.mdx`, `low-code.mdx`, all five `migrations/from-*.mdx` checklists, and `migrations/overview.mdx`. Every user-facing install line now reads `pipx install agentbreeder` (or `python3 -m pip install agentbreeder`) — no more `pip` vs `pip3` ambiguity in docs. Blog posts and CI examples (where `pip install` is idiomatic for the platform) are intentionally untouched.
- **Tests** new `tests/unit/test_cli_install_path.py` — 12 cases covering `_scripts_dir()`, `_scripts_dir_on_path()` happy/empty/unrelated PATH cases, `_shell_rc_hint()` for zsh / fish / bash-default / Windows PowerShell, and the welcome command's panel-shown / panel-hidden branches.

### v2.5 — First-run guided tour + empty-state hero (#465)

> **P1 UX gap closed.** After first sign-in the user used to land in Studio with a fully populated registry (5 sample agents seeded by quickstart) and zero guidance on what to click. The 4-step welcome tour fixes that, and the no-agents page now ships a hero CTA instead of a one-line text dead-end.

- **Added** `TourProvider` + `useTour()` in `dashboard/src/hooks/use-tour.tsx`. Per-browser via `localStorage` (key `ag-tour-completed-v1`). Bumping the version suffix re-shows the tour to everyone after a major redesign. No backend round-trip — tour is an onboarding affordance, not user state worth syncing across devices.
- **Added** `WelcomeTour` modal in `dashboard/src/components/welcome-tour.tsx`. Four steps — Welcome / Try chatting / Build your own / Governed by default — each with a deep-link CTA (Visit Agents, Open Playground, Open Builder, See Costs) that closes the tour and navigates. Centered modal with progress dots, Back / Next / Skip / Done nav, top-right X, and Esc-to-dismiss. No DOM-spotlight coupling to specific element positions — pure prose tour, immune to UI rot across 46 pages.
- **Wired** `TourProvider` + `<WelcomeTour />` into the `RequireAuth` tree in `App.tsx` so the tour only renders for authenticated users and overlays the full viewport.
- **Added** "Restart tour" link in the shell footer (`components/shell.tsx`) so users can replay the tour after dismissing it. Hidden when the sidebar is collapsed.
- **Replaced** the text-only "No agents registered. Deploy an agent with `agentbreeder deploy` and it will appear here automatically" empty state on `/agents` with a `NoAgentsHero` block: two equal-weight CTAs (visual builder Link + CLI snippet), both routing through the same 8-step deploy pipeline. Filtered-empty state (when search/filters return nothing) preserves the old `EmptyState` since it's a different intent.
- **Tests** new `dashboard/src/__tests__/use-tour.test.tsx` — 4 Vitest cases covering auto-open on first mount, no-show when flag set, `dismiss()` persistence, and `open()` after dismiss (the restart-tour path). All pass.

### v2.5 — Force password change on first login (#464)

> **P0 security gap closed.** The seeded admin account ships with the publicly documented credential `admin@agentbreeder.local` / `plant`. Anyone exposing Studio beyond `localhost` (e.g. via the Caddy reverse-proxy example in `SELF_HOSTING.md`) was one curl away from a known-credential takeover. This fix forces a rotation on first sign-in before the user can do anything else.

- **Added** `must_change_password BOOLEAN NOT NULL DEFAULT FALSE` to the `users` table (alembic migration `023`). Existing user rows on running installs are unaffected — only new admins seeded by `_seed_default_admin` get the flag set to TRUE.
- **Added** `POST /api/v1/auth/change-password` endpoint. Accepts `{old_password, new_password}` on an authenticated session, verifies the old hash, rejects passwords shorter than 8 chars or equal to the old one, rotates the hash, and clears the flag. Returns the refreshed `UserResponse`.
- **Updated** `POST /api/v1/auth/login` response: `TokenResponse` now carries a `must_change_password` boolean so clients can route to a forced-rotation flow.
- **Updated** `_seed_default_admin` in `api/main.py` to set `must_change_password=True` on the seeded admin row. Existing single-user installs that already rotated stay unaffected (the flag's default is FALSE).
- **Studio** — new `/change-password` route with a non-dismissable rotation form (current / new / confirm fields, live validation, sign-out escape hatch). The `RequireAuth` guard now intercepts every navigation with a redirect to `/change-password` while the flag is set; a separate `RequireAuthOnly` guard wraps the change-password route itself to break the redirect loop.
- **CLI** — `agentbreeder login` detects the flag in `/auth/login` (email+password path) or `/auth/me` (`--token` paste path) and walks the user through the rotation **before** persisting the token to the OS keychain. Tone-aware messaging — the seeded-admin case gets a security-focused warning, admin-rotated users get a generic one.
- **Tests** new `TestChangePasswordRoute` class with 5 cases (requires auth, happy path clears flag, wrong old → 401, short new → 422, same-as-old → 422). Updated `TestLoginRoute` to cover the flag-passthrough. 31 tests pass in `test_auth.py`; 89 adjacent RBAC tests unaffected.
- **Docs** new "First-login password rotation" section in `authentication.mdx` covering the user flow, the wire format, the manual `UPDATE users SET must_change_password=TRUE` admin reset path, and the note that the gate is currently client-side only. Inline mentions in `quickstart.mdx`, `no-code.mdx`, `how-to.mdx`, `local-development.mdx`.

### v2.5 — Rename Dashboard → Studio (user-facing rebrand, #460)

> **User-facing string sweep only.** No folder renames, no Docker image renames, no route/env/identifier changes. `dashboard/`, `agentbreeder/agentbreeder-dashboard`, `DASHBOARD_URL`, the `dashboard` compose service, and `area:dashboard` label all stay; they're internal handles, not the brand.

- **Studio is the canonical name** for the React UI at `:3001`. Every user-visible "Dashboard" string in the shell, CLI output, docs, API docstrings, and root contributor docs now reads "Studio" (or "AgentBreeder Studio" at top-level).
- **CLI breaking change** `agentbreeder ui` is gone. The command is `agentbreeder studio` — same flags (`--follow`, `--pull`, `--port`, `--api-port`), same behavior. Anyone with a script using `agentbreeder ui` must update it. The compose project name (`agentbreeder-ui` → `agentbreeder-studio`) and state file (`~/.agentbreeder/docker-compose.ui.yml` → `docker-compose.studio.yml`) also renamed; existing containers from a previous `ui` run can be cleaned up with `docker compose -p agentbreeder-ui down` if needed.
- **Studio shell** browser `<title>`, sidebar wordmark, version chip, and two body strings on the Models + Agent Detail pages now show "AgentBreeder Studio".
- **CLI output** every `quickstart`, `studio`, `up`, `auth` panel / status table / smoke check / error message reads "Studio". The `services_ok` dict keys derived from those labels flipped from `dashboard` → `studio` in lockstep across 3 call sites.
- **Website docs** 27 `.mdx` files updated across `quickstart.mdx`, `faq.mdx`, `no-code.mdx`, `low-code.mdx`, `full-code.mdx`, `authentication.mdx`, `cli-reference.mdx`, `how-to.mdx`, `local-development.mdx`, `playground.mdx`, `tools.mdx`, `rag.mdx`, `graphrag.mdx`, `mcp-servers.mdx`, `prompts.mdx`, `gateway.mdx`, `gateways.mdx`, `providers.mdx`, `secrets.mdx`, `evaluations.mdx`, `runtime-contract.mdx`, `migrations.mdx`, `migrations/overview.mdx`, `migrations/from-autogen.mdx`, `orchestration-sdk.mdx`, `orchestration-yaml.mdx`, `registry-guide.mdx`. Tab-pair labels (7 `Tabs items={['Studio', …]}` ↔ `<Tab value="Studio">` pairs) updated in lockstep. The FAQ anchor `#dashboard-at-httplocalhost3001…` slug renamed to `#studio-at-httplocalhost3001…` with all inbound links updated.
- **Marketing site** `agent-for-all.tsx` animated demo header reads "Studio → New Agent"; `cloud-coming.tsx` "cost dashboards" reads "cost tracking".
- **API docstrings** `agentops.py`, `providers.py`, `secrets.py`, `models.py`, `agents.py`, plus `engine/builder.py` and `api/services/agentops_service.py` — visible via `/docs` OpenAPI.
- **Root docs** `ARCHITECTURE.md`, `SELF_HOSTING.md`, `AGENT.md`, `CONTRIBUTING.md` — stack matrix, IA diagrams, contributor commands.
- **`CLAUDE.md`** new "📛 Product Naming" section codifies the **Studio rule**: Studio is a top-level surface name only, **never** a feature suffix. Inner pages take functional nouns (Agents, Costs, Sessions, Evals, Registry, Audit, Fleet, Playground). No "Cost Studio", no "Sessions Studio". Also lists the third-party "dashboard" references (Grafana, Stripe, Google Security Dashboard, AWS CloudWatch) that stay as-is.
- **Pre-commit guardrail** new `.githooks/pre-commit` blocks staged lines that combine "dashboard"/"studio" with a JWT, API key, or third-party hostname like `dashboard.stripe.com`. Activate with `git config core.hooksPath .githooks`; `SKIP_REBRAND_GUARD=1` bypass for legitimate hits (e.g. the Stripe URL in `examples/templates/returns-processor/README.md`).
- **CI check** new `MDX Title Uniqueness` job in `.github/workflows/docs-check.yml` (calling `.github/scripts/check-mdx-titles.sh`) fails when two `website/content/docs/*.mdx` files share an identical frontmatter `title:`. Prevents the SEO collision a sloppy find-and-replace can produce.
- **SEO transition copy** the H1 of `no-code.mdx` reads "AgentBreeder Studio (formerly the Dashboard)" for ~6 months so users Googling "agentbreeder dashboard" still match. Page titles in the sweep are suffixed " — AgentBreeder Studio" to keep `<title>` tags unique.
- **What we did NOT touch** the `dashboard/` folder, `agentbreeder/agentbreeder-dashboard:latest` Docker image, `DASHBOARD_URL` constant, `dashboard_port` / `dashboard_key` / `_dashboard_smoke_check` identifiers, `CostDashboard` React component, `workflow:new-dashboard-page` slug, GitHub `area:dashboard` label, and any historical name in `CHANGELOG.md` itself. Internal handles are stable.
- **Test** 11 unit tests in `tests/unit/test_studio_cmd.py` (renamed from `test_ui_cmd.py`) cover the `studio` command end-to-end; all pass.
- **Cross-repo follow-up** `agentbreeder-cloud` has ~50 user-facing "Dashboard" references in `ROADMAP.md` and internal specs that should be swept in a companion PR. Tracked alongside the broader UX work in #461.
- **UX tracker** the rebrand surfaced 7 first-run UX gaps (forced password change on `plant`, prereq preflight, empty-state tour, etc.) — filed as #462–#468 under epic #461.

### v2.4 — Tighten `/api/v1/deploys` with team-scoped RBAC (#414)

- **Closed** the HR-1 analogue gap for deploys. Before this change, a user with the `deployer` role in team A could `POST /api/v1/deploys` against an agent owned by team B — `require_role("deployer")` was applied **without** `resource_team=`, so the middleware only verified deployer-anywhere.
- **Two-layer gate** the route now stacks `require_role("deployer")` (fast-rejects users who aren't a deployer in any team) with a new `enforce_team_role(user, agent.team, "deployer")` call (refines the check after the agent's team is resolved). Defense in depth, and the v2.3 viewer-rejection behavior is preserved.
- **Resolution** team is read from `agent.team` when the request carries an `agent_id`, and from the YAML's `team:` field when the request is a builder-mode `config_yaml`. Builder payloads without a `team:` field now return 400 instead of falling through to a less-helpful error.
- **Lifecycle parity** the same team-scope check now gates `DELETE /api/v1/deploys/{id}` (cancel) and `POST /api/v1/deploys/{id}/rollback`. Both look up the job's agent to find the team.
- **Audit log** every deploy lifecycle action (`deploy.create` / `deploy.cancel` / `deploy.rollback`) now emits an `AuditService.log_event` entry tagged with the agent's team and the caller's email + IP.
- **Platform admin bypass** unchanged — admins still pass for any team.
- **Test** 8 new tests cover the cross-team 403 path (POST agent_id, POST config_yaml, DELETE, POST /rollback), the same-team 200 path, the YAML-missing-team 400 path, and the platform-admin escape hatch. Six existing tests in `test_api_routes_extended.py` now override `get_db` to mock the new agent/job lookups that the team-scope check requires.
- **Cross-repo** `agentbreeder-cloud` does not consume `/api/v1/deploys`; no companion PR needed.

### v2.4 — `agentbreeder deploy --remote` closes in-process RBAC bypass (#416)

- **Closed** the Phase A bypass where `agentbreeder deploy` ran `engine.builder.DeployEngine` in-process — RBAC, audit logging, and team-scoped credentials never fired for CLI deploys.
- **Added** `--remote` and `--local` flags on `agentbreeder deploy`. `--remote` POSTs `/api/v1/deploys` and polls the detail endpoint until terminal; the bearer token comes from `cli/_http` (env var or OS keychain). `--local` keeps the in-process engine for dev / offline use.
- **Default behavior** the CLI picks remote when `$AGENTBREEDER_URL` is set, otherwise local. Explicit flags always win over the env var. Production environments should set `$AGENTBREEDER_URL` so `agentbreeder deploy` defaults to the gated path.
- **Polling** until SSE streaming lands in #387, remote mode polls `GET /api/v1/deploys/{job_id}` every 2 s for up to 30 min. Terminal statuses (`succeeded`/`failed`/`cancelled` from v2.3 plus `complete`/`error` from the v2.4 event bus) all map onto the same exit-code semantics.
- **Tests** 15 new tests cover the mode-selection matrix (flag wins, env wins, mutual exclusion), the happy path (POST → poll → succeeded), the 403 team-scope path (which #414 will make load-bearing), 401 → login hint, terminal `failed` → exit 1, and that `--local` does not hit the API even when `$AGENTBREEDER_URL` is set.
- **Docs** `website/content/docs/cli-reference.mdx` and `website/content/docs/deployment.mdx` describe the two modes and when to use each.

### v2.4 — CLI `login` / `logout` / `whoami` + keychain token storage (#415)

- **Added** three new top-level commands — `agentbreeder login`, `agentbreeder logout`, `agentbreeder whoami` — plus an `agentbreeder auth` sub-app with the same commands for discoverability. Tokens are stored in the OS keychain (macOS Keychain, GNOME libsecret, Windows Credential Manager) via the existing `keyring>=24.0.0` dependency; the `AGENTBREEDER_API_TOKEN` env var still wins over the keychain so CI keeps working unchanged.
- **Added** `agentbreeder context use <team>` / `show` / `clear` for users who belong to multiple teams. Active team is persisted to `~/.agentbreeder/context.json` and sent on every authenticated request as the `X-AgentBreeder-Team` header.
- **Refactor** new `cli/_http.py` consolidates the duplicate `_auth_headers()` / `_api_base()` / `_post()` / `_get()` / `_post_multipart()` / `_request()` helpers that lived in `cli/commands/registry_cmd.py` and `cli/commands/model.py`. Both files now delegate to the shared module; behavior is unchanged for users.
- **DX** the "no token" error now points to `agentbreeder login` instead of telling users to curl `/api/v1/auth/login` and `export` the response themselves. A 401 on any authenticated call surfaces a "your token has expired" hint with the same direction.
- **Test** 36 new unit tests cover token storage (env-wins-over-keychain precedence, headless fallback, set/clear round-trips), `auth_headers()` composition, the `request()` helper's success/4xx/401 paths, and the user-facing `login` / `logout` / `whoami` / `context` flows via `typer.testing.CliRunner`.
- **Docs** new "CLI login" section in `website/content/docs/authentication.mdx`; new `agentbreeder login` / `logout` / `whoami` / `context` entries in `website/content/docs/cli-reference.mdx`.

### v2.4 — DeployOrchestrator.destroy_partial wires to provisioner.destroy() (#450)

- **Wired** the "Roll back" button in the deployment wizard now actually rolls back. `DeployOrchestrator.start()` persists the returned `InfraState` onto the job record after `provisioner.provision()`; `destroy_partial(job_id)` reloads it and calls `provisioner_for(state.cloud).destroy(state)`.
- **Events** publishes `phase: "destroying"` (new value added to the `PhaseName` literal) + log events + terminal `complete`/`error`, so the SSE client transitions cleanly.
- **Idempotent** clears `job.infra_state` after a successful destroy so a second call is a no-op.
- **Safety** delegates to the per-cloud `destroy()` impls which already refuse untagged resources and tolerate 404s.
- **Test** 6 new tests cover state-persist on start, real destroy with state shape, no-op branches (no state / unknown job), idempotency of a second call, error event on provisioner failure.

### v2.4 — Redis-backed DeployJobService persistence (#449)

- **Hardened** `DeployJobService` no longer keeps idempotency map + job records in process memory. Two new Protocols (`IdempotencyStore`, `JobStore`) in `api/services/deploy_stores.py` with in-memory implementations for tests and Redis-backed implementations for production.
- **TTLs** idempotency entries: 24h (per the wizard spec §6); job records: 30d (audit-friendly window).
- **Multi-replica safe** the API can now scale horizontally — the wizard's `Idempotency-Key` dedupe + `GET /api/v1/deployments/{job_id}` reads survive restarts and load-balanced replicas.
- **Test** 9 new tests (`fakeredis`-backed) cover get/set round-trip, TTL assertions, and restart-safety simulations (two store instances over the same Redis backend).

### v2.4 — Deployment wizard E2E specs (#446)

- **Test** Six Playwright specs at `dashboard/tests/e2e/deploy-wizard-*.spec.ts` cover the wizard's happy paths (GCP greenfield, AWS BYO), the validation-failure path (Azure BYO), approval-required flow (poll → SSE handoff), draft-resume from localStorage, and the stalled-deploy failure UI.
- **Fixture** `dashboard/tests/e2e/deploy-wizard-helpers.ts` provides a `mockOrchestrator` extension over the existing `authedPage` fixture — overrides `window.EventSource` with a `FakeEventSource` and exposes `__pushDeployEvent` for driving scripted SSE sequences. Plus per-test helpers for `mockAgents`, `mockValidateInfra`, `mockCreateJob`, `mockGetJob`.
- **CI** Specs run under the existing "E2E Tests (Docker)" job — alert-only per spec §9.7.

### v2.4 — Container hardening: drop root in dashboard + cli images (#444)

- **Security** `dashboard/Dockerfile` and `Dockerfile.cli` now declare a non-root `USER` directive. The main `Dockerfile` (api image) already did this; these two images had been shipping as `root` since v0.
- **dashboard image** runs as `nginx` (UID 101 from `nginx:alpine`). `/usr/share/nginx/html`, `/var/cache/nginx`, `/var/log/nginx`, and `/var/run/nginx.pid` are chowned to the same user. Listens on port 3001 (≥1024), so `CAP_NET_BIND_SERVICE` is not required.
- **cli image** runs as `appuser` (UID 1000). A `--create-home` HOME at `/home/appuser` keeps Python tool caches (`~/.cache`, `~/.config`) writable.
- **Verification** `docker inspect ... --format='{{.Config.User}}'` returns `nginx` and `appuser` respectively; Kubernetes / Cloud Run `runAsNonRoot: true` admission now passes.

### v2.4 — Dashboard Deployment Wizard + SSE progress stream (#389, #387)

- **Added** `/deploy-wizard` route in the dashboard. 5-step UI: agent → cloud + region → BYO or greenfield infra → env vars / secrets / scaling → live deploy with SSE progress. localStorage-backed draft survives refresh; approval-required agents route through `/approvals`.
- **Added** `GET /api/v1/deployments/{job_id}/stream` SSE endpoint with a per-job 200-event ring buffer (30-min TTL). Closes #387.
- **Added** `POST /api/v1/deployments/`, `GET /api/v1/deployments/{job_id}`, `POST /api/v1/deployments/{job_id}/destroy-partial` endpoints.
- **Tooling** Pydantic→TS codegen at `scripts/gen_deploy_event_types.py` keeps `DeployEvent` types in sync.

### v2.3 — Cloud-Deploy Foundation (#413)

- **Added** `engine/provisioners/` — `InfraProvisioner` ABC with read-only `validate_existing` impls for AWS / GCP / Azure (boto3, google-cloud, azure-sdk). Greenfield `provision()` / `destroy()` stubbed (per-cloud follow-ups #382 / #383 / #384).
- **Added** `engine/provisioners/state.py` — `InfraState` round-trippable via `.agentbreeder/infra-state.json`.
- **Added** `engine/provisioners/requirements.py` — static user-input contract per cloud × `(simple, full)` mode.
- **Added** API: `GET /api/v1/deployments/cloud-requirements/{cloud}?mode=simple|full` (any authenticated user) and `POST /api/v1/deployments/validate-infra` (`deployer` role required in the requested team, cross-team → 403, rate-limited 10 req/min via slowapi, audit-logged via `AuditService`).
- **Added** `pgvector>=0.2.5` + `slowapi>=0.1.9` to optional deps; new `gcp` extras group; expanded `azure` group.
- **Docs** — rewrote `website/content/docs/deployment.mdx` to a three-cloud tabbed contract.

### v2.3 — HR-1 memory team-scope enforcement (#418, closes #403)

- **Fixed** `api/routes/memory.py` now forwards `user.team` to `MemoryService` on every read/write. Cross-team access → HTTP 403.
- **Hardened** `MemoryService` now raises `PermissionError` when a team-scoped config is accessed without `requesting_team` (previously a silent allow).
- **Tests** — flipped 2 `MM7` xfail tests to passing + added route-layer 403 coverage.

### v2.3 — HR-3 GraphRAG `custom_entity_types` (#419, closes #405)

- **Added** `entity_extraction.custom_types[]` accepted by `engine/schema/agent.schema.json` + Pydantic mirror (`engine/config_parser.py`).
- **Threaded** `custom_types` through `extract_entities` / `_call_claude` / `_call_ollama`; cache key extended so different type sets don't collide.
- **Docs** — flipped roadmap callout in `graphrag.mdx` to shipped.

### v2.3 — HR-7 Docker Hub namespace migration (#420, closes #408)

- **Changed** every operational reference from `rajits/agentbreeder-*` to `agentbreeder/agentbreeder-*` across 18 files (CI workflow, docker-compose, sidecar, CLI defaults, docs, tests). Symmetric +54/-54.
- Historical files (`CHANGELOG.md`, `ROADMAP.md`, audit specs/plans) intentionally untouched.

### v2.3 — HR-2 memory wiring in 3 Python runtimes (#421, closes #404)

- **Added** `MemoryManager` init + per-turn load/save + shutdown hook to Claude SDK, OpenAI Agents, and CrewAI server templates (mirrors the LangGraph reference).
- **Added** 18 source-level smoke tests under `tests/integration/runtimes/test_memory_wiring_hr2.py`.
- TS runtime parity tracked in #417.

### v2.3 — HR-4 pgvector backend (#422, closes #406)

- **Added** `api/services/pgvector_rag_backend.py` — `PgvectorRAGBackend` with `asyncpg` pool, dimension-namespaced chunk tables (`rag_pgvector_chunks_d{N}`), IVFFlat cosine index. Self-installs the `vector` extension on connect.
- **Changed** `registry/rag.py:_make_pgvector` returns the real backend now; missing DSN raises a clear `ValueError` (no more silent fallback).
- **Added** `pgvector>=0.2.5` to the `rag` extra; integration tests against `pgvector/pgvector:pg16` via testcontainers.
- Wire-through to `RAGStore.search/ingest` lands in #423 / PR #434.

### v2.3 — Dashboard Docker build EPIPE (#433, closes #411)

- **Fixed** `dashboard/Dockerfile` build stage now uses `node:22-slim` (Debian glibc) instead of `node:22-alpine` (musl). Eliminates the esbuild EPIPE that intermittently killed `vite build` under Docker Desktop / BuildKit on macOS. `NODE_OPTIONS=--max-old-space-size=4096` added as belt-and-suspenders.

### v2.3 — pgvector wire-through to RAGStore (#434, closes #423)

- **Added** `RAGIndex.backend` + `backend_config` fields; `RAGStore` now lazily caches per-index backends and dispatches `search` / `upsert` to them when configured.
- **Restart-safe** search no longer short-circuits on empty `idx.chunks` when an external backend is in play — answers come from Postgres after a server restart.
- Stacked on #422 (HR-4 backend adapter).

### v2.3 — GCP greenfield provisioner (#437, closes #382)

- **Added** `GCPProvisioner.provision()` / `destroy()` — creates Artifact Registry repo + per-agent Service Account + 4 default IAM bindings (storage.objectViewer, cloudbuild.builds.builder, logging.logWriter, secretmanager.secretAccessor). Idempotent.
- **Deferred** Cloud SQL → #435, VPC Connector → #436. Both surface as explicit deferred markers in `InfraState.resources` when requested.
- Stacked on #413 (Phase A foundation).

### v2.3 — AWS greenfield provisioner (#383)

- **Added** `AWSProvisioner.provision()` / `destroy()` — creates the minimum-viable ECS Fargate footprint end-to-end via boto3: VPC `10.0.0.0/16` + 2 public + 2 private subnets across 2 AZs, IGW, single NAT Gateway (multi-AZ opt-in via `AWS_MULTI_AZ_NAT=1`), three security groups (alb / agent / db with strict tier-to-tier ingress only), ECS cluster (`FARGATE` + `FARGATE_SPOT`), per-agent IAM execution role with `AmazonECSTaskExecutionRolePolicy` + ECR pull, optional RDS PostgreSQL `t3.micro` in private subnets (when `memory:` declared, `publicly_accessible=False`, `storage_encrypted=True`, random password generated via `secrets.token_urlsafe(32)` and stored in AWS Secrets Manager — never on disk), optional ALB + target group + listener (when `access.visibility: public`, TLS 1.2+ policy `ELBSecurityPolicy-TLS13-1-2-2021-06`). Every resource tagged `AgentBreeder=true` + `AgentName` + `Version`; `destroy()` refuses to touch any resource missing the canonical tag and takes a final RDS snapshot unless `--no-final-snapshot`.
- **Idempotent** — re-running `provision()` against an existing state file is a no-op.
- **Tests** 25 unit tests with mocked boto3 clients cover the security-critical paths: DB SG never ingresses from 0.0.0.0/0, RDS is private + encrypted, plaintext password never appears in `InfraState.model_dump_json()`, RDS rolled back if Secrets Manager write fails, ALB listener uses TLS 1.2+, `destroy()` refuses untagged resources, session never accepts inline credentials.
### v2.3 — GCP Serverless VPC Connector provisioning (#436)

- **Added** `GCPProvisioner._ensure_vpc_connector()` / `_delete_vpc_connector()` — creates a Serverless VPC Access connector named `ab-{agent_name}` (`e2-micro`, min 2 / max 3 instances, `/28` IP range `10.8.0.0/28` by default). Idempotent (check-then-create) and reverses cleanly in `destroy()`.
- **Trigger** explicit via `GCP_PROVISION_VPC_CONNECTOR=1`, or implicit when Cloud SQL is requested with private IP (the #435 path). Tuning knobs: `GCP_VPC_NAME` (default `default`), `GCP_VPC_CONNECTOR_IP_CIDR`, `GCP_VPC_CONNECTOR_MIN_INSTANCES`, `GCP_VPC_CONNECTOR_MAX_INSTANCES`, `GCP_VPC_CONNECTOR_MACHINE_TYPE`.
- **Wiring** Cloud Run deploy already honoured `GCP_VPC_CONNECTOR` (`engine/deployers/gcp_cloudrun.py:110`); operators now pipe the connector name from `.agentbreeder/infra-state.json` instead of provisioning out of band.
- **Deps** added `google-cloud-vpc-access>=1.10.0` to the `gcp` + `all-clouds` extras.
- **Tests** 9 new unit tests cover the trigger predicate (`_should_provision_vpc_connector`), the truncator (`_truncate_connector_id` — 2-25 chars, lowercase, hyphenated), the create / skip / custom-network / idempotent-state paths, plus destroy semantics including the legacy "deferred-marker" branch.
### v2.3 — Azure greenfield provisioner (#384)

- **Added** `AzureProvisioner.provision()` / `destroy()` — creates the minimum-viable Container Apps footprint end-to-end via azure-mgmt-*: Resource Group `agentbreeder-{agent}-rg`, Log Analytics workspace (required prerequisite for ACA), Container Apps Environment (internal-only unless `access.visibility: public`), Azure Container Registry (`admin_user_enabled=False` — Managed Identity auth only), per-agent user-assigned Managed Identity with `AcrPull` role scoped to the specific registry resource ID (never the subscription), and — when `memory:` is declared — VNet + delegated subnet + Azure Database for PostgreSQL Flexible Server (`public_network_access=Disabled`, private DNS) with a random password stored in a per-agent Key Vault. State stores the Key Vault secret URI only.
- **Idempotent** — every helper is check-then-create; re-running `provision()` is a no-op. `destroy()` refuses untagged resources, then deletes the resource group last (cascades anything not explicitly tracked).
- **Deps** added `azure-keyvault-secrets`, `azure-mgmt-authorization`, `azure-mgmt-keyvault`, `azure-mgmt-loganalytics`, `azure-mgmt-msi`, `azure-mgmt-network`, `azure-mgmt-rdbms` to the `azure` + `all-clouds` extras.
- **Tests** 24 unit tests with mocked azure-mgmt clients pin the security-critical paths: PostgreSQL `public_network_access=Disabled`, ACR `admin_user_enabled=False`, AcrPull role scoped to registry not subscription, plaintext password never appears in `InfraState.model_dump_json()`, DB rolled back if Key Vault write fails, `destroy()` refuses untagged resources.

### v2.3 — GCP Cloud SQL provisioning (#435, stacked on #436)

- **Added** `GCPProvisioner._ensure_cloud_sql()` / `_delete_cloud_sql()` — creates a Cloud SQL Postgres 15 instance named `{agent_name}-memory` (default tier `db-f1-micro`, configurable via `GCP_CLOUD_SQL_TIER`), database `agentbreeder_memory`, user `agentbreeder` with a random password generated via `secrets.token_urlsafe(32)`. The password is written to Secret Manager as `agentbreeder-{instance_id}-db-password` in the same `provision()` call; `InfraState.resources["cloud_sql"]` carries the secret resource name only — the plaintext password never appears on disk, in logs, or in the state file. If the Secret Manager write fails after a fresh instance was created, the instance is rolled back.
- **Private only** `ip_configuration.ipv4_enabled=False`, `require_ssl=True`, `private_network` points at the VPC the connector (#436) was created on. Cloud SQL implies the VPC connector unless `GCP_CLOUD_SQL_PRIVATE_IP=0`.
- **Idempotent** instance / database / user are all check-then-create. Re-running `provision()` rotates the user password and re-writes Secret Manager but does not recreate the instance.
- **Wiring** Cloud Run deploy already honoured `GCP_CLOUD_SQL_INSTANCE` (connection name); operators now pipe it from `.agentbreeder/infra-state.json`.
- **Deps** added `google-cloud-sql>=1.6.0` + `google-cloud-secret-manager>=2.20.0` to the `gcp` + `all-clouds` extras.
- **Tests** 9 new unit tests cover skip-by-default, create-when-flag-set, custom-tier, implied-VPC-connector, plaintext-password-never-in-state (regex assertion on `state.model_dump_json()`), the `_truncate_cloud_sql_instance_id` helper, and destroy semantics including the legacy "deferred-marker" branch (37 total).

### Platform Audit Summary (2026-05-18)

A 9-way parallel audit (`docs/superpowers/specs/2026-05-18-platform-audit-design.md`) surfaced 91 findings across 8 code subsystems plus website. 85 additive-safe items landed across 5 execution waves:

- **Wave 0** — Website docs aligned 1:1 with current v1.7.x implementation (`quickstart.mdx`, `how-to.mdx`, `cli-reference.mdx` 100% correct; honest deploy-target status across 9 additional pages; per-target prereqs canonicalized in `deployment.mdx`).
- **Wave 1** — 4 P0 security/correctness fixes: path-traversal sanitization in `markdown_writer`; Pydantic validation of RAG search weights + numeric bounds; structured alerting on pseudo-embedding fallback.
- **Wave 2** — 5 shared utility modules introduced (`engine/observability/degraded_mode.py`, `api/retry.py`, `engine/deployers/_health.py`, `engine/util/path_safety.py`, `api/models/_validators.py`).
- **Wave 3** — Cross-cutting threading of 4 Wave-2 utilities into existing callsites (6 deployers consolidated onto `poll_until_ready`; path validation propagated; warn-once dedup standardized; Pydantic field-types adopted).
- **Wave 4** — 37 P1 fixes across 8 subsystems (idempotency keys, retry semantics, schema validation, sandbox AST guards, MCP timeout config, RAG upload size cap, content-hash dedup, atomic graph extraction, Neo4j indexes + native vector support, memory LIKE escaping, summary circuit breaker, deploy idempotency, sidecar pre-validation, and more).
- **Wave 5** — 38 P2 polish items (docs, type hints, observability, malformed-input tests, framework example READMEs, contributor guides, integration tests).

See per-wave detail in subsequent sections of this changelog.

### Human-Review Backlog (not in this release)

Six audit findings require breaking changes, schema extensions, or cross-repo coordination and were deferred:

- **HR-1** — Memory team-scope isolation not enforced at runtime (data-isolation gap). RBAC integration plan needed; signature changes across memory routes. Wave-5 added xfail tests documenting the current state.
- **HR-2** — Memory wiring missing from Claude SDK / OpenAI Agents / CrewAI runtimes (only LangGraph has full memory integration today). New runtime feature wiring + multi-runtime test plan needed.
- **HR-3** — GraphRAG `custom_entity_types` claimed in docs but not in `agent.yaml` schema. Requires schema bump → website + cloud sync per CLAUDE.md cross-repo rule. Wave-5 added a doc callout marking this as roadmap.
- **HR-4** — `pgvector` RAG backend silently falls back to in_memory. Either implement (large) or remove (breaking).
- **HR-5** — Greenfield (scenario B) infrastructure provisioning across AWS/Azure/K8s (~970 LOC gap). Aligns with the comprehensive architecture plan epics #377–#381.
- **HR-7** — `rajits/` → `agentbreeder/` Docker Hub namespace migration (15 files across CI, sidecar, deployers, docs). Coordination with Docker Hub org needed; out of scope for an additive-only audit.

### Added
- **`registry rag ingest` / `registry rag search` CLI + `agenthub.rag.RagIndex` SDK module** — the API has shipped `POST /api/v1/rag/indexes/{id}/ingest` and `POST /api/v1/rag/search` since v2.0, but neither was reachable from the CLI or the Python SDK, so operators had to fall back to `curl` with hand-built multipart payloads. Two new CLI subcommands close the gap: `agentbreeder registry rag ingest NAME FILE...` resolves the index name to its UUID (UUIDs are accepted directly), validates each file's extension against the API allow-list (`.pdf`, `.txt`, `.md`, `.csv`, `.json`) client-side, and POSTs multipart/form-data to the API; `agentbreeder registry rag search NAME --query TEXT --top-k N` posts to `/api/v1/rag/search` and renders results as a Rich table (score / source / snippet) with a `--json` escape hatch. New `_post_multipart` helper in `cli/commands/registry_cmd.py` reuses the same bearer-token auth flow as the rest of the registry CLI. New `agenthub.rag` SDK module (`sdk/python/agenthub/rag.py`) exports `RagIndex`, `IngestResult`, and `RagIndexError` — `RagIndex(name_or_id, base_url=..., token=...).ingest([paths])` returns a typed `IngestResult` dataclass mirroring the API `IngestionJob` shape, and `.search(query, top_k=...)` returns a normalized list of `{score, source, text, metadata}` dicts that handles both `score`/`similarity` and `text`/`content` field aliases the API has historically used. The name→UUID lookup is lazy and cached on the instance. 21 new tests: `tests/unit/test_cli.py::TestRegistryRagIngest` (5 tests covering UUID passthrough, name lookup, missing-file error, unsupported-extension error, and the full multipart POST flow with field-by-field assertions) + `TestRegistryRagSearch` (2 tests for the body shape and the `--json` path); `tests/unit/test_sdk_rag.py` (14 tests covering token requirement, base-url normalization, name lookup, all four ingest error paths, multipart payload shape, HTTP error propagation, search body, similarity/content field aliasing, and a regression test that asserts the SDK allowlist stays in sync with `api/routes/rag.py`). Docs added in `website/content/docs/cli-reference.mdx` under `### agentbreeder registry`.

- **`agentbreeder registry memory` and `agentbreeder registry rag` CLI commands** — registering a memory config or RAG index previously required hitting `POST /api/v1/memory/configs` / `POST /api/v1/rag/indexes` by hand because the registry CLI only shipped `prompt`, `tool`, and `agent` subapps. Two new Typer subapps in `cli/commands/registry_cmd.py` (`memory push|list`, `rag push|list`) close the gap with the same `_post`/`_get` auth flow the rest of the registry CLI uses. `memory push` parses a `memory.yaml`, maps `backend → backend_type`, `config.window_size → max_messages`, propagates `namespace_pattern`, `scope`, `linked_agents`, and tags through, then POSTs to `/api/v1/memory/configs`. `rag push` parses a `rag.yaml`, flattens the nested `embedding_model: {provider, name}` block to a slash-joined string (`openai/text-embedding-3-small`), promotes `chunking.{strategy,chunk_size,chunk_overlap}` to top-level `chunk_*` fields, and POSTs to `/api/v1/rag/indexes`. Both `list` commands render Rich tables (name / backend / type / id) and accept `--json` for scripting. 9 new regression tests in `tests/unit/test_cli.py` (`TestRegistryMemoryPush`, `TestRegistryRagPush`) cover yaml-to-body mapping for `backend`, `memory_type`, `max_messages`, `embedding_model` flattening, default chunking values, and the missing-name / missing-file error paths. Docs added in `website/content/docs/cli-reference.mdx` under `### agentbreeder registry`.

### Fixed
- **`registry agent push` 500 (MissingGreenlet) + polyglot regression for `runtime.framework` agents** (#376) — registering a polyglot agent (`runtime: { language: node, framework: vercel-ai }` with no top-level `framework:` key) via `agentbreeder registry agent push agent.yaml` was failing the request lifecycle in two distinct places. (1) The validator path was inconsistent: `engine/config_parser.py` had already grown `validate_framework_or_runtime` to accept either top-level `framework:` or `runtime.framework:`, and `api/routes/agents.py` had `_NODE_FRAMEWORKS` + `_guess_language_for_framework()` to coerce the runtime block, but the regression had no test coverage — so any future revert would have silently re-broken the polyglot path. (2) After the validation fix landed, the API was still 500'ing with `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called` because `registry.agents.create_from_yaml` called `AgentRegistry.register(session, config, endpoint_url="")` and returned the ORM instance without ever issuing `await session.commit()` / `await session.refresh(agent)`. The downstream FastAPI response serializer then triggered a lazy-load of the DB-default columns (`created_at`, `updated_at`), which crashed under the async greenlet bridge. The fix in `registry/agents.py` mirrors the manual `create_agent` path in `api/routes/agents.py` — commit + refresh before returning. New regression test `TestCreateFromYaml::test_create_polyglot_runtime_agent` in `tests/unit/test_agent_yaml.py` posts a polyglot YAML (no top-level `framework:`, `runtime.framework: vercel-ai`) through `create_from_yaml` and asserts (a) the agent is created with `framework == "vercel-ai"`, and (b) `created_at` / `updated_at` are populated (this assertion is what fails under the pre-fix code, because the lazy-load on those columns raises). Verified end-to-end against a live API container: `agentbreeder registry agent push /tmp/my-vercel-agent/agent.yaml` now returns 200 and the agent surfaces in `registry agent list` with the correct `vercel-ai` framework.
- **RAG search returns N duplicate hits when a file is re-ingested — ingest now idempotent + search dedups by content hash** — re-ingesting the same file into a RAG index used to silently double the chunk count, and `POST /api/v1/rag/search` happily returned every byte-identical copy, so the dashboard `/rag` search page would show 8 (or more) identical result cards for a single query. Two root causes, both fixed: (1) `RAGStore.ingest_files` was append-only — it never checked whether the incoming bytes already lived in the index. Every chunk now carries a SHA-256 `content_hash` in its metadata (back-filled transparently on legacy chunks the first time they're encountered), incoming chunks whose hash already exists are skipped, and a new keyword-only `replace: bool = False` parameter drops any pre-existing chunks whose `source` matches one of the incoming filenames before ingestion when the caller explicitly wants to overwrite. (2) `hybrid_search` now deduplicates results by `content_hash` (falling back to a SHA-256 of the chunk text for legacy hits) before truncating to `top_k`, so pre-fix indexes that already contain duplicates surface only one copy per unique chunk. Plumbed through the stack: `POST /api/v1/rag/indexes/{id}/ingest` accepts a `replace` form field (`Form(False)`), `agentbreeder registry rag ingest` grows a `--replace` flag, and `agenthub.RagIndex.ingest(files, *, replace=False)` accepts the kwarg — all backward compatible. 7 new regression tests in `tests/unit/test_rag_service.py::TestRAGStoreIngestDedup` (idempotent re-ingest, replace-by-source drop, within-batch dedup, legacy-chunk hash back-fill, the user-facing "8 duplicates for one query" bug), plus CLI test `TestRegistryRagIngest::test_ingest_replace_flag_sends_form_field` and SDK test `TestIngest::test_ingest_replace_kwarg_sends_form_field`. All 95 unit tests across `test_rag_service.py`, `test_sdk_rag.py`, and `test_cli.py::TestRegistryRagIngest` pass.

- **Dashboard `/mcp-servers` page returns 500 — missing alembic migration for ORM columns** — `GET /api/v1/mcp-servers` was failing with `sqlalchemy.exc.ProgrammingError: column mcp_servers.version does not exist`, surfaced to the user as `Failed to load MCP servers: API error 500` on the dashboard. The `McpServer` SQLAlchemy model (`api/models/database.py`) declares `version`, `team`, `deploy_config` and `image_uri` but the columns were never added to the database — they shipped in the v1.1 commit `057f143` ("v1.1 Connectivity") with no accompanying migration, leaving `select(*)` queries built from the ORM mapper to reference columns that didn't exist in the `mcp_servers` table. New idempotent alembic migration `022_add_mcp_server_registry_fields` adds all four columns (nullable, with `ADD COLUMN IF NOT EXISTS` so partially-applied states re-run safely) and indexes `team` to match the `index=True` declaration on the model. New regression test `TestMigration015::test_migration_022_mcp_server_fields` in `tests/unit/test_rbac_system.py` (same pattern as the existing 012/015 migration tests) asserts revision identifiers, that `upgrade`/`downgrade` are callable, and that the migration source includes `ADD COLUMN IF NOT EXISTS` for each of the four fields plus the `ix_mcp_servers_team` index. Verified locally: `alembic upgrade head` advances the DB from `021` to `022` and `GET /api/v1/mcp-servers` returns `200 OK` with `{"data": [], "meta": {"page":1,"per_page":20,"total":0}, "errors":[]}`.

- **Registry rejects polyglot agents; memory config 500s; `validate memory.yaml` uses wrong schema** (#372, #373, #374) — three independent registry/memory bugs surfaced while registering a polyglot Vercel-AI agent + Postgres memory config. (1) `POST /api/v1/memory/configs` was 500'ing with `column "tags" is of type text[] but expression is of type json` because Alembic migration `016` creates the column as `text[]` while the ORM declared it `JSON`. Fixed by switching `MemoryConfig.tags` to `JSON().with_variant(ARRAY(Text), "postgresql")` so PostgreSQL uses the native `text[]` and SQLite falls back to JSON (tests stay portable). (2) `agentbreeder registry agent push` rejected any agent that used the polyglot `runtime: { language, framework }` block with `Validation failed: 'framework' is required`. `registry.agents.validate_config_yaml` now accepts either top-level `framework:` (Python) **or** `runtime.framework:` (polyglot) — mutually exclusive — and `create_from_yaml` routes through the appropriate `AgentConfig` kwarg. The API `POST /api/v1/agents` (and v2 equivalent) now fall back to constructing the `runtime` block with a guessed language (`vercel-ai` / `mastra` / `langchain-js` / `openai-agents-ts` / `deepagent` / `mcp-ts` / `mcp-py` → `node`; `go-custom` → `go`) when the framework string doesn't match `FrameworkType`. (3) `agentbreeder validate memory.yaml` was running the file against the agent schema, producing nonsense `'framework' or 'runtime.framework' is required` errors. `cli/commands/validate.py::_detect_config_type` now classifies memory configs by filename (`memory.yaml` / `memory.yml`) or content shape (`backend` + `memory_type` present, `framework` / `runtime` / `model` absent) and a new `_validate_memory_config` validates against `engine/schema/memory.schema.json` using `jsonschema.Draft202012Validator`. 7 new regression tests in `tests/unit/test_agent_yaml.py` (`TestValidateConfigYaml::test_polyglot_runtime_accepted`, `…test_both_framework_and_runtime_rejected`, `…test_neither_framework_nor_runtime_rejected`) and `tests/unit/test_cli.py` (`TestValidateCommand::test_validate_detects_memory_yaml_by_filename`, `…test_validate_detects_memory_yaml_by_content`). Docs updated in `website/content/docs/cli-reference.mdx` (file-type detection rules) and `website/content/docs/low-code.mdx` (polyglot `runtime:` block accepted by `registry agent push`).

### Docs
- **Wave 0 of the platform audit lands.** Quickstart, how-to, and CLI reference are 100% aligned with the v1.7.x implementation. Stale "supported deploy targets" claims across 9 additional pages now distinguish ✅ Shipped (Local, GCP Cloud Run greenfield) from 🟡 Deployer-exists-but-requires-existing-infra (AWS ECS, App Runner, Azure, K8s, Claude Managed). Canonical status table at [cli-reference#agentbreeder-deploy](https://agentbreeder.io/docs/cli-reference#agentbreeder-deploy); canonical prereqs at [deployment#prerequisites-per-target-as-of-2026-05-18](https://agentbreeder.io/docs/deployment#prerequisites-per-target-as-of-2026-05-18). See audit spec at `docs/superpowers/specs/2026-05-18-platform-audit-design.md`.
- **Quickstart troubleshooting refresh** — added a "blank dashboard at `:3001`" section to `website/content/docs/faq.mdx`, `quickstart.mdx`, and `how-to.mdx` covering: stale `rajits/agentbreeder-dashboard:latest` cached locally, the `--dev` flag for building images from local source, the `BUNDLE` health-check one-liner, and the React-version verification. Added an FAQ note that `migrate-1` exiting is expected (one-shot alembic job). Added a stale-`DOCKER_HOST` row to the Troubleshooting tables. `CONTRIBUTING.md` §6 now documents `quickstart --dev` and the clean-rebuild incantation. `README.md` Install section now points contributors at `--dev` for local-source workflows.

### Fixed
- **`agentbreeder quickstart` autonomous-recovery path on macOS** (#349, #350) — three additional auto-recovery paths so a fresh `quickstart` run reaches a working dashboard without manual intervention. (1) Stale `DOCKER_HOST` auto-strip: when the user's shell exports `DOCKER_HOST=unix:///path/that/no/longer/exists` (e.g. a removed Rancher Desktop socket), the compose pre-flight detects the dead socket, strips the variable from the subprocess env only (never the user's shell), and retries. (2) Ollama 127.0.0.1-only bind detection: parses `lsof -nP -iTCP:11434 -sTCP:LISTEN` to detect loopback-only binds (which are invisible to Docker containers via `host.docker.internal`); offers an interactive Y/n prompt to rebind via `launchctl setenv OLLAMA_HOST 0.0.0.0:11434` followed by an Ollama restart (brew services / `Ollama.app` / `pkill`+`ollama serve` fallback) and verifies the new bind before continuing. (3) Dashboard bundle smoke check: after services come up, validates that the served HTML contains a React mount point and that the Vite bundle is fetchable and non-trivial; on failure offers `compose up -d --build dashboard` when a build context is available, or prints the `compose pull dashboard && compose up -d dashboard` fallback for pip-installed users. Also pins `react` and `react-dom` to **exact** `19.2.5` (was caret) and adds an `overrides` block in `dashboard/package.json` to prevent any transitive bump from reintroducing React error #527.
- **Path traversal in `markdown_writer`** (W1-01): `subdir` is now validated against parent traversal (`..`), absolute paths (`/`), home expansion (`~`), and null bytes. Unsafe inputs raise `ValueError` instead of writing to arbitrary filesystem paths.
- **RAG search request validation** (W1-02 / W1-04): `POST /api/v1/rag/search` now validates body via `RagSearchRequest` Pydantic model — `vector_weight + text_weight` must sum to 1.0 (within float tolerance), `top_k` is bounded `[1, 1000]`, `hops` is bounded `[0, 10]`, `seed_entity_limit` is bounded `[1, 50]`. Invalid bodies produce `422 Validation Error`.
- **Silent embedding fallback alerting** (W1-03): When OpenAI / Ollama is unreachable or `OPENAI_API_KEY` is missing, `embed_texts` still falls back to deterministic pseudo-embeddings to keep ingest moving — but now WARN-logs the first occurrence per (model, reason) per process, and the search response carries a `degraded: true` flag so callers can detect quality-degraded results.

### Security
- **Resolve 6 medium-severity Dependabot alerts before launch** (#173, #338) — bumps `github.com/go-chi/chi/v5` from `5.1.0` to `5.2.2` in `sidecar/`, `sdk/go/agentbreeder/`, and `examples/go-agent/` (fixes host-header injection → open redirect in `RedirectSlashes`); adds npm `overrides` to `website/package.json` pinning `postcss` to `^8.5.10` (resolves to `8.5.13`, fixes XSS via unescaped `</style>` in CSS Stringify), and to `sdk/typescript/package.json` pinning `vite` to `^6.4.2` and `esbuild` to `^0.25.0` (resolves to `6.4.2` / `0.25.12`, fixes vite path traversal in optimized-deps `.map` handling and esbuild dev-server CSRF-style request issue). Verified resolved versions via `npm ls` against each advisory's `first_patched` constraint.

---

## [2.1.0] — 2026-04-30

> **Real backends everywhere.** v2.1 finishes the v2.0 honesty patch by replacing every Coming-soon stub and seeded placeholder on the dashboard with real, persisted, DB-backed implementations. Eight dashboard pages and three runtime contracts move from mock data to production state. Net: 14 issues closed, 17 PRs landed (#211, #213, #210, #214, #212, #207, #204, #209, #205-area, #180, #199, #215, #206, #208 + CI hotfixes #232 #233 #234 #235 #236).

### Added
- **Auto-seed registry tables on first boot** (#180) — a fresh `docker compose up` no longer shows an empty Agents / Prompts / Tools / MCP / Providers dashboard. New `engine.seed.seed_registries(db, examples_dir=None)` is called from the API lifespan handler after `_seed_default_admin`; for each registry table it counts rows, and only when the count is zero loads canonical seed YAMLs from `examples/seed/{agents,prompts,tools,mcp_servers,providers,knowledge_bases}/` and inserts via the existing `AgentRegistry.register` / `PromptRegistry.register` / `ToolRegistry.register` / `McpServerRegistry.create` / `ProviderRegistry.create` services (no raw SQL). Idempotent: re-running is a no-op when any table is non-empty. Gated by `AGENTBREEDER_AUTO_SEED` (default `true` for `AGENTBREEDER_INSTALL_MODE=dev`, `false` for `cloud`). Failures during seeding log a warning and never crash startup. Six canonical seed YAMLs ship in-tree (customer-support agent, support-system-v1 prompt, web-search + order-lookup tools, example-mcp server, local-ollama provider, product-docs knowledge base) and validate against `engine/schema/*.schema.json`. The existing `agentbreeder seed` CLI command grows a `--registry [--examples-dir PATH]` flag that runs the same seeder against a live API DB. 8 new unit tests in `tests/unit/test_first_boot_seed.py` cover empty-DB seed, idempotency, partial-population skip, structured-report shape, missing-directory handling, malformed-YAML resilience, custom `examples_dir`, and unknown `provider_type` recovery.
- **Daily models-sync cron + `agentbreeder model sync-now` CLI** (#199) — model lifecycle reconciliation (added → deprecated → retired) was previously triggered only by an operator hitting `POST /api/v1/models/sync` or running `agentbreeder model sync` against a live API. Now `api/tasks/models_sync_cron.py` schedules a daily background task in the API server's lifespan that calls `_build_discoveries()` + `ModelLifecycleService.sync()` across every configured provider, isolates per-provider failures (one bad provider can't kill the sweep), and emits a `model.sync.scheduled` audit event with the totals. Gated by `AGENTBREEDER_MODELS_DAILY_SYNC` (defaults to `true` when `AGENTBREEDER_INSTALL_MODE=cloud`, `false` otherwise) — no new dependencies (plain asyncio with random initial jitter so co-deployed replicas drift apart). New `agentbreeder model sync-now` CLI subcommand runs the same sweep synchronously in-process, useful for testing and for self-hosted environments without the daily loop. 12 new unit tests cover env-var gating, happy path, empty-providers short-circuit, single-provider failure isolation, audit-failure tolerance, background-task lifecycle, and the CLI's human + skipped output paths.
- **Structured tool-call history across every runtime template** (#215) — the `/playground` Agent tab previously had no way to render the tools an agent called during a run; the page even shipped a `TODO(#215)` note pending a structured-history field. Every Python runtime template (`claude_sdk_server.py`, `openai_agents_server.py`, `crewai_server.py`, `langgraph_server.py`, `google_adk_server.py`, `custom_server.py`) plus the `node/openai_agents_ts_server.ts` template now returns a top-level `history: list[ToolCall]` field on `/invoke`, where each `ToolCall` is `{ name, args, result, duration_ms, started_at }`. Claude SDK records each tool execution inside the agentic loop with a real `duration_ms`; OpenAI Agents pairs `ToolCallItem`/`ToolCallOutputItem` from `RunResult.new_items`; LangGraph pairs `AIMessage.tool_calls` with `ToolMessage` content from the graph state's `messages`; Google ADK pairs `function_call` with `function_response` parts across emitted events; CrewAI walks `tasks_output[*].tool_calls` (best-effort, depends on user attaching step callbacks); Custom (BYO) surfaces `result["tool_history"]` if the user attaches it. The API proxy at `POST /api/v1/agents/{id}/invoke` forwards the field through `AgentInvokeResponse.history` (new `AgentInvokeToolCall` schema). The dashboard playground (`AgentChatPanel`) drops the regex-fallback path and the `TODO(#215)` comment, maps each entry into the existing `ToolCallCard`, and renders the timeline inline in verbose mode. Backwards-compatible — the field is purely additive.
- **Real SOC 2 / HIPAA compliance scanner** (#208) — `/compliance` previously rendered control statuses and the downloadable evidence report from a 12-row `_SEED_COMPLIANCE_CONTROLS` list inside `api/services/agentops_service.py`; nothing was actually checked. Replaced with a real, executable control registry (`engine/compliance/controls.py`) plus a scanner (`engine/compliance/scanner.py`) and a new `compliance_scans` table (Alembic migration `021`). Six controls ship with this PR: `audit_log_retention` (oldest `audit_events` row >= 365d, partial when sparse), `rbac_enforced` (at least one `ResourcePermission` row exists), `secrets_backend_not_env` (workspace backend isn't the local `.env` fallback), `db_ssl_enabled` (Postgres `ssl_is_used()` — skipped on SQLite), `mfa_enabled` (active users have `password_hash`; partial until real MFA ships), `encryption_at_rest_documented` (`docs/security.md` exists). New `ComplianceService.{run_and_persist, get_or_run_latest, status_payload, report_payload}` in `api/services/agentops_service.py`. `GET /api/v1/agentops/compliance/status` and `GET /api/v1/agentops/compliance/report` now run real checks, persist a row to `compliance_scans` (cached 60s to avoid scan-storms on dashboard polling), and return real per-control evidence (row counts, oldest timestamps, backend names, dialect) — no more `"Automated compliance check for X"` placeholder strings. New `POST /api/v1/agentops/compliance/scan` to force-rescan. Removed the v2.0.1 `<ComingSoonBanner>` for #208 from `dashboard/src/pages/compliance.tsx`. Deferred: real TOTP/WebAuthn MFA (still TODO; `mfa_enabled` returns `partial` for now), additional controls, and scheduled cron-driven scans. 27 new unit tests cover the registry shape, every control's pass/fail/partial/skipped paths, scan orchestration with per-control exception trapping, and persistence across session recycle.
- **Workspace secrets backend chooser is live in the dashboard** (#213) — `/settings/secrets` previously rendered a Coming-soon stub that told operators to edit `~/.agentbreeder/workspace.yaml` by hand. Now the page renders a working `<select>` over the supported backends (`env`, `keychain`, `aws`, `gcp`, `vault`); switching backends prompts a confirm dialog (since existing secrets in the previous backend are NOT auto-migrated), then `PUT /api/v1/secrets/workspace` persists the choice through the new `save_workspace_secrets_config()` helper. Backend swap is admin-only (deployer/viewer get 403), validates that the chosen backend can actually be instantiated (so missing optional deps surface as 400 instead of silently breaking the workspace), and emits a `secret.backend_changed` audit event. New `api.secrets.setBackend()` client. 10 new unit tests cover RBAC, validation, audit, file persistence, and workspace-name preservation.
- **Ollama Pull Model live in the dashboard** (#214) — `/settings` provider list previously rendered a disabled `Pull Model` button labelled "Coming soon" for Ollama rows. Now clicking it opens a modal that streams real progress from `ollama pull <model>`. New `OllamaProvider.pull_model()` async generator yields each event from Ollama's `/api/pull` NDJSON stream (`pulling manifest` → `downloading <digest>` with `total`/`completed` byte progress → `success`). New `POST /api/v1/providers/{id}/pull-model` returns those events as Server-Sent Events; rejects 400 when the provider isn't Ollama and 404 when missing. Frontend `api.providers.pullModel()` returns the raw `Response` so the modal can consume the SSE stream via `ReadableStream`. The dialog shows a popular-model chip palette (`llama3.2`, `mistral`, `mixtral`, `qwen2.5`, etc.), a percent progress bar derived from `completed/total`, and final ✓ / ✗ states. 4 new unit tests cover 404 / 400 (non-Ollama) / streamed-success / Pydantic validation.
- **`incidents` PostgreSQL table** (#207) — new Alembic migration `020_incidents_table.py` adds an `incidents` table with `id`, `title`, `severity` (`critical|high|medium|low`), `status` (`open|investigating|mitigated|resolved`), `affected_agent_id` (FK → `agents.id`, ON DELETE SET NULL), `description`, `created_by`, `created_at`, `resolved_at`, `timeline` (JSONB), `incident_metadata` (JSONB) plus indexes on status / severity / created_at / affected_agent_id. New `IncidentSeverity` and `IncidentStatus` enums in `api/models/enums.py`; new `Incident` ORM model in `api/models/database.py`.
- **Real agent version history** (#210) — `/agents/:id` Configuration Tab → Compare Versions panel was reading `MOCK_VERSIONS` + `MOCK_VERSION_YAML` constants because the registry only stored the *current* `config_snapshot` per agent. New `agent_versions` table (Alembic 019) is populated by `AgentRegistry.register()` whenever an agent's version string changes, capturing `(agent_id, version, config_snapshot, config_yaml, created_by, created_at)` with a UNIQUE `(agent_id, version)` constraint so re-registering the same version updates in place. New `GET /api/v1/agents/:id/versions` route + `api.agents.versions()` client; the dashboard now fetches real history (lazily, only when the diff panel opens), defaults the diff selectors to the two newest versions, and shows graceful empty / single-version states. 5 new registry tests cover first-register, version bump, idempotent re-register, actor email, and cascade-delete with parent agent.
- **Visual agent builder emits v2 YAML fields** (#204) — the `/agents/builder` visual mode now exposes a Language toggle (python / typescript), a collapsible Gateways panel with per-gateway URL / api-key-env / fallback-policy overrides, and a lifecycle-aware model picker that hides `deprecated` and `retired` models behind a `Show deprecated` checkbox. Selecting typescript emits the canonical `runtime: { language: node, framework: <fw> }` block (the engine parser rejects a top-level `language:` key). The emit/parse helpers were extracted to `dashboard/src/lib/agent-yaml-emit.ts` so visual → YAML → visual round-trips losslessly through the gateway and language fields. New Python tests in `tests/unit/test_agent_yaml.py` (`TestDashboardEmitFormat`) confirm the python, typescript, and gateways YAML shapes parse cleanly via `engine.config_parser.parse_config`, and 4 new Playwright specs cover the language radio, runtime emit, gateways panel, and deprecated-model toggle.

### Fixed
- **Dashboard `/agentops` page now reads real fleet / events / top-agents / teams from PostgreSQL** (#206) — the AgentOps fleet view previously rendered eight hardcoded agents from `_SEED_AGENTS` with frozen `2026-03-12` timestamps; events came from `_SEED_EVENTS`; top-agents and team comparison were derived from the same seeds. Replaced both seed constants with a new `FleetService` in `api/services/agentops_service.py` that joins the `agents` registry table to 24h aggregates over `traces` (invocations, error rate, p50 latency) and `cost_events` (per-agent spend), and derives operations events from `audit_events` + `cost_events` (single requests over $1.00 surface as `cost_spike` events). `/api/v1/agentops/{fleet,fleet/heatmap,top-agents,events,teams,costs/forecast}` all inject `db: AsyncSession = Depends(get_db)` and the fleet `status`/`health_score` are derived from the registry's `AgentStatus` enum + observed error rate (failed→down, ≥25% errors→down, ≥5%→degraded, else healthy). The `/teams` route still calls `IncidentService.open_count_by_agent_name()` (preserves the #207 integration). On a fresh deploy with no traces / cost events / audit log, every endpoint returns sensible empty-state payloads — no seeded fakes. `dashboard/src/pages/agentops.tsx` drops the v2.0.1 `<ComingSoonBanner>`. The test suite gains 19 new DB-backed tests in `tests/unit/test_agentops_service.py` (7 fleet, 2 heatmap, 4 top-agents, 6 events, 4 team comparison) plus an updated 5-test cost-forecast block; the existing route tests in `test_api_routes_coverage_boost.py` were rewired to mock `FleetService` static methods (6 tests rewritten + 1 new `_override_db_dep`/`_restore_db_dep` helper pair); `test_rbac_phase1.py::test_agentops_fleet_viewer_ok` was updated to override the `get_db` dependency the new way.
- **Orchestration builder canvas now persists** (#211) — the `/orchestrations/builder` page was pure local state with zero API calls; nothing saved across reloads, no Validate/Save/Deploy buttons, and the existing `/api/v1/orchestrations` CRUD endpoints were unreachable from the UI. Added an `api.orchestrations.{list,get,create,update,delete,validate,deploy,execute}` client and wired the builder's new Save / Validate / Deploy buttons. Validation errors render inline with path + message + suggestion. Visual-builder layout (per-node `{x, y}`) round-trips through a new `layout` field on `OrchestrationRecord` (in-memory equivalent of `.agentbreeder/layout.json`). New `/orchestrations` list page with strategy-tagged cards, status badges, delete confirmation, and "New Orchestration" CTA. Loading by `?id=…` rehydrates name / version / strategy / agents from the saved record. Removed the v2.0.1 `ComingSoonBanner`.
- **Dashboard `/incidents` page now persists to PostgreSQL** (#207) — incidents previously lived in `AgentOpsStore._incidents`, an in-memory dict seeded from a fake `_SEED_INCIDENTS` list; every API restart wiped user-created incidents and re-seeded the demo data. Replaced with `IncidentService` in `api/services/agentops_service.py`, backed by the new `incidents` table. The `_SEED_INCIDENTS` list and the `_incidents` dict are gone; a fresh deploy starts with an empty table. `api/routes/agentops.py` now injects `db: AsyncSession = Depends(get_db)` and routes `list / create / get / update / execute_action` through `IncidentService`. The `/api/v1/agentops/teams` endpoint now reads open-incident counts from the same table. `dashboard/src/pages/incidents.tsx` no longer renders the `<ComingSoonBanner>` for #207. Remediation actions (restart / rollback / scale / disable) still only record an operator-intent timeline entry — wiring them to the deploy / rollback machinery and auto-creating incidents from cost anomalies / health-check failures are deferred to follow-up issues.
- **Dashboard `/gateway` page now reads real data instead of synthetic logs and a hardcoded comparison fixture** (#212) — `api/routes/gateway.py` previously generated 100 random "request log" entries per call (`_generate_log_entries`, seeded by `int(time.time()) // 60` so the table refreshed every minute) and returned a hand-coded `_GATEWAY_MODELS` price table for `/api/v1/gateway/costs/comparison`. Both are gone. `/api/v1/gateway/logs` now calls the LiteLLM proxy's authenticated `/spend/logs` endpoint via the new `api/services/gateway_logs_service.py` (normalizes `LiteLLM_SpendLogs` rows into the dashboard's `LogEntry` shape, infers gateway tier from the `custom_llm_provider` field). When LiteLLM is unreachable the endpoint returns `503` with `data: []` and a clear `errors` message — never synthetic data. `/api/v1/gateway/costs/comparison` now aggregates over the real `cost_events` table grouped by `(provider, model_name)`, computing average input/output prices per million tokens from recorded usage; returns an empty list when the table is empty (no more hardcoded fixture). `dashboard/src/pages/gateway.tsx` drops both `<ComingSoonBadge issue="#212">` markers and surfaces a clear "LiteLLM proxy unreachable" banner on the logs tab plus an empty-state on the costs tab. Required env vars (`LITELLM_BASE_URL`, `LITELLM_MASTER_KEY`) documented in the route module docstring.
- **Release workflow PyPI propagation race** — `Build & Push CLI Image` and `Update Homebrew Tap` jobs now probe the PyPI `/simple/` index (via `pip index versions`) instead of the `/pypi/<pkg>/<ver>/json` Warehouse endpoint. The JSON endpoint returns 200 the moment a file is uploaded, but `pip install` reads `/simple/` through Fastly, which can lag by tens of seconds — long enough for the v2.0.1 CLI image build to fail with `Could not find a version that satisfies the requirement agentbreeder==2.0.1`.
- **6 example/template `agent.yaml` files now pass schema validation** (#183) — `examples/quickstart/rag-agent/agent.yaml` switched from inline `knowledge_bases` to a registry `ref`; `examples/quickstart/search-agent/agent.yaml` dropped the unrecognized top-level `entrypoint`; the four `examples/templates/{competitor-monitor,github-pr-reviewer,meeting-summarizer,returns-processor}/agent.yaml` switched `claude_sdk.thinking.type: adaptive` to the schema-correct `claude_sdk.thinking.enabled: true`. All 44 example/template yamls now validate.
- **23 secret-CLI tests un-skipped and rewritten for Track K's command surface** (#202) — three test classes (`TestSecretCommand`, `TestSecretSetPrompted`, `TestSecretSetTags`) had been wholesale `@pytest.mark.skip`'d during the Track K merge to keep CI green. Updated each mock for the new shape: backends now expose `backend_name` (used in JSON output), `secret_set`/`rotate` probe `b.get(name)` to decide created-vs-updated, and the `secret list --json` envelope is now `{workspace, backend, entries: [...]}`. Also fixed two prefix-sanitization tests that were patching `engine.secrets.factory.get_backend` instead of the locally-imported `cli.commands.secret.get_backend`. Added a new `TestSecretSync` class (6 tests) covering invalid target, dry-run, actual mirror, include filter, partial-failure surfacing, and the no-candidates empty state. Coverage on `cli/commands/secret.py` returns from 75% → **92%**.
- **Dashboard `/activity` page now reads from `/api/v1/audit`** (#209) — `dashboard/src/pages/activity.tsx` previously rendered a hardcoded `MOCK_EVENTS` array with timestamps relative to a frozen `NOW = 2026-03-11`. Replaced with `useQuery({ queryKey: ['audit', ...], queryFn: () => api.audit.list(...) })` against the existing `/api/v1/audit` endpoint. Resource-type filter now passes through as a server-side query param. Adapter `adaptAuditToActivity` maps the backend `AuditEvent` shape (including dotted action names like `secret.created`) into the page's existing visual taxonomy with graceful fallbacks for unknown resource types/verbs. Adds proper loading skeleton, error state, and empty state. Removed the v2.0.1 `ComingSoonBanner`. E2E test `dashboard/tests/e2e/activity.spec.ts` now mocks `/api/v1/audit` explicitly with empty + seeded fixtures.

---

## [2.0.1] — 2026-04-29

> **Honesty patch.** Reviews every dashboard page + every website page against shipped reality and marks unshipped features as "Coming soon" with linked issues. No new features; no breaking changes.

### Added
- **Dashboard "Coming soon" badges** — the dashboard now visibly marks features that are scaffolded but not yet wired to real backends. New `<ComingSoonBadge>` component plus per-page top banners on agentops, incidents, compliance, activity, orchestration-builder. Per-feature badges on agent-detail (version compare), gateway (logs / cost compare), settings (Ollama Pull), settings-secrets (backend chooser), models (Local tab), playground (tool-call rendering). Each badge links to the GitHub issue tracking the gap (#206–#216).
- **Page-by-page website audit** — every docs page, homepage component, and blog post audited against shipped code. Unshipped features re-scoped as "Coming soon" with linked issues. Notable rewrites: 5-layer architecture step 5 ("Pulumi" → "Cloud SDK calls"), runtime-contract codegen scope (Go ships, Kotlin/Rust/.NET roadmap), sidecar auto-injection scope (compose/Cloud Run/ECS today; Azure/App Runner/K8s deferred), secrets auto-mirror scope (AWS ECS + GCP Cloud Run today), agent-yaml `gateways:` block now documented.
- **Migrations doc page** at `website/content/docs/migrations.mdx` — fixes a 404 in the docs nav and explains the v1.x → v2.0 upgrade path.
- **v2.0 launch blog post** at `website/content/blog/v2-platform-substrate.mdx` with an honest "what didn't ship yet" section.
- **Homepage feature cards for v2 tracks** — Provider Catalog, Sidecar, Workspace Secrets, Polyglot Runtime Contract, Gateways added alongside the v1 features.

### Tracked gaps (filed during the audit)

Backend / infra: #196–#205. Dashboard data wiring: #206–#216. Release infra: #195.

---

## [2.0.0] — 2026-04-29

> **Platform v2.** Six new tracks turn AgentBreeder into a substrate where frameworks, clouds, languages, and providers all plug in. See `docs/architecture/platform-v2.md` and the epic at #166.

### Added
- **Gateways as first-class providers — LiteLLM + OpenRouter** (#164, Track H): the catalog schema now distinguishes `type: openai_compatible` (one upstream) from `type: gateway` (many upstreams). `engine/providers/catalog.yaml` ships `litellm` and `openrouter` as built-in `gateway` presets, and `model.primary` accepts a 3-segment ref `<gateway>/<upstream>/<model>` (e.g. `openrouter/moonshotai/kimi-k2`, `litellm/anthropic/claude-sonnet-4`) — parsed by the new `engine.providers.catalog.parse_gateway_ref`. The wire-level `model` field is shaped as `<upstream>/<model>` so both LiteLLM and OpenRouter accept it as-is. New optional `gateways:` block on `agent.yaml` lets you override the catalog `url` / `api_key_env` / `fallback_policy` / `default_headers` per-gateway (the long-term home is `workspace.yaml` once Track A / #146 ships). The dashboard `/models` page now has a working **Gateways** tab — same Configure flow as Direct providers, with a small `gateway` badge per row. Backwards-compat: existing 2-segment direct refs (`nvidia/llama-…`) and `model.gateway: litellm` configs keep working unchanged. New docs page at `website/content/docs/gateways.mdx` covering when to pick which gateway and how 3-segment refs route end-to-end.
- **Model lifecycle — auto-discover, status, retire** (#163): `engine/providers/discovery.py` ships per-provider `/models` fetchers (OpenAI-compatible, curated Anthropic, Google `v1beta/models`) behind a `ProviderDiscovery` Protocol. New Alembic migration `018_add_model_lifecycle_fields.py` adds `discovered_at`, `last_seen_at`, `deprecated_at`, `deprecation_replacement_id` to `models` plus a `(status, last_seen_at)` index. New `registry/model_lifecycle.py` reconciles discovery output with the registry: new models become `active`, absent ones flip to `deprecated`, and after 30 days of continuous absence they `retired`. Per-provider discovery errors are isolated so a transient outage cannot mass-deprecate. Audit events `model.added` / `model.deprecated` / `model.retired` emit on every transition. New CLI: `agentbreeder model list / show / sync / deprecate`. New API: `GET /api/v1/models` (lifecycle-aware list), `POST /api/v1/models/sync` and `POST /api/v1/models/{name}/deprecate` (deployer-gated). Dashboard `/models` page Sync button is live (RBAC-aware, with a spinner) and rows show coloured status badges (`active`/`beta`/`deprecated`/`retired`). Daily cron is left as a TODO documented under `website/content/docs/providers.mdx#daily-cron-out-of-scope-for-this-pr` — for now operators add a system cron.

### Fixed
- **Local stack UX**: compose now wires `GOOGLE_API_KEY`, `LITELLM_BASE_URL`, `LITELLM_MASTER_KEY`, `AGENTBREEDER_INSTALL_MODE=team` into the API container. `/playground` and `/api/v1/secrets` now work out of the box on `docker compose up`.
- **LiteLLM trampling agentbreeder DB**: `STORE_MODEL_IN_DB: False` so LiteLLM's prisma migrations don't run against the shared database (which was wiping the `users` table). Stateless mode is fine for the playground; re-enable with a separate DB when virtual keys / DB-stored configs are needed.
- **`gemini-2.5-flash` not in LiteLLM config**: added to `deploy/litellm_config.yaml` (also fixed `GOOGLE_AI_API_KEY` → `GOOGLE_API_KEY` typo on `gemini-2.5-pro`).
- **Deploy → registry sync 401 (`endpoint_url` empty after deploy)**: `engine/builder.py` now attaches `AGENTBREEDER_API_TOKEN` as a Bearer header when set, so the post-deploy `PUT /api/v1/agents/{id}` succeeds and the registry record gets a real `endpoint_url` for cloud deploys.

### Added
- **`/playground` agent mode — chat with a deployed agent** (#177): the dashboard playground gets a Model | Agent tab toggle. Agent mode shows a dropdown of registered agents that have a non-empty `endpoint_url` and routes chats through `POST /api/v1/agents/{id}/invoke` (which auto-resolves `AGENT_AUTH_TOKEN` server-side from the workspace secrets backend per #176 — no token UI). Conversation history is currently sent as a single concatenated `input` string with role labels; `session_id` round-trips between turns. Mode preference persists in `localStorage`. Empty workspaces (no deployed agents) get a "Deploy one with `agentbreeder deploy`" empty state with a link to `/agents`. Model mode behaviour unchanged.
- **Track I phase 1 — Go SDK + runtime builder + example agent** (#165, Go scope only): new `sdk/go/agentbreeder/` module is the first Tier-2 polyglot SDK. Implements [Runtime Contract v1](engine/schema/runtime-contract-v1.md): `NewServer(InvokeFunc, …Option)` returns a chi-based `http.Handler` that auto-wires `/health`, `/invoke`, `/stream`, `/resume`, `/openapi.json`, `/.well-known/agent.json`, bearer-token auth via `AGENT_AUTH_TOKEN`, the `X-Runtime-Contract-Version: 1` header, and the SSE `[DONE]` terminator. Hand-curated types in `types.go` mirror `engine/schema/runtime-contract-v1.openapi.yaml` (oapi-codegen regen command in the SDK README). Includes a `Client` for the central registry (agents, models, secrets). New `engine/runtimes/go/` Python builder packages Go agents with a multi-stage `golang:1.22-alpine` → `gcr.io/distroless/static` Dockerfile. New `examples/go-agent/` minimal agent talks to Anthropic via `net/http` (mock-falls-back when no API key). New CLI flag `agentbreeder init --lang go --framework custom` scaffolds a working Go project. `agent.yaml` schema accepts `language: go|kotlin|rust|csharp` (only `go` wired in the runtime registry; the rest are reserved). New CI jobs `test-go-sdk` (≥85% coverage gate) and `test-go-example`; release.yml publishes `rajits/agentbreeder-go-agent-example` on each version tag. New docs page `website/content/docs/go-sdk.mdx`. Kotlin (#188) / Rust (#189) / .NET (#190) SDKs are deferred to follow-up issues.
- **Generic OpenAI-compatible provider + 9-preset catalog** (#160): `engine/providers/openai_compatible.py` parameterised by `base_url`/`api_key_env`/`default_headers` replaces what would have been 9 hand-written classes. New `engine/providers/catalog.yaml` ships nvidia, openrouter, moonshot (Kimi K2), groq, together, fireworks, deepinfra, cerebras, hyperbolic — merged with `~/.agentbreeder/providers.local.yaml` overrides at load time. New CLI: `agentbreeder provider list/add/remove/test/publish`. New API route `GET /api/v1/providers/catalog`. Dashboard `/models` page lists catalog presets with a Configure stub. `model.primary: nvidia/<model>` resolves through the existing engine path.
- **Sidecar — cross-cutting concerns layer** (#161): single Go binary auto-injected next to every agent that declares `guardrails:`, MCP `tools:`, or `a2a:`. Fronts the agent on `:8080` (bearer-token auth + guardrail egress checks → reverse proxy to `:8081`) and exposes `localhost:9090` helpers for A2A JSON-RPC, MCP HTTP/SSE passthrough, and cost emission. Auto-injection wired into docker-compose, GCP Cloud Run, and AWS ECS deployers. `AGENTBREEDER_SIDECAR=disabled` bypasses for local dev. New top-level `sidecar/` Go module (~89% test coverage), Dockerfile, image build target `rajits/agentbreeder-sidecar:<version>`, and docs at `website/content/docs/sidecar.mdx`.
- **Secrets — workspace-bound backend + auto-mirror to cloud at deploy** (#162): new `engine/secrets/keychain_backend.py` (cross-platform via `keyring`) plus `engine/secrets/workspace.py` for per-workspace backend selection, defaulting keychain locally / Vault for self-hosted teams / AWS Secrets Manager in cloud. New CLI `agentbreeder secret set/list/rotate/sync` routes through the workspace backend with `getpass` prompts and audit events. AWS ECS + GCP Cloud Run deployers now auto-mirror declared `secrets:` to the target cloud's native store under `agentbreeder/<agent>/<secret>`, grant the runtime SA `secretAccessor` on each, and inject them as ECS `secrets` / Cloud Run `SecretKeyRef` env vars (no plaintext in image). New dashboard `/settings/secrets` page (values never leave the backend) and 3 REST endpoints under `/api/v1/secrets/*`.

### Fixed
- **Invoke tab no longer prompts for `AGENT_AUTH_TOKEN`** (#176): `POST /api/v1/agents/{id}/invoke` now resolves the bearer token server-side from the workspace secrets backend keyed by `agentbreeder/<agent-name>/auth-token`. The dashboard's `<InvokePanel>` drops the password input + `token` state and shows a hint pointing users at `agentbreeder secret set <agent>/auth-token`. The request body's `auth_token` field is preserved as an optional explicit override (e.g. for SDK callers and tests). Backend lookup failures fall through to "no token" — runtime returns 401, no 500s. Documented under "Per-agent auth tokens" in `website/content/docs/secrets.mdx`.
- **`/models` page UX gaps on top of Track F** (#175):
  - `<ProviderCatalog>` now reads the user's role via `useAuth()` and disables the **Add provider** + per-row **Configure** buttons for viewers with a "Requires deployer role" tooltip. RBAC is enforced server-side too — the UI checks are pure UX.
  - **Configure** is no longer a stub. Clicks open a dialog with a password-typed input that POSTs to the new `POST /api/v1/secrets` route under the deterministic key `<provider>/api-key`, writing through the workspace secrets backend (Track K). On success the row flips to a green ✓ **Configured** badge and a success toast fires; 401/403 surface as a permission-denied toast.
  - New `GET /api/v1/providers/catalog/status` returns `{<provider_name>: bool}` so each catalog row can show its real configuration state. The status check covers both the deterministic dashboard key and the legacy env-var name, so secrets imported via the CLI before Track K landed are still recognised.
  - **Sync** button + **Direct providers / Gateways / Local** tabs scaffolded. Sync is disabled with a tooltip pointing at Track G (#163), Gateways at Track H (#164), Local at the future local-runtimes track. The PRs for those tracks just enable the existing affordances.
- **MDX deployment-doc parser failure**: escape `<1 sec` in `website/content/docs/deployment.mdx` (Turbopack/MDX parses `<1` as start of a JSX tag). Was blocking Vercel preview builds on every PR.
- **E2E API smoke `.local` TLD rejected**: switch test email to `@example.com` (RFC 2606); pydantic `email-validator` now rejects `.local` as a special-use TLD, breaking the Docker E2E smoke on every PR.
- **Integration tests / Docker builds**: removed `@agentbreeder/aps-client` npm dep (package not yet published); vendor `aps-client.ts` source directly into Node.js Docker build context so `npm install` no longer fails with 404
- **ESLint errors**: suppressed `react-hooks/set-state-in-effect` errors in `gateway.tsx`, `incidents.tsx`, `prompt-builder.tsx`; fixed root cause in `login.tsx` by initializing `mounted=true` (removes invisible form on first render — fixes E2E login tests)
- **CI gate**: integration tests (`tests/integration/`) now run alongside unit tests in the `test-python` CI job
- **Mocked E2E tests in CI**: switch webServer from `vite dev` (per-request ESM compilation) to `vite build && vite preview` (pre-built static bundle) — eliminates Vite server overload from concurrent workers on slow Ubuntu runners; raise `expect.timeout` to 15 s and seed auth token via `addInitScript` to remove a redundant `/login` page-load per test
- **CI E2E mocked job removed**: removed `test-e2e-mocked` job from CI workflow — mocked E2E tests were consistently failing on GitHub Actions 2-vCPU runners regardless of timeout and server configuration tuning; tests remain available for local execution (`npx playwright test` in `dashboard/`); `docker-build` job no longer depends on them

---

## [1.8.0] — 2026-04-16

### Added

#### Ollama / LiteLLM Support Across All Runtimes (#63, #64, #65, #66)
- **LangGraph, OpenAI Agents, Custom runtimes**: `litellm>=1.40.0` added to requirements when `model: ollama/*`; `OLLAMA_BASE_URL` injected into Dockerfile ENV block
- **OpenAI Agents server**: startup routes to Ollama's OpenAI-compatible endpoint (`AsyncOpenAI(base_url=OLLAMA_BASE_URL/v1)`) for `ollama/` models; standard path unchanged
- **CrewAI runtime + server**: `litellm>=1.40.0` dep for Ollama models; `agent.llm.base_url` set to `OLLAMA_BASE_URL` at startup
- **Google ADK runtime + server**: `SERVER_LOADER_CONTENT` and lifespan both use `LiteLlm(model, api_base=OLLAMA_BASE_URL)` for non-Gemini models; resolves #66 (no `agent.py` workaround needed)
- **Claude SDK validation**: `validate()` rejects `ollama/*` models with a clear error pointing to compatible frameworks
- **DockerComposeDeployer**: auto-starts `ollama/ollama` sidecar, creates `agentbreeder-net` Docker network, runs `ollama pull` before agent starts — resolves #64, #65

### Fixed
- **`Dockerfile.cli` version pin**: uses `ARG VERSION` + `pip install "agentbreeder==${VERSION}"` — eliminates race condition between parallel build-images and publish-pypi CI jobs

### Examples
- `examples/ai-news-digest/`: Google ADK + Ollama Gemma3:27b daily news digest — fetches HN, ArXiv, RSS; synthesises with Gemma; emails via Gmail SMTP

---

## [1.7.0] — 2026-04-14

### Added

#### Agent Architect Skill (`/agent-build`) — M35
- `/agent-build` Claude Code skill: AI-powered agent architect with two paths — Fast Path (6-question scaffold) and Advisory Path (6-question interview → full-stack recommendations → scaffold)
- Advisory Path recommendation engine: framework, model, RAG, memory, MCP/A2A, deployment target, and eval dimensions with reasoning in `ARCHITECT_NOTES.md`
- Advisory Path scaffold generates 19 files including `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, `.antigravity.md`, `memory/`, `rag/`, `mcp/servers.yaml`, `tests/evals/`
- Fast Path scaffold generates 10 files (core project, no advisory extras)
- IDE config file contents tailored to chosen framework, model, and deployment target
- Skill entry added to `AGENT.md` under Build category

#### Documentation
- Homepage animation: split-screen autoplay demo in `docs/index.md` showing `/agent-build` advisory flow (invoke → interview → recommendations → scaffold → deploy, ~14s loop)
- `docs/how-to.md`: `/agent-build` lead paragraph in "Build Your First Agent" section
- `docs/how-to.md`: new "Use the Agent Architect (`/agent-build`)" section with Fast Path walkthrough, Advisory Path walkthrough, 18-row generated-files table, and next-steps commands
- `CLAUDE.md`: added item 8 to "When Adding a New Feature" — scaffold with `/agent-build`

---

## [1.5.0] — 2026-04-13

### Added

#### Framework Parity (CrewAI, Claude SDK, Google ADK)
- `crewai_server`: `/stream` SSE endpoint with `akickoff` step streaming; `_detect_mode`/`_dispatch`/`_validate_output` helpers; `output_schema_errors` field on `InvokeResponse`; `AGENT_MODEL`/`AGENT_TEMPERATURE` env-var wiring for per-agent LLM config
- `claude_sdk_server`: `_call_client` with adaptive thinking and prompt-caching support; `_get_cache_threshold`; `_prompt_caching_enabled`/`_thinking_config` module globals; `/stream` SSE endpoint; `_client` wired from `AsyncAnthropic` at startup
- `google_adk_server`: streaming and tool-wiring parity aligned with Claude SDK and CrewAI servers
- Engine runtimes: Claude SDK requirements bump to `anthropic>=0.50.0`; CrewAI/ADK runtimes inject `AGENT_MODEL`/`AGENT_TEMPERATURE` into container Dockerfile
- `engine/tool_bridge`: fixed `sys.modules` lookup order so test stubs are resolved correctly
- Eject scaffolds for CrewAI, ADK, and Claude SDK agent tiers (`cli/commands/eject.py`)

#### A2A Protocol (M19)
- A2A JSON-RPC 2.0 engine: protocol handler, server, client, JWT-based inter-agent auth (`engine/a2a/`)
- Agent Card generation from `agent.yaml` configuration
- Auto-generated `call_{agent_name}` tools from `subagents:` declarations (`engine/a2a/tool_generator.py`)
- A2A API routes: discovery, invoke, agent card management (`api/routes/a2a.py`)
- A2A registry service for agent registration and lookup (`registry/a2a_agents.py`)
- A2A dashboard: topology graph, call log, agent detail page (`dashboard/src/pages/a2a-*.tsx`)
- Multi-agent orchestration patterns: supervisor, fan-out/fan-in, chain (`engine/orchestrator.py`)
- Orchestration examples: `examples/orchestration/supervisor/`, `examples/orchestration/fan-out-fan-in/`
- A2A subagent example: `examples/a2a-subagent/`

#### MCP Server Hub (M20)
- MCP server packaging and lifecycle management (`engine/mcp/packager.py`)
- MCP sidecar deployer for co-deploying MCP servers with agents (`engine/deployers/mcp_sidecar.py`)
- Enhanced MCP server registry with versioning and sharing (`registry/mcp_servers.py`)
- MCP server detail page and API routes (`api/routes/mcp_servers.py`, `dashboard/src/pages/mcp-server-detail.tsx`)

#### Visual Orchestration Canvas (M30)
- ReactFlow-based orchestration builder with agent, router, supervisor, and merge nodes (`dashboard/src/components/orchestration-builder/`)
- Routing rule editor, strategy selector, and orchestration-to-YAML generator
- Orchestration builder page (`dashboard/src/pages/orchestration-builder.tsx`)

#### TypeScript SDK (M30)
- `@agentbreeder/sdk` npm package (`sdk/typescript/`)
- Agent, Tool, Model, Orchestration, and Deploy classes with full TypeScript types
- `toYaml()` / `fromYaml()` serialization
- Unit tests for agent and orchestration SDK

#### Template System (M21)
- Parameterized agent configuration templates with `{{placeholder}}` substitution
- Template schema (`engine/schema/template.schema.json`) with full JSON Schema validation
- Template CRUD API: create, list, get, update, delete, instantiate (`api/routes/templates.py`)
- Template registry service (`registry/templates.py`)
- Template gallery page with category filters (`dashboard/src/pages/templates.tsx`)
- Template detail page with parameter form and YAML generation (`dashboard/src/pages/template-detail.tsx`)
- `agentbreeder template list|create|use` CLI commands (`cli/commands/template.py`)
- Built-in templates: Customer Support Bot, Data Analyzer, Code Reviewer, Research Assistant (`examples/templates/`)
- Template versioning support

#### Marketplace (M22)
- Marketplace browse API with search, category/framework filters, sorting (`api/routes/marketplace.py`)
- Marketplace registry service with listing submission, approval, reviews (`registry/templates.py`)
- Marketplace browse page with search, filters, star ratings (`dashboard/src/pages/marketplace.tsx`)
- Marketplace detail page with reviews, one-click deploy, install tracking (`dashboard/src/pages/marketplace-detail.tsx`)
- Ratings & reviews system: star rating + text reviews per listing
- Listing approval workflow: submit → admin review → approve/reject
- One-click deploy from marketplace listing (install count tracking)
- Featured listings support
- Marketplace navigation section in dashboard sidebar

#### Other
- `agentbreeder eject --sdk typescript` support (`cli/commands/eject.py`)
- `subagents:` field in `agent.yaml` schema (`engine/schema/agent.schema.json`)
- Orchestration schema updates for fan-out/fan-in and supervisor patterns
- GitHub Actions CI/CD workflows: security scanning, integration tests, release automation
- Branch protection setup script (`scripts/setup-branch-protection.sh`)
- Dependabot configuration for automated dependency updates
- CODEOWNERS file for automatic PR reviewer assignment
- Stale issue/PR bot
- Gitleaks secret scanning
- Trivy container image scanning
- Bandit Python SAST
- pip-audit and npm audit dependency vulnerability scanning
- Dependency Review action (blocks PRs introducing high-severity dependencies)

### Changed
- Extended orchestrator engine with supervisor, fan-out/fan-in, and chain patterns
- Updated config parser and resolver for A2A subagent resolution
- Enhanced orchestration YAML parser for new pattern types

---

## [1.4.0] — 2026-04-12

### Added

#### Package Distribution (M32)
- PyPI: `pip install agentbreeder` (CLI + API server + engine) and `pip install agentbreeder-sdk` (lightweight SDK)
- npm: `npm install @agentbreeder/sdk` — TypeScript SDK published to the npm registry
- Docker Hub: multi-platform images (`linux/amd64` + `linux/arm64`) for API server, dashboard, and CLI
  - `rajits/agentbreeder-api` — FastAPI backend
  - `rajits/agentbreeder-dashboard` — React frontend
  - `rajits/agentbreeder-cli` — lightweight CLI image for CI/CD pipelines
- Homebrew: `brew tap agentbreeder/agentbreeder && brew install agentbreeder` via `Formula/agentbreeder.rb` (Python virtualenv pattern)

#### Release Automation
- `.github/workflows/release.yml`: single workflow publishes to all four distribution channels on each tagged release
- PyPI publishing via OIDC trusted publishers — no long-lived API tokens required
- Homebrew formula auto-updated on every release via the `agentbreeder/homebrew-agentbreeder` tap repo

### Changed
- Both Python packages (`agentbreeder` and `agentbreeder-sdk`) now derive their version from git tags using `hatch-vcs` — no more manual version bumps

### Infrastructure
- CI test matrix expanded to Python 3.11 and 3.12
- Codecov configuration fixed (`fail_ci_if_error`) for accurate coverage reporting across the matrix

---

## [1.0.0] — 2026-03-12

### Added
- Evaluation framework (M18) with dashboard and CI/CD integration
- Quality gates for agent evaluation
- Orchestration YAML (M29) for multi-agent workflows
- Observability stack: distributed tracing, OpenTelemetry integration
- Teams and RBAC enforcement
- Cost tracking and audit/lineage trail
- Python SDK (`agentbreeder-sdk`)
- Visual playground (No Code builder)
- Git CLI workflow (`agentbreeder submit`, `agentbreeder review`, `agentbreeder publish`)
- YAML schemas for `agent.yaml`, `orchestration.yaml`, `prompt.yaml`, `tool.yaml`, `rag.yaml`, `memory.yaml`
- MCP server example and scanner connector
- Builders API (Low Code and Full Code endpoints)
- Three-tier builder model: No Code / Low Code / Full Code

### Framework Support
- LangGraph
- OpenAI Agents
- CrewAI
- Google ADK
- Custom (bring your own framework)

### Deployment Targets
- AWS ECS Fargate
- GCP Cloud Run
- Local Docker Compose

---

[Unreleased]: https://github.com/agentbreeder/agentbreeder/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/agentbreeder/agentbreeder/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/agentbreeder/agentbreeder/compare/v1.0.0...v1.4.0
[1.0.0]: https://github.com/agentbreeder/agentbreeder/releases/tag/v1.0.0
