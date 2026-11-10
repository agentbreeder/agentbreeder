# Installable preview release: `agentbreeder==3.0.0a1` on PyPI and `npx agentbreeder`

- Status: **r2, APPROVED by the owner 2026-11-09.** Design-reviewed (architecture/install-UX,
  supply-chain security). Owner prerequisites §3.5 open; they block publishing, not code.
- Epic: #751. Slices: #752 packaging and PyPI, #782 npx launcher, #753 2.x notice, #755 install
  docs and website, #754 clean-machine verification.
- Follow-on epic: #783 (`agentbreeder up`: local command center, services, registries; Northwind
  onboard → build → release → deploy to local Docker, then GCP Cloud Run). Owner decision
  2026-11-09: #751 ships first. This spec adds only what #783 needs from packaging (§5.4, §5.5).
- Dates: r1 and r2 2026-11-09 (§10).

## 1. Problem

The rebuild installs only from a source checkout. PyPI `agentbreeder` holds 15 earlier releases
(0.1.0, five 1.x, nine 2.x; latest 2.7.3, none yanked), which are unmaintained and have known
security issues. npm has no `agentbreeder` package; the name is free. Nobody can try the product
without cloning it.

Measured on 2026-11-09 from `main` (07afde7), building a wheel and installing it with pip into a
clean Python 3.12 venv outside the repo:

- The wheel builds (146 files, 877 KB) and installs with two dependencies (pydantic, PyYAML).
- `registry list` finds all 14 built-in versions in site-packages; `registry verify` and `doctor`
  pass. `verify` checks structure and manifest/path agreement only; it compares hashes to nothing.
- **Defect:** the wheel contained a stale `…/mcp-relay/1.0.0/relay/__pycache__/relay.cpython-312.pyc`
  from the working tree (package-data glob `builtin_registry/**/*`).
- **Defect (review):** installing with uv's hardlink mode, uv's default on Linux, makes
  `registry verify` reject every built-in version (`ENTRY_UNSAFE hardlinks are not allowed`,
  `registry.py:120`). `uvx` and `npx agentbreeder` would fail on Linux.
- **Gaps:** `examples/` is not in the wheel. The quickstart (`examples/http-agent/quickstart.py`)
  imports `agentbreeder` and runs `sys.executable -m agentbreeder`, which a uvx/npx user cannot
  do. Northwind's eval gate needs the `[eval]` extra (psycopg; `evals.py:268`).

## 2. Goal

A user with Docker plus either Python 3.11+ (pip or uv) or Node 18+ with uv (uv fetches Python
itself) installs a pinned preview, runs the quickstart, and then runs Northwind to its 61/61 gate,
on clean macOS and Linux machines. The steps and time are measured and published. 2.x users are
never moved onto the rebuild without asking.

## 3. Decisions

### 3.1–3.4 Made by the owner on 2026-11-09

1. **Version `3.0.0a1`**, a PEP 440 pre-release. pip and uv skip pre-releases unless asked, so
   `pip install -U agentbreeder` leaves a 2.x user on 2.7.3. Every documented command pins the
   exact version (`agentbreeder[eval]==3.0.0a1`), never `--pre`, which would also admit
   pre-releases of dependencies and float to later alphas. Docs warn that bare
   `pip install agentbreeder`, `uvx agentbreeder` and `pipx run agentbreeder` still install
   2.7.3.
2. **Examples ship in the wheel**, copied out by `agentbreeder example <name> [DIR]`.
3. **npx launcher runs uv**; when `uvx` is missing it prints the official install command and
   exits 127. It never downloads uv.
4. **npm version mapping:** PyPI `3.0.0a1` ↔ npm `3.0.0-alpha.1` (`3.0.0a1` is not valid
   semver). A test checks the mapping. npm publishes with `--tag latest`, since a pre-release
   without it is refused and bare `npx agentbreeder` would not resolve to it.

### 3.5 Owner prerequisites (blocking the first publish; the plan checks each through the API)

Verified 2026-11-09: environment `pypi` exists with no protection rules and admin bypass on; the
repo has no rulesets, so tags are unprotected; `NPM_TOKEN` is a repo-level secret readable by any
workflow. The 2.x release workflow published `agentbreeder` and `agentbreeder-sdk` from
`environment: pypi` through OIDC. Whether that PyPI trusted publisher is still registered has not
been verified, and only the owner can check it.

1. `pypi` environment: required reviewer (owner), admin bypass off, deployments limited to `v3.*`
   tags. New `testpypi` and `npm` environments with the same rules.
2. A tag ruleset restricting creation, update and deletion of `v*` to the owner.
3. On PyPI: audit trusted publishers and API tokens for `agentbreeder` and `agentbreeder-sdk`;
   remove stale ones; register the new workflow (`release.yml`, environment `pypi`). Register a
   TestPyPI publisher (environment `testpypi`).
4. npm: the owner publishes `3.0.0-alpha.1` manually with 2FA (a package-scoped token cannot exist
   before the package does), then configures npm trusted publishing for `release.yml`. The
   repo-level `NPM_TOKEN` is deleted or rotated.
5. Optional, recommended: defensive placeholder names on npm (`agent-breeder`, `agentbreeder-cli`,
   `@agentbreeder/cli`) and PyPI (`agent-breeder`, `agentbreeder-cli`), all free on 2026-11-09.

## 4. Non-goals

- `agentbreeder up`, the command center, Cloud Run: epic #783.
- A final `3.0.0` and any stability promise for the CLI or file formats.
- Homebrew, conda or Docker-image distribution of the CLI. Windows (documented as not verified).
- Yanking 2.x (notices only, #753).
- Tamper-evidence. The shipped hash manifest detects corruption, not tampering: anyone who can
  change the installed files can change the manifest too.

## 5. Design

### 5.1 Packaging (#752)

- **Version source:** `agentbreeder.__version__`, read by setuptools (`dynamic = ["version"]`).
  Tests: CLI `--version` == metadata == release tag (in the workflow) == npm mapping (§3.4).
- **Extras:** the documented install is `agentbreeder[eval]`, so the Northwind gate works. psycopg
  stays an extra, so non-eval users don't need the binary wheel.
- **Wheel contents are an allowlist.** A committed `src/agentbreeder/_content.json` lists every
  shipped non-Python file, with its SHA-256 and mode (built-in registry and examples), generated
  by a dev script from `git ls-files` and reviewed in diffs. setuptools package-data lists explicit subtrees
  (plan S2) and the file-list test compares the wheel with it, so dotfiles such as `northwind/stack/.dockerignore` are included explicitly and
  ignored files (`examples/northwind/.agentbreeder/`, `deploy/`, `stack/.secrets/`) can never
  ship. A test builds the wheel **and the sdist** and asserts their file lists exactly equal the
  expected set (Python modules + `_content.json` entries + metadata). The release build also fails
  on a dirty tree.
- **Corruption check:** `registry verify` (output renamed "built-in content") compares installed
  built-in files against `_content.json`. A test asserts that existing built-in versions never
  change their hashes or modes, since locks such as Northwind's `runtime/adk@1.0.6` depend on that.
  Docs call this a corruption check, never integrity or provenance.
- **Hardlinks:** the `st_nlink > 1` refusal stays for project and user registry roots. For the
  installed built-in root and shipped examples it is replaced by the `_content.json` comparison
  (hash + mode + regular file, no symlinks), because package installers legitimately hardlink from
  their cache. A test installs the wheel with `UV_LINK_MODE=hardlink` and runs `registry verify`,
  `example northwind` and `build --locked`.
- **Metadata:** a one-line description of what ships; `Development Status :: 3 - Alpha`; project
  URLs to the repo, the rebuild docs and the Legacy 2.x page.
- **Reproducibility (wording: "two builds on the same runner match"):** pinned, hash-locked build
  dependencies (`setuptools`, `build`), `SOURCE_DATE_EPOCH` from the tag commit, fixed umask. Two
  builds must produce byte-identical wheels and sdists with identical contents (setuptools does
  not apply `SOURCE_DATE_EPOCH` to sdist tar entries, so sdist archive bytes differ). This proves determinism, not that the build tools were
  honest; provenance attestation (§5.2) covers where it was built.

### 5.2 Release workflow (#752; build/deploy config, so it takes the full gate)

`.github/workflows/release.yml`, triggered only by `v3.*` tags. Actions pinned by commit SHA; no
caches; `persist-credentials: false`; no `pull_request_target`; `${{ github.ref_name }}` never
interpolated into `run:` (passed through `env:`).

1. **verify** (`contents: read`): fail unless the tag commit is an ancestor of `origin/main` and
   the tag equals `v` + `__version__`. Full test suite.
2. **build** (`contents: read`): build sdist and wheel twice, compare, run the allowlist test, and
   smoke-install the wheel with pip and with `uv` in hardlink mode (`--version`, `registry verify`,
   `example northwind`, `validate`, `quickstart --dry-run` if §5.5 provides one). Outputs the
   sha256s as job outputs and uploads the artifacts.
3. **attest** (`id-token: write`, `attestations: write`): download the artifacts, check the
   sha256s, `actions/attest-build-provenance`.
4. **publish-testpypi** (environment `testpypi`, `id-token: write`) → **publish-pypi**
   (environment `pypi`, owner approval) → **publish-npm** (environment `npm`, owner approval,
   `npm publish --provenance --tag latest`). Each job downloads the artifacts, re-checks the
   sha256s and uploads. No build tool runs in a job that has `id-token: write`.

A Linux CI job on every PR (in `ci.yml`) runs the uv-hardlink install smoke test, so a #754-class
failure is caught before the tag.

### 5.3 npx launcher (#782)

- `npm/` holds an unscoped package `agentbreeder`: one dependency-free `bin/agentbreeder.js`, a
  `package.json` with `bin`, `engines.node >= 18`, no `install`/`postinstall` scripts, and
  `constraints.txt`: the exact dependency versions the release was tested with.
- It runs `uvx --exclude-newer <release timestamp> -c <constraints.txt> --from
  agentbreeder[eval]==<pypi version> agentbreeder <args...>`. `--exclude-newer` stops a cold
  run from resolving dependency releases, or files added to existing versions, uploaded after
  the release. That bounds what a given npm version runs; it does not pin hashes. Hashes are
  enforced only on the documented `uv pip install --require-hashes -r <release requirements>`
  path, which uses a hashed requirements file attached to the GitHub release.
- **Finding `uvx`:** resolve it to an absolute path from `PATH` after removing entries that
  contain a `node_modules` segment (or whose resolved file is under `node_modules`), and refuse
  a match that comes from the current directory: an empty or relative `PATH` entry, or the
  current directory itself (r10; absolute entries below it, e.g. `~/.local/bin` from `$HOME`,
  are allowed). On a terminal, SIGINT is left to the terminal, which already signals uvx. Spawn with
  `shell: false` and an argv array; never fall back to a shell (Windows `.cmd` shims are
  unsupported, and Windows is documented as not verified). Forward stdio, SIGINT/SIGTERM and the
  exit code.
- **Environment:** passed through unchanged; the launcher adds nothing. Docs state that uv honours
  the user's index settings (`UV_INDEX_URL`, `UV_DEFAULT_INDEX`, `UV_EXTRA_INDEX_URL`, `UV_INDEX`,
  `uv.toml`), which is how corporate mirrors and the TestPyPI rehearsal (§5.6) work, and that the
  exact pin is then satisfied by that index.
- `npx agentbreeder --launcher-version` prints the npm and PyPI versions, the resolved `uvx` path,
  and a warning when a uv variable that changes the source or resolution is set (index variables,
  `UV_INDEX_STRATEGY`, `UV_PRERELEASE`, `UV_OVERRIDE`, `UV_CONSTRAINT`, `UV_CONFIG_FILE`; r10).
  It does not start Python.
- Tests (node's built-in test runner, in CI): a stub `uvx` on `PATH` records its argv and env;
  assertions on the exact argv, on the `node_modules` and current-directory refusals, on exit 127
  with install guidance when `uvx` is missing, and on signal and exit-code forwarding.

### 5.4 Examples in the wheel (#752; prerequisite for #783)

- r3 (plan review): the examples **move** to `src/agentbreeder/examples/` with explicit package-data
  subtrees. Keeping them at the repo root needed a custom `build_py` hook that setuptools rejects in
  `pyproject.toml` and that breaks editable installs and sdist→wheel builds (reproduced). The
  allowlist test, the clean-checkout release build and the dirty-tree check keep ignored local files
  out. The repo-root `examples/README.md` points to the new location.
- `agentbreeder example list` and `agentbreeder example <name> [DIR]` (default `./<name>`). `DIR`
  must not exist, or must be an empty real directory (checked with `lstat`, not a symlink). The
  copy reads each manifest-listed file with `O_NOFOLLOW` and writes with exclusive create into a
  hidden staging directory inside the destination, then moves the entries into place (r7: renaming
  a sibling over an empty destination replaced the caller's working directory); it skips `__pycache__` and writes modes `0644`/`0755` from
  the exec bit in `_content.json` (installs under umask 077 give 0600/0700, so only the exec bit
  is compared). The source is checked against `_content.json` first.
- Tests: the copied Northwind validates (`agent.yaml`, `stack.yaml`) and `build --locked` passes
  from an installed wheel (`agent.lock` records `source: builtin` and project-relative paths, so it
  survives the copy).
- The plan enumerates every reference to update: `ci.yml`, ruff `extend-exclude`, `MANIFEST.in`,
  six test files, README, 12 doc pages, and the website (`index.mdx`, `local-deployment.mdx`,
  `services.mdx`, `agent-yaml.mdx`, `hero.tsx`).

### 5.5 `agentbreeder quickstart` (#752)

The quickstart logic moves from `examples/http-agent/quickstart.py` into the package as
`agentbreeder quickstart`, with the same five steps and output. It uses the in-process engine
instead of `sys.executable -m agentbreeder`. The script stays as a one-line wrapper for checkout
users. This is the documented first command on every install path, and #783's `up` builds on it.

### 5.6 2.x notice (#753)

- The PyPI long description (frozen per release, so it merges before the tag) opens with: this is
  a rebuild, not a 2.x upgrade; 2.x users pin `agentbreeder<3`; bare `pip install agentbreeder`
  still installs 2.7.3, which is unmaintained with known security issues; link to Legacy 2.x.
- `doctor` reports when 2.x state is present in `~/.agentbreeder` (markers taken from the
  `v2.7.3` tag in the plan) and says 3.x neither reads nor changes it. Acceptance: no 3.x path
  (`~/.agentbreeder/local`, `stacks/`, `operator.yaml`, `oidc-token`) collides with a 2.x path;
  if any does, 3.x moves to a subdirectory before the release.

### 5.7 Clean-machine verification and measured effort (#754)

- Machines: a fresh macOS user account with Docker Desktop; a fresh Ubuntu 24.04 VM with Docker
  Engine; nothing from this repo on either.
- **Rehearsal on TestPyPI** covers pip and uvx with `UV_INDEX`/`--extra-index-url` (dependencies
  come from PyPI). The npx path can only be verified after the real publish. Published versions
  are immutable: a failure after publishing means `3.0.0a2` and yanking `3.0.0a1`.
- Paths: (a) `pip install "agentbreeder[eval]==3.0.0a1"` in a venv; (b) `uvx --from
  "agentbreeder[eval]==3.0.0a1" agentbreeder …`; (c) `npx agentbreeder@3.0.0-alpha.1 …`.
- Steps: install → `doctor` → `quickstart` → `example northwind` → `secret set` for model keys →
  `services up` → `release` to 61/61 → `remove` → `services down`.
- Record each command, its wall time, and every manual step that isn't a command. Publish **admin
  effort** (one-time machine setup) and **developer effort** (installed → Northwind passing).
  Published transcripts redact `GEMINI_API_KEY`, `TAVILY_API_KEY`, `OPENROUTER_API_KEY` and any
  `sk-` value.

### 5.8 Install docs and website (#755), split in two

- **Before the tag:** README install section and PyPI long description with the three exact-pin
  commands, marked "preview; clean-machine verification pending", plus the 2.x warning.
- **After #754 passes:** website install page, quickstart switched to `example`/`quickstart`,
  footer badge `3.0.0a1`, `what-ships.tsx` "Installable preview (PyPI, npm)", README marking
  removed. Cross-repo grep of `agentbreeder-cloud` for version, package name and install lines,
  recorded in §10.

### 5.9 Slice order

#752 code (packaging, `_content.json`, hardlink fix, `example`, `quickstart`, release workflow) →
#753 and #782 (parallel) → pre-tag half of #755 → owner prerequisites §3.5 → tag →
TestPyPI rehearsal → owner approves PyPI publish → owner's manual first npm publish (§3.5.4) →
#754 → post-verification half of #755.

## 6. Acceptance criteria

1. `3.0.0a1` on PyPI and `3.0.0-alpha.1` on npm (dist-tag `latest`), published from CI on a
   protected tag after owner approval, with build-provenance attestations; §3.5 prerequisites
   verified through the API first.
2. Two builds on the same runner give byte-identical wheels and content-identical sdists; wheel and sdist file lists exactly equal
   the expected set.
3. `pip install -U agentbreeder` on a 2.7.3 install leaves 2.7.3 (CI, against PyPI after publish
   and TestPyPI before).
4. All three paths in §5.7 run `quickstart` and Northwind to 61/61 on clean macOS and Linux, with
   uv in its default link mode; admin and developer effort published.
5. Launcher tests in §5.3 pass; `--launcher-version` shows versions and the resolved `uvx` path.
6. Built-in hashes and modes are unchanged across releases; `build --locked` passes on the copied
   Northwind from an installed wheel.
7. No 3.x state path collides with a 2.x path.
8. README, docs, website and footer show only what criterion 4 verified; Cloud repo re-checked.

## 7. Security

- PyPI through OIDC trusted publishing; npm through trusted publishing after the owner's manual
  first publish; no long-lived publish tokens; publishing environments need owner approval and
  only accept `v3.*` tags from `main`.
- Build and publish in separate jobs; `id-token: write` only where needed; artifacts hash-checked
  between jobs.
- The launcher runs nothing at install time, pins the exact PyPI version, and bounds dependency
  resolution with `--exclude-newer` and constraints. Residual risks, documented: the user's
  configured index decides where the pinned version comes from; hashes are enforced only on the
  `--require-hashes` path.
- Wheel contents are an exact allowlist from tracked files, so local secrets next to the examples
  cannot ship. Example copy: no symlink following, no escape, atomic rename, normalised modes.
- Claims: "corruption check", not integrity or tamper-evidence; "two builds on the same runner
  match", not general reproducibility.

## 8. Open questions

- None blocking. Plan decides the exact 2.x state markers (from the `v2.7.3` tag) and whether
  `quickstart` gets a `--dry-run` for the release smoke test.

## 9. Cross-repo

- `agentbreeder-cloud`: grep for `agentbreeder` version pins, `pip install agentbreeder` and
  `0.0.0.dev0` before the tag; record the result here.
- `website/`: §5.8. #783 consumes `example`, `quickstart` and `_content.json`.

## 10. Review log

- r1 2026-11-09: written from a measured wheel build and clean-venv install. Owner accepted all §3
  recommendations (3.0.0a1; examples in the wheel; no uv download).
- r2 2026-11-09: two design reviews by Claude Fable (session model Opus), each finding checked
  against the repo before folding in.
  - Architecture / install UX (14 findings): uv hardlink mode breaks `registry verify` on Linux
    (reproduced by the reviewer; check at `registry.py:120` confirmed) → §5.1 hardlinks + CI job;
    `3.0.0a1` invalid npm semver (confirmed with `semver`) → §3.4; Northwind needs `[eval]`
    (confirmed `evals.py:268`) → extras everywhere; quickstart unusable from uvx/npx (confirmed
    imports) → §5.5; glob drops `.dockerignore` and can ship ignored files → §5.1 allowlist;
    `verify` compares no hashes → corruption-check wording + `_content.json`; `--pre` headline →
    exact pins; TestPyPI can't test npx, versions immutable → §5.7; slice order → §5.9; shared
    `~/.agentbreeder` → §5.6 criterion; unlisted references → §5.4; lock/hash stability →
    criterion 6; reproducibility detail and launcher testability → §5.1, §5.3.
  - Supply chain (10 findings): unprotected `pypi` environment, no tag rules, possibly live 2.x
    publisher (environment and rulesets confirmed via API) → §3.5; `uvx` has no
    `--require-hashes` (confirmed in `uvx --help`), pin claim false → `--exclude-newer` +
    constraints, reworded; repo-level `NPM_TOKEN` (confirmed) → §3.5.4; single-job build+publish →
    §5.2 job split; `uv`/`uvx` PATH hijack via `node_modules/.bin` → §5.3; index inheritance →
    documented, not overridden; denylist and local secrets beside examples (confirmed
    `examples/northwind/.agentbreeder/`) → allowlist; integrity overclaim → wording; example-copy
    TOCTOU → §5.4; facts (15 releases, typosquat names, transcript redaction, 2.7.3 warning) →
    §1, §3.5.5, §5.6, §5.7.
  - Owner approved r2 on 2026-11-09.
- r3 2026-11-09: Fable plan review (16 findings; each checked). Spec-level changes: examples move
  into the package (§5.4, above); modes compared by exec bit only; the hardlink trust applies inside
  `_entries` to every `copy_and_hash` caller (lock, release, services), not just `verify`; the
  launcher adds `--exclude-newer-package agentbreeder=…` (otherwise the release timestamp excludes
  the package itself) and a minimum uv version (0.11.6); npm `3.0.0-alpha.1` is published manually
  without npm provenance, a recorded exception to criterion 1 (§3.5.4). "12 doc pages" counted
  matching lines, not files; `hero.tsx` has no example reference. Plan-level fixes are in plan r2.
- r4 2026-11-09: Fable re-review of plan r2: 15 of 16 resolved, 8 new (each checked). Spec-level:
  package-data is explicit subtrees checked by the file-list test, not generated from
  `_content.json` (§5.1); trust is pinned to the import-time package root, not the patchable
  `BUILTIN_ROOT`; the launcher's `agentbreeder` window is release time + 30 days, and P4 checks the
  PyPI upload falls inside it. The rest are plan edits (plan r3).
- r5 2026-11-09: Fable review of the S1 + S2 code (4 should-fix, 4 nits; each checked, all fixed):
  the old-path `.gitignore` rules for Northwind state are kept beside the new ones (an older
  checkout's state would otherwise be untracked and unignored, and `.gitleaks.toml` allowlists
  `examples/`); the packaging test plants local-state sentinels so its leak check is not vacuous on
  a clean runner, which caught `examples/northwind/stack/**/*` shipping an untracked log, so
  `stack/` is now listed file by file; two stale `docker build` paths; installed metadata and the
  built wheel's `Version` are checked against `__version__`; `npm_version` uses `fullmatch`.
  Recorded limits: sdists are compared by content, not archive bytes (§5.1); the two registry
  globs would still take a stray untracked file in a local build, which `content_manifest.py
  --check` (CI, release) refuses and the installed content check rejects.
- r6 2026-11-09: Fable review of the S3 code (trust boundary holds; 2 should-fix, 4 nits; each
  checked, all fixed): `registry publish` into a shipped tree failed with a misleading
  `CONTENT_MISMATCH` (staging sits inside the tree) and now refuses up front
  (`REGISTRY_READ_ONLY`, naming the manual steps); `CONTENT_MISMATCH` names the checkout remedy
  (`git add`, `scripts/content_manifest.py`); malformed manifest entries are a `RegistryError`,
  not a raw exception; `docs/registry.md` and the website document the built-in exception; the
  CLI's corruption-check line prints only when the trusted built-in root was searched.
- r7 2026-11-09: Fable review of the S4 code (3 should-fix, nits; each checked, all fixed):
  `example NAME .` renamed a sibling over the working directory, leaving the shell in an unlinked
  directory, so staging moved inside the destination; filesystem errors were raw tracebacks and are
  now `COPY_FAILED` / `DESTINATION_NOT_EMPTY` / `DESTINATION_UNSAFE`; a failed copy left created
  parent directories, now removed. Nits taken: directory modes ignore the umask, manifest paths are
  checked with `_safe_name`, an entry appearing mid-copy is refused and kept, more tests.
- r8 2026-11-09: Fable review of the S5 code (no high findings; 1 medium, 4 low, 2 nits; each
  checked, all fixed): the in-process path was only covered by the Docker-gated test, so a
  no-Docker test now runs the real `cli.main`, static-adapter gate and SQLite store with only
  Docker faked, and the real run is recorded (the Docker test, and `agentbreeder quickstart` from a
  non-editable `pip install .` in a fresh venv: all five steps, exit 0, no demo container or image
  left; macOS + Docker Desktop). Also: a Ctrl-C test (cleanup in `finally`, environment
  restored); dry-run tests that prove `validate` ran and fail on a copy, validate or workdir error;
  the shipped sample README points at `agentbreeder quickstart`; unexpected in-process exceptions
  print only their type (their text can carry local details); docstring timeouts corrected.
- r9 2026-11-10: Fable review of the S6 code (1 medium, 4 low, 3 nits; each checked; 7 fixed,
  1 deferred to its check): `doctor`'s exact-output test failed on a machine with 2.x home state,
  so tests stub the legacy check by default; the doctor markers are the 2.x names written under
  `~/.agentbreeder` at `v2.7.3` (`layout.json` dropped as project-local like `infra-state.json`;
  `.env`, 2.x's fallback secrets file, added); the no-collision test scans the package source for
  every 3.x home path instead of a hard-coded list; globbed compose files are reported as the
  pattern, never as raw names; README links are checked to exist. The README is the PyPI long
  description, so its relative links became absolute (`twine check --strict` passes on both
  dists). Deferred: "a bare `pip install agentbreeder` still installs 2.7.3" is reasoned from
  PEP 440 pre-release rules and is checked end to end by `upgrade-guard` at P4.
- r10 2026-11-10: Fable review of the S7 launcher (3 medium, 7 low; each checked, all fixed):
  the npm README claimed CI runs and a hashed-requirements file that do not exist yet and lacked
  "verification pending" (reworded); on a terminal, Ctrl-C reached uvx twice (the launcher now
  forwards SIGINT only when neither stdin nor stderr is a terminal; a `kill -INT` of the launcher
  alone on a terminal is therefore not forwarded, SIGTERM always is; the terminal path is reasoned, not run against real uv);
  the signal re-raise path was untested (test added, plus test timeouts); "uvx missing" tests
  depended on no system `/usr/bin/uvx` (private tool directory); `--launcher-version` warned only
  for index variables (now also `UV_INDEX_STRATEGY`, `UV_PRERELEASE`, `UV_OVERRIDE`,
  `UV_CONSTRAINT`; README lists them); a `uvx` link into `node_modules` was accepted (resolved
  path checked too); `sync_npm --check` compared only exact pins (now every pyproject
  requirement's specifier, offline, and the README's version and `min_uv`). Interpretation
  recorded in §5.3: "a match inside the current directory" means one that comes from it; the
  literal subtree reading refuses uv's default `~/.local/bin` whenever the user runs from
  `$HOME`. `exclude_newer` is the time the constraints are resolved at release prep
  (`sync_npm.py --write`), not the release commit time, which is unknown until merge; the S8
  verify job checks it is fresh and that `window_end` is after the tag. Verified: the launcher
  with real uvx 0.11.6 and the locally built wheel (`UV_FIND_LINKS`) ran `--version`,
  `example list`, `registry verify` and `quickstart --dry-run` (macOS). Node 18 not run locally
  (CI matrix in S8).
