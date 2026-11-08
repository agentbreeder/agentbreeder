# Plan: local backing services from `stack.yaml` (epic #734)

Spec: `docs/superpowers/specs/2026-11-08-agent-services-design.md` (r2). Branch `feat/agent-services`,
stacked on `feat/agent-yaml-build` (#733). One PR for the epic, opened when slices 1–5 pass and the
acceptance run is green (deferred-PR rule); commits per slice.

Every slice: tests first, ≥ 95% coverage on new modules, full suite green, `/launch` gate, then a
review pass (Codex when it has credits, otherwise the pair-loop reviewer on a different model) and
fix findings before the next slice.

## Slice 1 — secrets from the environment (#736)

- `src/agentbreeder/env_import.py` (new):
  - `parse_env_file(path)`: a strict parser that accepts only `KEY=value`, `export KEY=value`,
    single or double quoted values, `#` comments and blank lines. Anything else is an error that
    gives the line number only. It never sources the file or expands variables. It reads at most
    64 KiB, refuses symlinks, and requires the file to be owned by the user.
  - `DENYLIST` patterns: `AWS_*`, `AZURE_*`, `GOOGLE_APPLICATION_CREDENTIALS`, `GCLOUD_*`,
    `GITHUB_TOKEN`, `GH_TOKEN`, `GITLAB_TOKEN`, `NPM_TOKEN`, `PYPI_*`, `DOCKER_*PASSWORD*`,
    `*_SSH_*`, `SSH_AUTH_SOCK`, `KUBECONFIG`, `VAULT_TOKEN`.
  - `lookup(name, env_file=None)`: reads the process environment first, then the file. A
    denylisted name raises `IMPORT_DENIED`.
- `secret_files.generate_token(nbytes=24) -> bytes` (hex).
- CLI: `secret set NAME [--from-env VAR [--env-file FILE] | --generate]`. Stdin stays the default.
  - The options are mutually exclusive.
  - A missing variable is `SECRET_SOURCE_MISSING` and names the variable, never a value.
  - `--generate` refuses to overwrite unless `--force`.
- Tests: parser edge cases (quotes, export, comments, bad lines, size, symlink); denylist; CLI
  paths; canary values never in stdout or stderr.
- Docs: `docs/local-deployment.md` (secret set options), `docs/commands.md`.

## Slice 2 — schema: service kind, stack.yaml, references (#737)

- `registry.KINDS += ("service",)`. `scaffold("service")` writes a `service.yaml` template.
- `src/agentbreeder/services/schema.py`: pydantic models for `service.yaml` (image or build,
  provides, command, user, ports with expose and guard, params, env, secrets, health, egress,
  volumes, init, bootstrap, outputs) and `stack.yaml`. Validation:
  - digest-pinned images; exec-form commands only; no `privileged`, `cap_add` or host network;
  - reserved names; `db_name` regex; guards required on HTTP ports.
- `services/refs.py`:
  - reference grammar: single pass, `{{` escape;
  - the dependency graph and cycle detection;
  - typed outputs and the taint check (secret outputs only into `secrets:`, `bindings`,
    `eval_database` and the gateway `master_key`);
  - address-scope resolution (`internal`, `agents`, `host`) by consumer position.
- `services/lock.py`: `stack.lock` built on `lock.Lock`, with:
  - `path:` file params, which `--locked` accepts when the hash matches;
  - image digests;
  - imported names per service.
- `gateways/litellm_config.py`: parse the LiteLLM config's `model_list` aliases. A missing alias
  is an error, an extra one a warning.
- Tests:
  - every validation error;
  - the taint matrix;
  - the graph;
  - lock modes;
  - `agent.yaml` rejects `service/` refs.

## Slice 3 — engine: up/down/status/logs/env (#738)

- `services/state.py`: stack id (`<name>-<sha256(abs path)[:8]>`); state layout; `stack.db`
  (SQLite: owned secrets, ports, config hashes, bootstrap markers, written-config digest); private
  dirs with `O_NOFOLLOW`.
- `services/compose.py`: the only Compose touchpoint.
  - Generate a paths-only Compose file (0600, `$` escaped, labels for stack id and config hash).
  - Run it with a sanitised environment, explicit `-f`, `--project-directory` and `-p`.
  - Errors are wrapped so env values never show.
- Networks:
  - `agentbreeder-<id>` (internal), and `agentbreeder-<id>-agents`, which only `expose: agents`
    services join;
  - `expose: host` ports publish on `127.0.0.1` with allocated ports.
  - Deploy side: the `Environment.network` field (operator config). `runtime.create` joins that
    network. Tests cover that the existing `deploy` path is unchanged when the field is unset.
- One-shot runner: `init` on every `up` and `bootstrap` once per volume identity, with
  `--log-driver none --rm`, attach stdout, an 8 KiB cap and fail-closed behaviour.
- Operator config writer: the bounded subset from spec §3; digest rule (`OPERATOR_CONFIG_MODIFIED`).
- `--stack DIR` on build, deploy, eval, release, status, remove and reconcile, plus
  `services env/status/logs/down`. `down --volumes` deletes labelled volumes only and refuses
  while deployments are active.
- `doctor`: a Compose v2 `--wait` check.
- Built-in `service/echo@1.0.0` (digest-pinned), with an opt-in real-Docker test:
  - up, idempotent up, down, up;
  - canaries absent from `docker inspect`;
  - two stack ids side by side.

## Slice 4 — built-ins and Northwind (#739)

- `service/postgres-pgvector@1.0.0`:
  - uid 999 and a pre-owned volume;
  - a superuser used only by `init`;
  - per-database roles, `init_sql`;
  - `databases.<name>.url` outputs.
- `service/litellm@1.0.0`: `provides: gateway/litellm`, `egress: true`, the `litellm-auth` guard,
  admin UI and docs off, and `file` param config.
- `service/phoenix@1.0.0`: generated auth secrets, its DB from postgres, and a `bootstrap` that
  mints the ingest key.
- Northwind:
  - `stack.yaml`;
  - `registry/service/northwind-proxy` and `northwind-ops`, built from source with pinned `FROM`
    lines;
  - an ops bearer token.

  Remove `stack/up.sh`, `compose.yaml`, `litellm/entrypoint.sh` and `Dockerfile.host` from
  `examples/northwind` and from the support-agent repo (rajitsaha/agentbreeder-support-agent PR #1).
- Acceptance run: clean checkout → `services up --yes` → `release --stack .` = 61/61, plus spec
  ACs 2–7.

## Slice 5 — docs and site (#740)

- `docs/services.md` (new), plus `docs/agent-yaml.md`, `docs/registry.md`, ARCHITECTURE.md and
  README.
- Site: a `services` page and the what-ships row, labelled local-only preview, claiming only what
  the acceptance run showed.

## Risks

- **Agents network:** this changes deploy. It is behind an unset-by-default operator field, and
  tests cover the existing path.
- **Postgres as uid 999 without capabilities:** this depends on the image's entrypoint. Verify it
  in slice 4 first; fallback is an explicit, documented `cap_add` list (`CHOWN`, `SETUID`,
  `SETGID`, `FOWNER`, `DAC_OVERRIDE`) for that service only.
- **Phoenix bootstrap:** this needs Phoenix's GraphQL API. Pin the image, and test the mint step
  in isolation.
