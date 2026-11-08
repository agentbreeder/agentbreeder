# Plan: agent.yaml → build → governed deploy (Northwind end to end)

Spec: `docs/superpowers/specs/2026-11-07-agent-yaml-build-design.md` (r3; §18 normative).
Status: slice 1 implemented (incl. Codex fixes). Slice 4 now also carries the `model_access`/`gateways` schema (§18.6). Epic #725.
Branch: `feat/agent-yaml-build`. Commit per slice after the `/launch` gate; open the PR only after
slice 7 passes (owner rule: defer the PR until the epic is done).

**Rules for every slice:**

- TDD: write the failing test first.
- Run the existing suite: `pytest -q` and `ruff check`.
- No change to `deploy` behaviour for manifests without `secrets` or `model_access`.

## Slice 1: local secret files (#727)

- **Code:**
  - `src/agentbreeder/secrets.py`:
    - `set_secret(state, name, data)`: 0600, `O_NOFOLLOW`, name regex `^[a-z0-9][a-z0-9-]{0,62}$`.
    - `preflight(state, refs)`: stat only; `[SECRET_MISSING]` / `[SECRET_MODE]`.
    - `materialise(state, deployment_id, refs)`: copies into a 0700 dir as 0444 files and
      returns the mount list.
    - `cleanup(state, deployment_id)`.
  - `gate.py`: remove the `SECRETS_UNSUPPORTED` deny. Secrets stay `secret://` references only,
    and the manifest validator is unchanged.
  - `deployment.deploy`: after the gate, approval and the `starting` commit, run preflight, then
    materialise. On every failure path, and in `remove`, run `cleanup`.
  - `runtime.create(..., mounts=[(src, dst)])`: emits
    `--mount type=bind,src=…,dst=/run/secrets/<name>,readonly` plus `--env <ENV>_FILE=…`.
  - CLI: `agentbreeder secret set NAME [--state-dir]` reads stdin; `AGENTBREEDER_STATE_DIR`
    becomes the default `--state-dir` for all commands.
- **Tests:**
  - unit tests for modes, symlink refusal and names;
  - a gate test: secrets are allowed and the audit records names only;
  - a deployment test: a failure path cleans up;
  - a Docker integration test: a container reads the file as uid 65532, the value is absent from
    `inspect`, labels and logs, and the copies are gone after remove;
  - a Linux run in CI (GitHub Actions ubuntu).
- **Docs:** `docs/manifest.md` (secrets now local files), `docs/local-deployment.md`.

## Slice 2: registries + refs + lock (#728)

- **Code:** `src/agentbreeder/registry.py`:
  - ref parsing with exact versions only;
  - roots (`./registry` plus `AGENTBREEDER_REGISTRY_PATH`), with conflicting hashes across roots
    being an error;
  - `copy_and_hash(version_dir, dest)`: regular files and directories only; rejects symlinks,
    hardlinks, devices, `..`, absolute paths and non-UTF-8 names; hashes length-prefixed
    `(relpath, mode, size, bytes)` records;
  - `publish` (no overwrite), `list`, `verify`, `new` (scaffold).
- **Code:** `src/agentbreeder/lock.py`: load and write `agent.lock`; plain, `--update [ref]` and
  `--locked` semantics; `[LOCK_STALE]`, `[HASH_MISMATCH]`, `[REF_NOT_FOUND]`, `[REF_UNPINNED]`.
- **CLI:** `agentbreeder registry new|publish|list|verify`.
- **Tests:** hash determinism; a one-byte tamper; symlinks; a shadowing conflict; lock semantics
  table tests.

## Slice 3: agent.yaml schema + validate (#726/#729)

- **Code:** `src/agentbreeder/agentfile.py`:
  - pydantic models for §5 with strict loading; reuse manifest.py's bounded loader;
  - name ≤ 53 characters;
  - `notes` requires `tool/customer-notes`;
  - wrong-file detection: a v2 manifest or a 2.x file each gets the redirect message.
- `agentbreeder validate` dispatches on `kind`.
- **Tests:** a valid example; each rejection; detection of the three file kinds.

## Slice 4: ADK runtime + build (#729)

- **Runtime.** `src/agentbreeder/runtimes/adk/1.0.0/` (package data):
  - `runtime/{server,agent,plugins,memory,rag,telemetry,tools}.py`, generalised from the
    Northwind agent and driven by `agent.config.json`;
  - `Dockerfile` (fixed template; COPY only; base pinned by digest);
  - `requirements.lock` for amd64 and arm64, from
    `uv pip compile --generate-hashes "google-adk[db]==2.12.0" "litellm[mcp]==1.106.2" mcp==2.3.0 "psycopg[binary]==3.3.8" opentelemetry-…`;
  - every secret is read from `*_FILE`.
- **Tool loader.** Reads `tool.yaml` and imports from `PYTHONPATH`. Each call gets
  `ToolRuntime(customer, session, db, events)`. `writes_requests` tools are exposed as normal
  functions, with no execute path.
- **Build.** `src/agentbreeder/build.py`:
  - validate → resolve/lock → copy-and-hash into a temp context → write `agent.config.json` →
    `docker build` (no build args) → image ID;
  - `deploy/<name>.deploy.yaml` from operator `environments.<env>` (identity, bindings,
    `model_access`);
  - `deploy/<name>.build.json`;
  - `--json`, and a "next command" hint.
- **Operator config.** Add `environments.<env>.services|bindings|agents` (parsed, validated, no
  side effects).
- **Tests:**
  - runtime unit tests with a stub model, covering the step cap, tool scoping and the
    request-only tool;
  - build integration: a tiny registry gives an image, the manifest validates, and `deploy`
    succeeds without `model_access`, using a pre-minted key as a plain secret for this slice.

## Slice 5: knowledge + tools packaging for Northwind (#730)

- **Runtime RAG:** pgvector table per knowledge name and version; embeddings through the gateway
  alias; BM25 and RRF; sync on start with hash diffs.
- **Registry entries** under `examples/northwind/registry/`:
  - `prompt/northwind-support/1.0.0`;
  - `knowledge/northwind-help/1.0.0`;
  - `tool/{orders,cancel-order,customer-notes,escalate-to-human,web-search}/1.0.0`;
  - `eval/northwind-golden/1.0.0` (golden.jsonl plus fixture SQL).

  Each is ported from the reference agent without behaviour changes.
- **Stack** in `examples/northwind/stack/`:
  - Compose: pgvector, LiteLLM, Phoenix, tools proxy, ops + approvals;
  - `up.sh`, which writes operator.yaml, principals, the master-key file, the DB secret
    (`agentbreeder secret set`) and the seed.
- **Tests:** tool unit tests against a test Postgres (customer scoping; cancel writes a request
  only).

## Slice 6: LiteLLM first-class (#729/#730)

- **Manifest:** an optional `model_access` block, plus docs.
- **Operator config:** `gateways.<name>` (type litellm, url, agent_url, `master_key` via `env:` or
  `file:`) and `environments.<env>.gateway|ceilings`.
- **Gate:** `[BUDGET_CEILING]`.
- **Key lifecycle.** `src/agentbreeder/gateways/litellm.py` holds the client
  (`/key/generate|delete|info`, `/model/info`). `deployment` handles minting:
  - **Mint** after the `starting` commit, aliased to the deployment ID; the file goes into the
    deployment's secrets as `LLM_GATEWAY_KEY`, and the record stores the key ID.
  - **Failure:** revoke the key and clean up.
  - **Remove:** revoke.
  - **Disable:** set the blocker first, then revoke best-effort.
  - **Enable:** no mint.
  - **Reconcile:** revoke orphaned keys.
  - **Status:** show spend versus budget.
  - **Doctor:** check gateway reach and aliases.
  - The alias check runs at deploy.
- **Tests:** a fake LiteLLM HTTP server for unit tests, covering mint, revoke, idempotency,
  failure revoke, disable with the gateway down and the budget ceiling. An integration test runs
  against the real pinned LiteLLM container (revoked key gives 401; an alias off the allow-list
  is refused).

## Slice 7: eval + release + doctor agent.yaml + Northwind acceptance (#731)

- **`src/agentbreeder/evals.py`:** ported from Northwind `offline_evals.py`, covering code
  checks, the judge via its own minted key, and retries and flaky reporting.
- **`release.py`:**
  - holds a lockfile; runs build `--locked` → candidate → fixture reset → golden set → remove the
    candidate;
  - promotes by removing production and deploying the exact image; writes `release.json` for
    `--rollback`;
  - is a no-op for an already-promoted image;
  - writes `release_*` audit events; exits with 0/1/2/3/5.
- **`doctor agent.yaml --environment`** preflight (§12).
- **Acceptance:** run the §13 walkthrough verbatim: **61/61**, the exact image promoted, chat
  answers. Then the secret grep test (AC3), the tamper test (AC4) and the LiteLLM checks (AC7).
- **Docs:** a new `docs/agent-yaml.md` plus updates to `CLAUDE.md`, `ARCHITECTURE.md`,
  `README.md` and `docs/commands.md`. The website only after merge.
- **Then:** open the PR (stack on main), run Codex review in a loop, and wait for green CI plus
  owner merge.

## Sequencing and risk notes

- Slices 1–3 are independent of Docker-heavy work and land fast.
- Slice 4 is the long pole, because it means generalising the runtime.
- Slice 6 depends on slice 1 (secret files), and slice 7 on all of them.
- If the hash-locked install proves infeasible on both architectures, fall back to exact `==`
  pins plus a recorded `pip hash` check of wheels, and record that in the spec.
