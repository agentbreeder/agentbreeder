# Plan: installable preview release (epic #751)

- Spec: `docs/superpowers/specs/2026-11-09-installable-preview-release-design.md` (r2 approved by the
  owner 2026-11-09; r3 and r4 record the plan-review changes, §5.4 and §10). § numbers refer to it.
- Plan r2 (2026-11-09): rewritten after a Fable plan review (16 findings, all checked; see the spec
  §10 r3 entry). Plan r3: Fable re-review (15 of 16 resolved; 8 new findings, all checked, folded
  in below; spec §10 r4). Round 3: APPROVED (one low note folded into S4).
- Branch `feat/installable-preview` from `main`. One PR for code slices S1–S8, opened once the whole
  set passes locally. Publishing (P1–P5) happens after merge, each step with the owner.
- Each slice: tests first, implement, full suite + ruff, commit. `pyproject.toml` and workflow
  changes take the full gate; no docs-only exception.

## Facts (checked 2026-11-09)

- `registry.roots()` = project `registry/`, `AGENTBREEDER_REGISTRY_PATH`, `BUILTIN_ROOT`
  (`registry.py:80`). The hardlink refusal is in `_entries` (`registry.py:120`). `_entries` is
  reached through `copy_and_hash` from `registry.resolve`/`publish`/`verify`, `lock.py:121,137,207`,
  `release.py:78` and `services/mcp_stack.py:162`. The fix belongs in `_entries`, not `verify`.
- `_entries` already skips `__pycache__` and `.pyc` (pip byte-compiles shipped example `.py` files;
  13 tracked `.py` files under `examples/`, no `__init__.py`).
- `build.build(..., builder=docker_build)` (`build.py:301`) always calls the builder and needs an
  operator config with a gateway and an agent identity; there is no check-only flag.
- `[tool.setuptools.cmdclass]` with a module outside `src/` is rejected or unresolvable (reviewer
  reproduced it), so plan r1's build hook is dropped.
- Under umask 077, pip installs files as 0600 and directories as 0700, so modes cannot be compared
  exactly; only the executable bit is meaningful (as `copy_and_hash` already treats it).
- 3.x home state (`local/`, `stacks/`, `operator.yaml`, `oidc-token`) does not collide with 2.x
  names at `v2.7.3` (`workspace.yaml`, `infra-state.json`, `context.json`, `registry/`,
  `providers.local.yaml`, `layout.json`, `evals/`, `.env`, compose files). `doctor` markers:
  `workspace.yaml`, `infra-state.json`, `context.json`.
- One tracked dotfile under examples: `northwind/stack/.dockerignore`.

## Code slices

### S1. Version source and metadata (#752)
- Tests: `__version__` == `importlib.metadata.version` == `agentbreeder --version`;
  `npm_version()` maps `3.0.0a1→3.0.0-alpha.1`, `b→beta`, `rc→rc`, final unchanged; classifiers
  include Alpha.
- `pyproject.toml`: `dynamic = ["version"]` from `agentbreeder.__version__`; description,
  classifiers, URLs. `__version__ = "3.0.0a1"`. `src/agentbreeder/_version.py` holds `npm_version()`.

### S2. Examples move into the package; explicit package data (#752)
- `git mv examples/http-agent examples/northwind src/agentbreeder/examples/`. Repo-root
  `examples/README.md` points to the new location and to `agentbreeder example`.
- `pyproject.toml` package-data lists exact subtrees, never a bare `**/*` over examples:
  `examples/http-agent/{*.py,*.md,*.yaml,Dockerfile}`, `examples/northwind/{agent.yaml,agent.lock,
  stack.yaml,stack.lock,README.md}`, `examples/northwind/registry/**/*`,
  the three tracked `stack/` files one by one (`.dockerignore`, `db/commerce.sql`,
  `litellm/config.yaml`; a `stack/**/*` glob took local run output, review r5), plus the built-in
  registry, `policy/cedar_schema.json` and `_content.json`. `deploy/`, `.agentbreeder/` and
  `.secrets/` are never matched (dotfiles and dot-directories are skipped by `**/*`; verified).
  Brace forms above are shorthand: `pyproject.toml` gets one pattern per entry.
- `scripts/content_manifest.py`: from `git ls-files` of `builtin_registry/` and `examples/`, write
  `src/agentbreeder/_content.json` = `{path: {sha256, exec}}`. `--check` (CI) fails if stale, or if
  an untracked, non-ignored file exists under those trees.
- `MANIFEST.in`: drop the old `examples/http-agent` line (package data now carries it).
- Tests (`tests/test_packaging.py`, CI): build sdist, then wheel from the sdist, and a wheel from the
  tree; both wheels' file lists equal Python modules + `_content.json` paths + `_content.json` +
  `cedar_schema.json` + dist-info; sdist list equals tracked files under the package +
  metadata files; no `__pycache__`, `.pyc`, `.agentbreeder/`, `deploy/`, `.secrets/`, `.DS_Store`;
  `.dockerignore` present. Two builds with `SOURCE_DATE_EPOCH` and umask 022 give equal sha256s.
- `tests/fixtures/builtin_hashes.json`: built-in refs are append-only (an existing ref's hash may
  never change).
- Update every reference to `examples/` (list below). `.gitignore` patterns for
  `examples/northwind/{.agentbreeder,deploy,stack/.secrets}` move to the new path; the owner's
  ignored local state at the old path is left alone (noted in the PR).
- Ruff: `extend-exclude` covers all of `src/agentbreeder/examples/northwind` (its registry `.py`
  files are published versions hashed in `stack.lock` and fail ruff today; they must not be
  reformatted). CI ruff and validate paths → `src/agentbreeder/examples/http-agent`.
  `.github/dependabot.yml:12` → `/src/agentbreeder/examples/http-agent`.
- Proof the move keeps locks valid: the existing `test_sample` and `test_services_lock` `--locked`
  paths run against the new location (no `../` or repo-root paths exist in Northwind's files).

### S3. Built-in content check; hardlinks (#752)
- `registry.py`: `_PACKAGED_ROOT = Path(__file__).resolve().parent`, fixed at import and separate
  from the patchable `BUILTIN_ROOT` (four tests patch `BUILTIN_ROOT` to a temp dir and create
  versions there; those keep the hardlink rule and need no manifest). `trusted_root(path)` is true
  when `path.resolve()` is under `_PACKAGED_ROOT/builtin_registry` or `_PACKAGED_ROOT/examples`.
  Manifest keys are package-relative; `_entries` maps its version-directory source to that prefix. Inside `_entries`, trusted sources skip the `st_nlink` refusal and are
  instead checked against `_content.json`: regular file, no symlink, sha256 equal, exec bit equal,
  no files outside the manifest (ignoring `__pycache__`/`.pyc`, as today). Project and user
  roots are unchanged. This applies to every `copy_and_hash` caller.
- `registry verify` output: "built-in content matches the shipped manifest (corruption check)".
- Tests: hardlinked built-in passes `verify`, `resolve`, `lock.materialise` and
  `release._eval_set`; changed byte → `CONTENT_MISMATCH`; extra file refused; symlink refused;
  hardlink in a project registry still `ENTRY_UNSAFE`; 0600/0700 install modes pass; a patched
  `BUILTIN_ROOT` keeps today's behaviour; site-packages behind a symlinked venv path still trusted;
  `AGENTBREEDER_REGISTRY_PATH` pointing at the built-in root resolves to the same manifest check.

### S4. `agentbreeder example` (#752)
- As built (supersedes the bullets below where they differ): the module is
  `src/agentbreeder/example.py` (singular, so it does not read as the `examples/` data directory)
  with `names()` and `copy()`. It does not reuse `copy_and_hash`: it reads each manifest file with
  `O_NOFOLLOW` and checks sha256 + exec bit (once before writing, again for the bytes written),
  stages in a hidden directory **inside** the destination and moves entries into place, so an
  existing empty destination (even the working directory) keeps its inode and attributes; files
  0644/0755 and directories inside the destination 0755 regardless of umask (parents it creates
  follow the umask, like `mkdir -p`); the move is per entry, not one atomic rename (a tiny
  check-to-rename window remains for a file created at the same path); on failure it removes staged and moved
  entries and any directories it created; `OSError` becomes `COPY_FAILED` or
  `DESTINATION_NOT_EMPTY`. `validate` also accepts a `stack.yaml` (schema only). The checks from
  an installed wheel (copy, validate, `build --locked`) run in the S5/S8 install-smoke job; the S4
  tests use the checkout and a simulated installed package.
- `src/agentbreeder/examples.py`: `examples_root()` = the packaged directory; `list()`; `copy(name,
  dest)`. `dest` must be absent, or an empty real directory (`lstat`). The source is checked
  against `_content.json` first. The copy iterates the manifest's file list (never walks the
  directory), so ignored local state beside a checkout's examples is neither refused nor copied. Files
  are copied with the `copy_and_hash` primitives into a `mkdtemp`
  sibling, written 0644/0755 by exec bit, skipping `__pycache__`, then renamed into place.
- CLI `example list`, `example <name> [DIR]` (default `./<name>`).
- Tests: a planted `.agentbreeder/` beside the source is not copied and doesn't block the copy;
  absent/empty/non-empty/symlink/file destinations; modes; `.dockerignore` copied; tampered
  source refused before writing; failure mid-copy leaves nothing; copied Northwind passes `validate`
  on `agent.yaml` and `stack.yaml`; `build.build(mode="locked", builder=stub)` on the copy with a
  fixture operator config succeeds (lock resolves every ref without Docker).

### S5. `agentbreeder quickstart` (#752)
- `src/agentbreeder/quickstart.py`: the five steps of the old script. The image is built from a temp
  copy made by `examples.copy("http-agent", …)` (0644 sources, so `USER 65532` can read them).
- CLI calls run in-process through `cli.main(argv)` with `contextlib.redirect_stdout` capture and a
  private state dir passed as `--state-dir`. The operator config is read only from
  `AGENTBREEDER_OPERATOR_CONFIG` and the Docker context from `DOCKER_CONTEXT`, so a context manager
  sets both for the run and restores them in `finally` (quickstart is single-threaded). Docker calls keep the 180 s
  subprocess timeouts. `--dry-run`: `doctor` + `validate` + print the steps, no Docker changes.
- `examples/http-agent/quickstart.py` becomes a wrapper. `tests/test_quickstart.py` rewritten: the
  two monkeypatch tests (`demo.run`, `TimeoutExpired`) retarget `quickstart.run`; a dry-run test;
  Docker test stays behind `AGENTBREEDER_DOCKER_TEST=1`.

### S6. 2.x notice (#753)
- README top block + PyPI long description (§5.6). `doctor` INFO line when any marker exists:
  "2.x state found in ~/.agentbreeder; 3.x does not read or change it". Tests with a temp HOME.

### S7. npx launcher (#782)
- `npm/package.json` (`agentbreeder`, version via `npm_version()`, `bin`, `engines.node >=18`,
  `files` allowlist, no scripts), `npm/bin/agentbreeder.js`, `npm/release.json`
  (`pypi_version`, `exclude_newer`, `min_uv`), `npm/constraints.txt`, `npm/README.md`.
- argv: `uvx --exclude-newer <exclude_newer> --exclude-newer-package agentbreeder=<window_end> -c <constraints.txt> --from agentbreeder[eval]==<ver> agentbreeder <args…>`. `exclude_newer`
  is the release commit time, which bounds dependencies; the package override lets the
  later-uploaded `agentbreeder` file resolve (verified with uv 0.11.6). `window_end` = release
  commit time + 30 days, committed in `release.json`; the `==` pin already fixes the version, so the
  window only bounds files added later to that version. `min_uv` = 0.11.6 (verified flags); the launcher
  runs `uvx --version` and refuses an older uv with an upgrade message.
- `uvx` lookup: absolute path from `PATH` with `node_modules` entries removed; refuse a match under
  cwd; `shell: false`; forward stdio, SIGINT/SIGTERM, exit code. Missing → 127 + install command.
- `npm/constraints.txt` comes from a committed `uv pip compile --universal` of the `[eval]` extra at
  release time; `scripts/sync_npm.py --check` compares versions and `release.json` without
  re-resolving (no network).
- Tests: exact argv including both exclude flags; `cd npm && node --test` (auto-discovery; Node 18 and 22 in CI; only `node:test` APIs
  present in 18). Stub `uvx` records argv; `node_modules/.bin/uvx` skipped; cwd `uvx` refused;
  missing → 127; old uv refused; exit code + SIGTERM forwarded; `--launcher-version` output and the
  index-variable warning; `npm pack --dry-run` file list.

### S8. Release workflow, CI and pre-tag docs (#752, #755 first half)
- `.github/workflows/release.yml` (§5.2), triggered by `v3.*` tags:
  - **verify**: `fetch-depth: 0`; tag commit is an ancestor of `origin/main`; tag (via `env:`)
    == `v` + `__version__`; `git status --porcelain` empty; full test suite.
  - **build**: umask 022, `SOURCE_DATE_EPOCH`; build deps from hashed `build-requirements.txt`;
    sdist + wheel twice and compare; packaging tests; `npm pack` in `npm/`; smoke install (below);
    `uv pip compile --generate-hashes` → `requirements-3.0.0a1.txt`; upload all with sha256 outputs.
  - **attest** (`id-token`, `attestations: write`): checks sha256s, attests wheel, sdist, npm tgz.
  - **github-release** (`contents: write`): attaches the hashed requirements file and artifacts.
  - **publish-testpypi** → **publish-pypi** (environments, owner approval).
  - **publish-npm** only `if: vars.NPM_TRUSTED_PUBLISHING == 'true'` (false for 3.0.0-alpha.1,
    which the owner publishes manually from the attested tgz).
  - Actions pinned by SHA (releases ≥ two weeks old); no caches; `persist-credentials: false`.
- `ci.yml` new jobs: `install-smoke` (ubuntu; uv pinned + checksum; `uv pip install
  "agentbreeder[eval] @ file://$PWD/dist/<wheel>"` with `UV_LINK_MODE=hardlink`, asserts `st_nlink
  > 1` on a built-in file, then `--version`, `registry verify`, `example northwind`, `validate`,
  the stub-builder locked build, `quickstart --dry-run`); `npm-launcher`; `content_manifest
  --check`; `sync_npm --check`; `upgrade-guard` (after publish only; before that against TestPyPI):
  install `agentbreeder==2.7.3`, run `pip install -U agentbreeder`, assert 2.7.3. `actionlint` and
  `zizmor` on workflow files.
- References to update (re-check each): `.github/dependabot.yml`, `ci.yml` (ruff paths, validate
  path), `.gitignore`, `.gitleaks.toml`, `pyproject.toml` (ruff `extend-exclude`), `AGENT.md`,
  `ARCHITECTURE.md`, `CHANGELOG.md`, `CLAUDE.md`, `MANIFEST.in`, `README.md`,
  `docs/{agent-yaml,commands,first-deployment,project-structure,registry,services}.md`, the six
  tests, website `{agent-yaml,index,local-deployment,services}.mdx`. (`hero.tsx` has no examples
  reference today; the spec's "12 pages" counted lines, not files: recorded in §10.)
- README install section: three exact-pin commands, "preview; clean-machine verification pending",
  2.x warning. `docs/commands.md`: `example`, `quickstart`.
- Cross-repo grep of `agentbreeder-cloud` (version pins, `pip install agentbreeder`,
  `0.0.0.dev0`), recorded in spec §10.

### Review and PR
- Re-review of this plan (Fable) until no substantive findings.
- After S8: full suite, ruff, `/gate`; ad hoc Fable review of the diff in two parts (packaging,
  registry and workflow security; launcher, example copy and quickstart), loop to convergence;
  push; open PR. Merge needs green CI, the review's approval and the owner's review.

## Publishing (after merge; each step with the owner)

- **P1 Owner prerequisites (§3.5)**, verified by `gh api`: `pypi`, `testpypi`, `npm` environments
  with a required reviewer, admin bypass off, deployments limited to `v3.*`; `v*` tag ruleset; PyPI
  publisher audit (owner, web UI; result noted in #752); repo-level `NPM_TOKEN` deleted now
  (nothing uses it).
- **P2 Tag `v3.0.0a1`** on the merged `main` commit. The workflow runs through TestPyPI; PyPI waits.
- **P3 TestPyPI rehearsal** (#754 part 1), clean macOS and Ubuntu, pip and uvx paths with
  `UV_INDEX`/`--extra-index-url`. A failure → fix, `3.0.0a2`, back to P2.
- **P4 Owner approves PyPI**; check that the PyPI upload time of `3.0.0a1` is before `window_end`
  (otherwise cut `3.0.0a2`); owner publishes npm `3.0.0-alpha.1` manually with 2FA from the
  attested tgz (sha256 checked). That version has no npm provenance: a recorded exception to
  criterion 1, citing §3.5.4. Owner then sets up npm trusted publishing and
  `NPM_TRUSTED_PUBLISHING=true`. `upgrade-guard` switches to PyPI.
- **P5 #754** clean-machine run, all three paths, Northwind 61/61 (owner's model keys; about $0.24
  per release for the judge, measured 2026-11-08); steps, times, admin vs developer effort,
  redacted transcripts. Then the second half of #755 in a follow-up PR: website install page,
  quickstart docs switched to `example`/`quickstart`, footer `3.0.0a1`, `what-ships.tsx`, README
  "verification pending" removed, CLAUDE.md "source-only `0.0.0.dev0`" and "Not shipped: PyPI"
  lines updated in both repos, `docs/cross-repo-sync.md:10`.

## Risks

- Moving examples churns paths in docs and tests: S2 does it in one commit with the reference list
  above, and CI's `validate` step catches a missed path.
- Trusting built-in roots by location means a user who can write site-packages can change built-in
  content; `_content.json` turns that into a corruption check only (stated in docs; §4 non-goal).
- If the 2.x PyPI publisher can't be audited or replaced quickly, P2 waits; the code PR doesn't.
