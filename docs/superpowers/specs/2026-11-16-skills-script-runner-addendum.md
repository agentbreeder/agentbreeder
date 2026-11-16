# Skills, kind (b): the script runner deploy-contract addendum (#1059 K6)

- Status: **r4, reviews converged** (round 4 APPROVED by `/architect` and `/security`, Claude Sonnet,
  read-only; §15). Needs the owner's answers to §13 (A1–A7) before any K6 code.
  No code in this PR.
- Issue #1059, part of epic #1057. Parent spec: `2026-11-16-skills-design.md` ("the skills spec"),
  whose §9 this addendum makes concrete. Plan: `docs/superpowers/plans/2026-11-16-sub-agents-and-skills.md` §9.
- The owner accepted the skills spec's recommendations on 2026-11-16, including D6 (one fresh
  `--network none` container per run, started by a host-side runner), D7 (Python standard library
  only) and D12 (gate visibility by presence only).
- Builds on (on `main` at 8b77de8b): `egress/host.py` and `egress/boundary.py` (a host-side process
  per deployment: `pin.json`, `endpoint.json`, `listen_mode`, `token_matches`, `response_mac`,
  `start_new_session`, idle exit), `egress/protocol.py` (its strict JSON hooks), `deployment.py` (`_deploy_requirements`,
  `_kubernetes_requirements`, the create path and `_grant_mcp`'s per-generation token pattern,
  `remove`, `disable`, `reconcile`), `secret_files.write_generated`, `runtime.py` (`create`,
  `host_gateway`), `manifest.py` (`Manifest`, `digest`), `package.py` (`DeployTemplate`,
  `Package`, `PACKAGE_VERSION`), `build.py` (`template`, `bind`, `docker_build`), `audit.py`,
  `store.py`.
- **Nothing in this document has been run.** In particular "no network" and "nothing shared" are
  design claims until the K6f acceptance run.

## 1. What this addendum decides

The skills spec fixed where a script runs (§9.1) and outlined the contract (§9.5). This addendum
fixes, for implementation:

1. the sandbox image: build, contents, permissions, identity (§3);
2. the deploy manifest, template and package fields (§4);
3. the gate and operator-config controls (§5);
4. the runner process: transport, authentication, request handling (§6);
5. the per-run container and its lifecycle (§7);
6. limits and error codes (§8);
7. the deployment lifecycle: deploy, status, reconcile, kill switch, remove (§9);
8. audit and observability (§10);
9. the threat model (§11);
10. files, tests and acceptance per sub-slice (§12).

## 2. Shape

```
host (operator user, has Docker access)
 ├─ agentbreeder deploy ── starts ──▶ skills runner (python -m agentbreeder.skills_runner, one per deployment)
 │                                     │  pin.json (0600, private dir): token sha256, declared scripts,
 │                                     │  sandbox image ID, revision, limits
 │                                     │
 │   agent container ── POST /run ─────┤  (Linux: Unix socket bind-mounted into the agent only;
 │   (holds the runner token file)     │   Docker Desktop: 127.0.0.1:<port> via host.docker.internal)
 │                                     ▼
 │                       docker run --rm --network none ... <sandbox image ID>  (one container per run)
 │                         stdin: args JSON   stdout: result (≤ 64 KiB)   killed at 10 s
 └─ audit chain (SQLite store) ◀── one row per run, written before the reply
```

The agent never talks to Docker, never names an image or a file, and never sees the runner's
state. The runner never accepts an image, a path or a command from a request.

## 3. The sandbox image (K6b)

- **Built by `agentbreeder build`** when the agent pins at least one script skill and its runtime
  lists `skill_scripts`. A second, separate `docker build` of its own context (not a stage of the
  agent's Dockerfile), through the same `builder` injection as the agent image so tests need no
  Docker.
- **Generated Dockerfile** (no interpolation of anything but validated names):

  ```dockerfile
  FROM python@sha256:<the runtime version's pinned base digest>
  COPY skills/ /skills/
  USER 65534:65534
  WORKDIR /
  ENTRYPOINT ["timeout", "-s", "KILL", "-k", "1", "10"]
  ```

  The base digest is read from the runtime version's `Dockerfile` `FROM` line and must match
  `python@sha256:[0-9a-f]{64}`; anything else is `BUILD_FAILED`. Today that line is the
  multi-arch index of `python:3.12-slim` (`runtime/adk@1.3.0/Dockerfile`), so the sandbox is the
  slim variant with the runtime's Python; the build record names the variant and digest. No `RUN`,
  no pip, no package manager.
- **PID 1 enforces the deadline.** `timeout` (coreutils, in the slim image) is PID 1 and the script
  its child, so a run is killed at 10 s even if the runner dies (§7.3). If a script signals PID 1
  and `timeout` exits, the kernel kills every process in the container's PID namespace, so the
  script dies with it. A K6f check covers a script that ignores SIGTERM and signals PID 1.
- **Context:** for each script skill, `/skills/<name>/scripts/<module>.py` and
  `/skills/<name>/references/*` (scripts may read their own references). `SKILL.md` and
  `manifest.yaml` are not copied: the runner holds the declarations (§4) and the agent image holds
  the instructions. Instruction packs are not copied.
- **Permissions:** directories `0555`, files `0444`, owned by root, so uid 65534 can read and
  nothing can write (the root file system is also read-only at run time, §7). `build` sets the
  modes before the build, as `_world_readable` does for the agent context.
- **Identity:** `docker build --iidfile`; the `sha256:` image ID is recorded in the build record
  (`<name>.build.json` `skill_sandbox_image`) and the manifest (§4). Runs always use the ID, never a
  tag, so a later build or a pulled tag cannot replace it.
- **Labels:** `io.agentbreeder.role=skill-sandbox`, `io.agentbreeder.agent=<name>`.
- **Platform:** the same `docker_platform` check as the agent image; the M13 package records it
  (§4.3).
- **Placed files match the declarations.** `build` checks each placed script's SHA-256 against
  the hash it writes into the manifest, from the same bytes, so the manifest's `sha256` is the
  file in the image.
- **The image is checked before it is trusted** (deploy, runner start and every `reconcile`):
  `docker image inspect <ID>` must show the label `io.agentbreeder.role=skill-sandbox`,
  `Config.User` `65534:65534`, the `Entrypoint` above, no `Volumes` (anonymous volumes stay writable
  under `--read-only`; `runtime.py` already refuses `VOLUME` for agent images), no `Healthcheck`,
  and an `Env` **equal to the set `build` recorded** from the built image (the base image sets
  `PYTHON_VERSION`, `LANG`, `PATH` and similar, which are expected). As a second check, no name in
  `Env` may be `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`, `PYTHONUSERBASE`, `PYTHONINSPECT`,
  `PYTHONEXECUTABLE`, `PYTHONBREAKPOINT` or start with `LD_` (`python -I` ignores `PYTHON*` variables anyway; `LD_*`
  it does not). The approved set travels with the image: `build` records it in the manifest
  (`skill_sandbox.env`, §4.1, so it is digest-covered and gate-approved), the package mirrors it
  (`sandbox_image.env`, §4.3), and deploy copies it into the pin, so deploy, runner start and
  `reconcile` compare against the manifest or pin, never a build record that may not exist on the
  host (a package deployed elsewhere, or a deleted `deploy/` directory). A hand-written manifest or third-party
  package naming another local image is refused (`SKILL_SANDBOX_IMAGE_INVALID`). Nobody reviews
  script content at gate time: the reviews are the publisher's and the operator's (`registry
  show`, the import confirm), and the docs say so.

## 4. Contract fields (K6a)

### 4.1 Deploy manifest

`Manifest` gains an optional field, valid only with `schema_version: 2` and `model_access`
(built agents):

```yaml
skill_sandbox:
  image: "sha256:<64 hex>"
  env: ["PATH=...", "LANG=C.UTF-8", "PYTHON_VERSION=3.12.x", ...]   # the image's Config.Env at build: ≤ 32 entries, each NAME=value, NAME ^[A-Za-z_][A-Za-z0-9_]{0,63}$, ≤ 1,024 characters; the package field has the same limits
  scripts:                      # 1–16 entries
    - tool: skill_refund_policy__prorate        # ^skill_[a-z0-9_]{1,24}__[a-z][a-z0-9_]{0,23}$ (≤ 55)
      skill: refund-policy                      # the skill name
      version: 1.2.0
      script: prorate
      module: prorate                           # scripts/<module>.py, a valid module name
      sha256: "sha256:<hash of the script file as placed>"
      parameters: {type: object, properties: {...}, required: [...]}   # §6.4 subset, ≤ 2 KiB canonical JSON
```

- **Why the declarations are in the manifest:** the runner's allow-list must come from the
  gate-approved, digest-covered document (skills spec §9.5), never from the agent's request or the
  image. With at most 16 scripts per deployment at ≤ 2 KiB of schema each, the field stays well
  inside the 64 KiB manifest cap together with the rest (the cap is unchanged; a manifest over it
  is refused as today).
- **16 per deployment** (`SKILL_SCRIPTS_TOO_MANY` at build), not the skills spec's theoretical
  10 × 10: a deployment needing more is a design smell and the cap keeps the manifest bounded. §13 A3.
- `tool` values are unique and must equal `skill_<skill with - as _>__<script>`; `(skill, version)`
  pairs are consistent across entries.
- **Digests.** Two digests read the manifest, and both must leave existing deployments alone:
  - `Manifest.digest()` (the spec digest) excludes `skill_sandbox` when it is `None`, as it does
    for `model_access` and `mcp_access`;
  - `gate.revision_digest` hashes `manifest.model_dump(mode="json")` with no exclusion, so a new
    field would add `"skill_sandbox": null` to every revision and make every stored approval and
    revision binding stale once. K6a dumps the manifest there with `skill_sandbox` excluded when
    `None`, and a test pins a revision digest computed on `main` for a manifest without it.
  When present the field is covered by both, so any change to the image or a declaration is a new
  revision.
- **Operator config.** `Environment.skill_scripts` (§5) does not touch the policy
  `SCHEMA_VERSION` (no Cedar attribute) or any revision digest: `operator.digest` hashes the
  config file's bytes, which change only when an operator edits the file.
- `docs/manifest.md` and `docs/manifest.schema.json` gain the field; `tests/test_manifest_v2.py`
  covers validation and the digest rule.
- **Kubernetes:** a `local_image`-style validator is not enough (the image ID is always local), so
  the refusal is in `_kubernetes_requirements` (`SKILL_SCRIPTS_LOCAL_ONLY`), the single engine check
  every cluster path calls (skills spec §9.6).

### 4.2 Deploy template and bind

- `DeployTemplate` gains `skill_scripts: tuple[SkillScript, ...]` (max 16, default empty), the
  declarations above without `image`: they derive from the agent and its pinned skills only, so the
  template stays environment-neutral and a released package can carry it.
- **Serialisation:** `DeployTemplate` is strict and dumped as a whole into `package.json`
  (`Package.to_bytes`), so a plain new field would write `"skill_scripts": []` into every package
  and change its bytes and hash. The field is excluded from the dump when empty (a
  `model_serializer` on `DeployTemplate`, used by `to_bytes` and every `model_dump` of it), and a
  test checks a version 1 package from `main` round-trips byte for byte.
- `bind(deploy, image, operator, environment, identity, name, sandbox_image=None)` writes
  `skill_sandbox` when `skill_scripts` is non-empty and refuses (`MANIFEST_INVALID`) when one is
  given without the other.
- A template with no `skill_scripts` serialises as before (the key is omitted), so existing
  packages' `deploy` hashes do not change.

### 4.3 M13 package

- `package_version` becomes **2** only for packages with a sandbox: `Package` gains optional
  `sandbox_image` (`ImageInfo` plus the `env` list of §4.1), `package_version` becomes `Literal[1, 2]`, and the parser's
  `version != PACKAGE_VERSION` check becomes a set of supported versions, with a validator that
  version 2 needs `sandbox_image` and `deploy.skill_scripts`, and version 1 forbids both. Packages
  without a sandbox stay version 1, byte for byte. An older CLI refuses a version 2 package with
  its existing "unsupported package_version (written by a newer AgentBreeder?)" message. §13 A4.
- Every caller of `bind` and the package code passes and checks the sandbox image:
  `build.py`, `deploy_ref.py` (`_bind`), `release.py` (the package writer and its local-image
  check before rollback), and the console's deploy path in `center/` (lane B agrees). Deploying a
  package checks both IDs exist locally before the gate (`IMAGE_MISSING` names which).

## 5. Gate and operator controls (K6a)

- **Environment opt-in, default deny.** The operator config's environment gains
  `skill_scripts: false | true` (default `false`). A manifest with `skill_sandbox` deploying into an
  environment without `skill_scripts: true` is refused in `_deploy_requirements`
  (`SKILL_SCRIPTS_NOT_ALLOWED`), so `deploy`, `preflight` and `release --rollback` all see it. When
  `_deploy_requirements` is called without an environment (it defaults to `None`), a manifest with
  `skill_sandbox` is refused too: fail closed. This
  is the "presence" control of D12 made explicit: a script skill is new code that runs on the host's
  Docker engine, and an operator turns that on per environment. §13 A2.
- **No policy input in v1.** The gate's policy request (`gate.py`, `policy_request`) carries a
  fixed set of resource properties (environment, target, risk tier, principal, tool actions,
  approval required, guardrails), not the manifest, so no Cedar, OPA or AuthZEN policy can see
  `skill_sandbox` today. The v1 controls are the environment opt-in above and the audit detail
  below (D12, "presence only"). This corrects the skills spec §9.5, which said a policy could deny
  through the manifest field; a status note there points here. A `skill_scripts: bool` resource
  property is a later, separate policy-input change with its own tests.
- **Audit.** The deploy event's detail gains `skill_sandbox: {image, tools: [...]}` (names, no
  schemas), beside the skills spec's `detail.skills`.
- **Runtime enforcement mode:** refused already by `MODEL_ACCESS_UNSUPPORTED` (built agents cannot
  run behind the egress boundary); a test pins that a manifest with `skill_sandbox` gets that code
  under enforcement.

## 6. The runner (K6c)

### 6.1 Process

- Module `agentbreeder.skills_runner` (package `src/agentbreeder/skills_runner/`: `host.py`,
  `container.py`, `schema.py`, `__main__.py`), run as `python -m agentbreeder.skills_runner
  --state-dir DIR --deployment ID`, started with `start_new_session=True`, `stdin=DEVNULL`,
  `close_fds=True` and a private log file, exactly like the egress decision service
  (`Boundary.start_service`).
- The shared mechanics of `egress/boundary.py` (writing a private pin, `listen_mode`,
  `free_loopback_port`, the PID check by command line, signed health) move to a small
  `hostservice.py` used by both, in a refactor commit with the egress tests unchanged.
- **State directory:** `<state>/skills/` and `<state>/skills/<deployment id>/` are `0700`; inside,
  `pin.json` (`0600`), `endpoint.json` (`{pid, address}`), `runner.log` (`0600`), `runner.lock`, and
  on Linux `sock/` (`0755`) holding `runner.sock` (`0666`: connecting needs write permission, and
  the agent runs as uid 65532). Host-user isolation of the socket rests on the two `0700` parents,
  exactly as for the egress socket (`egress/boundary.py`, `make_server`).
- **`pin.json`:** `{deployment, revision (manifest digest), request_key, reply_key, listen, port?,
  docker_context, image, env, scripts (from the manifest), limits}`. Written by deploy from the manifest
  and the deploy's Docker context only. The keys are derived from the token (§6.3); the token itself
  is not stored.
- **One runner per deployment.** The runner takes an exclusive `flock` on `runner.lock` at start
  and exits if it cannot, so a slow-exiting predecessor and a `reconcile` restart never run
  together (and never clean up each other's containers).
- **Same engine as the agent.** The runner calls Docker through the same prefix as the deploy path
  (`runtime.Docker._prefix`: `docker --context <ctx>`) with the context from the pin, and drops
  `DOCKER_HOST`, `DOCKER_CONTEXT`, `DOCKER_TLS_VERIFY` and `DOCKER_CERT_PATH` as `Docker._environment`
  does. It is started with a scrubbed environment (`PATH`, `HOME`, `LANG`, `PYTHONPATH` for the
  package, and nothing else), unlike the egress service, which needs the operator's environment for
  credential references; the runner has no credentials to resolve.
- **Exit:** like the decision service, it exits after two idle checks once the deployment is no
  longer `starting`/`ready`, and on SIGTERM; on exit it kills its running containers (§7.3).

### 6.2 Transport

- **Linux (Docker Engine):** a Unix socket at `<state>/skills/<id>/sock/runner.sock`. The `sock/`
  **directory** (never the socket file, whose inode a restarted runner replaces, and never its
  parent, which holds `pin.json`) is bind-mounted read-only into the agent container at
  `/run/agentbreeder/skills/`. Other host users cannot traverse the `0700` parents; other containers
  cannot reach it (it is on no network). This is stronger than the skills spec's §13.3 statement,
  which assumed TCP via the host gateway on Linux too. §13 A1.
- **Transport probe (both modes).** Before the agent is created, deploy runs a throwaway container
  from the sandbox image with the run's security options and the agent's network setup (the `sock/`
  mount on Linux; the default bridge plus `host.docker.internal` on Docker Desktop) that calls the
  signed `/healthz`; a failure is `SKILL_SANDBOX_TRANSPORT`. On Linux this also catches the case
  below.
- **Why Linux needs it.** `listen_mode` returns `unix` for every engine whose `docker info` does not say
  "Docker Desktop", but Colima, OrbStack and Rancher Desktop on macOS cannot mount host sockets
  either. The probe above turns that into `SKILL_SANDBOX_TRANSPORT` with a hint at deploy, never a
  deploy that fails at the first run. A
  setting to force TCP is a later option if such engines need it.
- **Docker Desktop:** host Unix sockets cannot be bind-mounted (`listen_mode`'s comment,
  `EOPNOTSUPP`), so the runner listens on `127.0.0.1:<free port>` and the agent uses
  `http://host.docker.internal:<port>`, as the egress decision service does. Any local process and
  any container on the engine can reach that port; the request-signing keys derived from the token are the control there (§6.3, §11).
- The agent learns the address from `AGENTBREEDER_SKILLS_URL`
  (`unix:/run/agentbreeder/skills/runner.sock` or the TCP URL) and the token from `AGENTBREEDER_SKILLS_TOKEN_FILE` (the generated secret is named `AGENTBREEDER_SKILLS_TOKEN`;
  `runtime.create` appends `_FILE` for mounted secrets); the runtime version's
  `manifest.yaml` lists both.

### 6.3 Authentication and reply integrity

**The runner never sends its token; request and reply signatures use separately derived keys.**
On Docker Desktop the runner's port is loopback TCP, which another local process could take after
the runner died, so nothing on the wire may let that process forge replies or act later.

- **Token:** `secrets.token_bytes(32)`, minted per container generation in the deploy create path
  (as `_grant_mcp` mints relay tokens), written with `secret_files.write_generated` to the
  deployment's private secret directory and mounted read-only into the agent only.
- **Keys:** `request_key = HMAC-SHA256(token, "agentbreeder-skills-request-v1")`,
  `reply_key = HMAC-SHA256(token, "agentbreeder-skills-reply-v1")` and
  `audit_key = HMAC-SHA256(token, "agentbreeder-skills-audit-v1")` (§10). The pin stores the three
  keys, not the token. The agent derives the first two from its token file.
- **Request:** headers `X-AgentBreeder-Nonce` (32 random hex), `X-AgentBreeder-Time` (Unix seconds,
  the agent's clock plus the offset it learned from `/healthz`, below)
  and `X-AgentBreeder-Signature = HMAC(request_key, method \n path \n nonce \n time \n
  sha256(body))`. The runner checks, in this order, before reading the body: the header shapes,
  `Content-Length` present and ≤ 16 KiB (chunked or missing is refused), time within ±60 s of the
  runner's clock and not earlier than the runner's own start time (so a request captured before a
  restart cannot be replayed to the new runner after its in-memory nonce cache was emptied); then it
  reads exactly that many bytes (2 s read timeout) and checks the signature with
  `hmac.compare_digest`, then that the nonce was not seen in the last 130 s (twice the time window
  plus a margin, so a request stamped at the edge of the window cannot outlive its nonce; about 130
  entries at the per-minute limit of §8; when full it refuses, never evicts). Only
  then is the body parsed (§6.4). A time outside the window is refused as `SKILL_CLOCK_SKEW`, so
  a Docker VM clock that drifted after host sleep is diagnosable.
- **Reply:** `X-AgentBreeder-Reply-Signature = HMAC(reply_key, nonce \n status \n sha256(body))`.
  **The runtime verifies it** and treats a bad or missing signature as `SKILL_SANDBOX_UNAVAILABLE`.
- **Health before every run.** Before each `/run` the runtime calls `/healthz` with a fresh nonce
  and checks the reply signature; only then does it send the run, on a new connection. A squatter
  cannot sign a health reply, so it never receives arguments unless it takes the port in the
  moment between the health check and the run.
- **What a squatter gets** (Docker Desktop only, after the runner died and another local process
  took its port): signed health checks it cannot answer, and, only in that window, one request body
  (script arguments, which may hold customer data). It cannot forge replies, replay a request, or
  learn the token. `status` and `reconcile` detect it (no valid health signature, §9). §11 T6.
- **No keep-alive.** The runner answers every request with `Connection: close`, so idle
  connections never hold the connection cap. It binds without `SO_REUSEPORT`, so it cannot share a
  port with another listener; the fixed-port race between `free_loopback_port` and the bind is
  caught by the signed health check at deploy.
- **Rotation:** a recreated container gets a new token; the pin is rewritten and the runner
  restarted before the agent starts, so an older token's keys stop working.
- **Health:** `GET /healthz` with a nonce header returns `{revision, time}` signed with
  `reply_key`. The runtime calls it at start and after a `SKILL_CLOCK_SKEW`, and signs with its
  clock corrected by the measured offset, so VM clock drift does not fail runs. It is
  unauthenticated and cheap (no Docker call, no store read), for `status` and `reconcile`.

### 6.4 Requests

`POST /run`, body ≤ 16 KiB, JSON object:

```json
{"turn": "<parent turn id>", "tool": "skill_refund_policy__prorate", "args": {"price": 30, "days_used": 10, "days_in_period": 30}}
```

- `tool` must equal one `scripts[].tool` in the pin (`SKILL_SCRIPT_UNDECLARED` otherwise). The
  runner derives skill, version, module and image from the pin; the request cannot name them.
- `turn` matches `^[A-Za-z0-9-]{1,64}$`; it is recorded, not trusted for anything.
- **Parsing** (only after §6.3's checks): `json.loads` with `egress.protocol`'s
  `_unique_pairs` (duplicate keys refused), `_no_constants` (`NaN`, `Infinity` refused) and
  `_finite_float`; integers limited to 19 digits by a `parse_int` hook, so Python's digit-limit
  `ValueError` cannot surface as a crash; nesting deeper than 4 refused before validation; any
  parse error is `400 {"code": "SKILL_ARGS_INVALID"}`, never a 500.
- `args` is validated against the declared `parameters` with a strict validator of the subset the
  skills spec allows (`schema.py`, new; the tool path today only filters argument names in the
  runtime): `type` object at the top; properties of type `string` (≤ 4,096 characters), `number`,
  `integer`, `boolean`, `array` (≤ 100 items of a scalar type); `enum` of scalars; `required`; no
  `$ref`, `pattern`, `format`, nested objects or `additionalProperties: true` (extra keys are
  refused). **Type checks are exact:** `bool` is not accepted for `integer` or `number` (Python's
  `bool` is an `int`), and `number` accepts `int` and finite `float` only. Publish (K1/K6b) refuses
  a `parameters` block outside the subset, so a declared schema is always checkable. Invalid →
  `SKILL_ARGS_INVALID` with the failing property name, never the value.
- Canonical args (`json.dumps(sort_keys=True, separators=(",", ":"))`) go to the container on
  stdin; never argv, never environment.

### 6.5 Replies

`200 {"ok": bool, "stdout": str, "stderr_tail": str, "exit_code": int|null, "duration_ms": int,
"code": str|null}`. `ok` is true only for exit code 0 within limits. `stdout` (at most 64 KiB) and
`stderr_tail` (the last 4 KiB) are decoded as UTF-8 with replacement and passed through
`redaction.redact`; nothing secret should reach a script, but the model sees both. Exit codes 125,
126 and 127 from `docker run` itself (the engine failed, or the command could not start) are
`SKILL_SANDBOX_UNAVAILABLE`, not a script failure. Exit 137 is `SKILL_SCRIPT_TIMEOUT` when the
elapsed time from `docker run` start is at least 9.5 s, whichever layer killed it (PID 1's 10 s
starts after container start, so on a slow start the runner's 11 s kill may come first, and that is
still a timeout); earlier it is `SKILL_SCRIPT_KILLED` (the
memory limit's OOM kill also exits 137), so a script that exhausts memory is not reported as slow. Error codes (§8) come back as `409 {"code"}` for refusals and `503 {"code"}`
for capacity or audit failures. The runtime returns all of them to the model as a tool error with
the code, never runner internals.

## 7. The per-run container (K6c)

### 7.1 Command

```
docker run --rm -i --pull never
  --name agentbreeder-skill-<deployment id, first 12>-<run id, 16 hex>
  --label io.agentbreeder.deployment=<id> --label io.agentbreeder.role=skill-run
  --network none --ipc none --hostname sandbox
  --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m
  --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges
  --pids-limit 32 --memory 256m --memory-swap 256m --cpus 0.5
  --ulimit nofile=64:64 --ulimit cpu=10:10 --ulimit fsize=16777216:16777216 --ulimit core=0:0
  --label io.agentbreeder.deadline=<epoch seconds, start + 15>
  --log-driver none --no-healthcheck --stop-signal KILL --stop-timeout 1
  --workdir /skills/<skill>
  <sandbox image ID> python -I -S /skills/<skill>/scripts/<module>.py
```

- Every value except the run id comes from the pin; the run id is random. The runner builds the
  argument list (no shell).
- `docker run` passes no host variable without `-e`/`--env`/`--env-file`, so the environment is
  only the base image's own `ENV` (`PATH`, `LANG`, `PYTHON_VERSION` and similar, none secret). A
  test asserts the argument list has no `-e`/`--env`/`--env-file` and no `-v`/`--mount`.
- `-I -S`: isolated mode (no user site, no `PYTHON*` variables, no script directory on
  `sys.path`) and no `site`, so only the standard library loads.
- The image's entrypoint (`timeout`, §3) is PID 1; the command above is its child. No `--init`.
- `--ulimit core=0`: on Linux `core_pattern` is host-wide, and a pipe handler (apport,
  systemd-coredump) would run on the host for a crashing container process; no core is written.
- **Docker's default seccomp profile stays on** (no custom profile, §7.3); no `--privileged`, no devices, no `--userns=host`.
  The design assumes rootful Docker (Docker Desktop, or Docker Engine as root); rootless Docker
  adds a user namespace and is not tested.

### 7.2 I/O

The runner writes the canonical args to stdin and closes it (a `BrokenPipeError` from a script
that does not read stdin is ignored), reads stdout and stderr **concurrently** (one thread each, or
`selectors`) so neither pipe can fill and stall the other, stops at 64 KiB of stdout (one byte more
→ kill, `SKILL_OUTPUT_TOO_LARGE`), keeps a 4 KiB ring of stderr, and waits for the exit status.

### 7.3 Stopping and cleanup

- Four layers stop a run:
  1. PID 1 `timeout` kills the script at 10 s and the container exits. This is **a convenience
     against buggy or hung scripts only**: `timeout` and the script share uid 65534, so a hostile
     script can stop it (`ptrace`, or writing `/proc/1/mem`, which seccomp does not cover). No
     custom seccomp profile is used: a vendored "default minus `ptrace`" profile would drift from
     the engine's own default and could break Python on newer engines, while not closing
     `/proc/1/mem`. Docker's default profile stays on. Layers 2 and 3 are the **authoritative**
     deadline, and neither depends on anything inside the container.
  2. The runner `docker kill`s the container by name at 11 s from `docker run` start (killing the
     `docker run` client alone would leave it running), then waits up to 2 s for the client and
     kills it.
  3. **A detached reaper per run** (authoritative even if the runner dies), started before `docker run` with `start_new_session=True` as
     `python -m agentbreeder.skills_runner.reap --context <ctx> --name <name> --after 12` (argv
     only, no shell, the runner's scrubbed environment), which sleeps 12 s, runs `docker rm -f`
     on that name and exits; so a run is removed even if the runner is SIGKILLed. The runner kills
     its reaper through its `Popen` handle, only while `poll()` is `None` (never a stored pid or
     process group, which could be reused), and tolerates one that already ran. A reaper from an
     earlier run that outlives a runner restart is harmless: `docker rm -f` on a name already gone
     does nothing, and every run name carries its own 16-hex run id. The reaper imports only the
     standard library, carries no secret in its argv, finds `docker` through the same scrubbed
     `PATH` as the runner, and appends a failure to `runner.log`. At the request limit that is about
     60 short-lived processes a minute. If
     the reaper dies too, layer 4 is passive: the container lives until the next request,
     `reconcile`, `remove` or runner start.
  4. Every container carries `io.agentbreeder.deadline`, and the runner (on each request and at
     start), `reconcile` and `remove` remove any of the deployment's skill-run containers past it.

  The run returns `SKILL_SCRIPT_TIMEOUT` (or `SKILL_SCRIPT_KILLED`, §6.5). K6f checks a script
  that writes to `/proc/1/mem` or `ptrace`s PID 1 and sleeps (the container is still removed, by
  the runner at 11 s or, with the runner SIGKILLed, by the reaper within 20 s).
- `--rm` removes the container; `docker rm -f` by name follows any run whose client ended
  abnormally.
- **Leftovers:** on runner start, on runner exit and in `remove`/`disable`, every container with
  `io.agentbreeder.deployment=<id>` and `io.agentbreeder.role=skill-run` is removed with
  `docker rm -f`. The filter also requires the name prefix `agentbreeder-skill-<id12>-`; a
  container matching only one of the two is reported, not removed. Labels and names are set by
  whoever has Docker access, which is root-equivalent anyway, so this guards against mistakes, not
  an attacker. The runner lock (§6.1) stops a new runner from removing a live predecessor's runs.
- **Host slots** are `flock`s held by the runner process, so they are released when it dies while a
  container may still be running for up to 20 s. Separately from the slots, before taking one the
  runner counts the engine's live `role=skill-run` containers of every deployment (on its own
  Docker context; containers being removed by `--rm` count as live) and waits if they already fill
  the host limit, so a runner crash cannot let the host exceed it for longer than one reaper delay.
- **Scope of the cap.** The slot and marker files live under `<state>/skills/slots/`, and `<state>`
  is per operator environment, so the hard cap is **4 per state directory**. The engine-wide count
  is best effort (two runners can both count 3 and both start), so an engine shared by several
  environments may briefly run more. The docs state the cap that way.
- **Fairness:** a runner waiting for a slot holds an exclusive `flock` on its own marker file
  `<state>/skills/slots/waiting-<deployment>` (a lock, not the file's existence, so a crash leaves
  no stale signal). To see whether another deployment is waiting, a runner tries each marker with a
  non-blocking `flock` (`LOCK_NB`) and releases it at once; it retries a failed probe with a short
  random delay rather than blocking, so the probe itself never looks like a waiter for long. A runner
  that sees any other deployment's marker locked runs at most 1 at a time. A compromised deployment that waits forever can hold others to 1; that is accepted and
  stated (§11 T8).

## 8. Limits and codes

| Limit | Value | Where |
|---|---|---|
| Script runs per turn | 5 (D9) | runtime `AgentPlugin` |
| Concurrent runs per deployment | 2, or 1 while another deployment waits for a host slot (§7.3); a further run waits ≤ 3 s, then `SKILL_SANDBOX_BUSY` | runner (bounded semaphore) |
| Concurrent runs per state directory | 4; a fifth waits ≤ 3 s, then `SKILL_SANDBOX_BUSY` | runner (four `flock` slot files under `<state>/skills/slots/`, plus the live-container count, §7.3) §13 A6 |
| Authenticated requests per deployment per minute | 60 (runs and refusals together), checked before any audit write | runner |
| Wall clock per run | 10 s | runner |
| CPU, memory, pids, files, tmp, file size | 10 s CPU, 256 MiB (no swap), 32, 64, 16 MiB, 16 MiB | `docker run` |
| Request body | 16 KiB | runner |
| stdout / stderr kept | 64 KiB / last 4 KiB | runner |
| Scripts per deployment | 16 | build, manifest |
| Connections | at most 8 open per runner, accept backlog 8, no keep-alive; headers and body must arrive within 2 s | runner handler |

**Timeouts nest** (one budget, worst case of each step):

| Step | Budget |
|---|---|
| Wait for a deployment and a host slot | ≤ 3 s |
| Write the start audit row | ≤ 1 s (busy timeout), else `AUDIT_UNAVAILABLE` |
| The run (PID 1 deadline 10 s; runner kill at 11 s; client wait ≤ 2 s) | ≤ 13 s |
| End row, reply | best effort, ≤ 1 s |
| **Runner, per request** | **≤ 18 s**; the connection is closed at 19 s |
| **Runtime client timeout** | **22 s** (health check ≤ 2 s before it, separately) |

So the runtime never reports `SKILL_SANDBOX_UNAVAILABLE` while the run is still in progress.

| Code | Meaning |
|---|---|
| `SKILL_SCRIPTS_NOT_ALLOWED` | The environment has no `skill_scripts: true` (deploy requirements). |
| `SKILL_SCRIPTS_LOCAL_ONLY` | Cluster target (`_kubernetes_requirements`). |
| `SKILL_SCRIPTS_TOO_MANY` | More than 16 script tools (build). |
| `SKILL_NAME_CLASH` | A script tool name equals another tool name (build). |
| `SKILL_SCRIPT_UNDECLARED` | `tool` not in the pin (runner). |
| `SKILL_ARGS_INVALID` | Args fail the declared schema (runner). |
| `SKILL_SCRIPT_TIMEOUT` | Killed at 10 s (runner). |
| `SKILL_OUTPUT_TOO_LARGE` | stdout over 64 KiB (runner). |
| `SKILL_SANDBOX_BUSY` | No slot within 3 s (runner). |
| `SKILL_SANDBOX_UNAVAILABLE` | Runner unreachable, unhealthy, wrong revision, or a bad or missing reply signature (runtime). |
| `SKILL_CLOCK_SKEW` | The request time is outside the runner's window (runner); the runtime re-measures the offset once and retries. |
| `SKILL_SCRIPT_KILLED` | The container was killed before the deadline (for example out of memory), with the exit code (runner). |
| `SKILL_SANDBOX_IMAGE_MISSING` | The pinned image ID is not on the engine (runner start, deploy). |
| `SKILL_SANDBOX_IMAGE_INVALID` | The image fails the §3 inspection (label, user, entrypoint, volumes, env). |
| `SKILL_SANDBOX_TRANSPORT` | The agent cannot reach the runner's socket on this engine (§6.2 probe). |
| `AUDIT_UNAVAILABLE` | The run's audit row could not be written; nothing ran (runner). |

The run limit lives in the runtime and the concurrency limits in the runner, so a compromised
agent can ignore the per-turn cap but not the concurrency, host or per-run limits.

## 9. Deployment lifecycle (K6d)

| Step | Change |
|---|---|
| `deploy`, new container generation | Inside `_run`'s create branch, after the gate and approval and inside the lifecycle lock, in this order: inspect the image (§3); mint the token and write it with the other generated secrets (the `generated` map beside the gateway key and MCP tokens); derive the keys and write `pin.json`; start the runner and wait ≤ 15 s for a signature-verified `healthz` with the current revision; run the mount probe (§6.2); then create the container with the token file (and on Linux the `sock/` directory) mounted and `AGENTBREEDER_SKILLS_URL` set. |
| `deploy`, existing container (retry, restart, after a reboot) | On every `_run`, not only on create: inspect the image; if the pin is missing or its revision is not the manifest's, or the token file is missing (`secret_files.present`), take the **recreate path** (remove the container, new generation as above), because the pin holds the only copy of the derived keys and an old token is useless without it; otherwise ensure the runner is alive and healthy, starting it from the pin if needed, before the container starts. |
| failed attempt | The `failed` cleanup stops the runner of that attempt; the idle exit (§6.1) is the backstop if cleanup itself fails. |
| retry / recreate | A new container generation rewrites the token and pin and restarts the runner (as `_grant_mcp` revokes before minting), so an old token never stays valid. |
| Docker Desktop port | The port is fixed in `pin.json` and in the agent's environment. A restarted runner reuses it; if another process holds it, the runner does not start, `status` shows `down`, and runs fail closed (`SKILL_SANDBOX_UNAVAILABLE`) until the deployment is redeployed, which picks a new port. |
| `status` | Shows `skills runner: ok | down | wrong revision` for a deployment with `skill_sandbox` (PID by command line plus signed `healthz`). |
| `reconcile` | Lists any stale `skill-run` containers (also shown by `status`) and removes those past their deadline. Today it "never starts anything" except the boundary's decision service and sidecar (`_ensure_boundary`). It gains the same exception for the runner: a dead or wrong-revision runner of a `ready` deployment is restarted from the pin (stateless) with an audit event `skill_runner_restarted`; a runner pin left by a `failed`, `disabled` or removed deployment is stopped and its runs reaped; a missing or invalid sandbox image is reported (`SKILL_SANDBOX_IMAGE_MISSING`/`_INVALID`), not repaired. `docs/commands/reconcile.md` and the docstring change with it. |
| kill switch (`disable`) | Stop the runner first (so new runs fail closed), reap its run containers (§7.3), then stop the agent as today. |
| `enable` | Starts nothing, as today; the next deploy creates a new container generation, token and runner. |
| `remove` | As `disable`, then delete `<state>/skills/<id>/` and the token file. **The sandbox image is kept**, like the agent image, which no lifecycle command removes (release rollback relies on local images); image cleanup is left to the operator. |
| host restart | Runners do not survive a reboot; the next `deploy`/`_run` or `reconcile` starts them for `ready` deployments. |

## 10. Audit and observability

- **Two rows per run** (§13 A5):
  - **Writing from the runner:** a long-lived process appending to the hash chain contends with CLI
    writers for the store's `BEGIN IMMEDIATE` lock. The runner opens the store at start, checks its
    schema version (refusing to start on a mismatch), sets a 2 s busy timeout, and holds no
    transaction between writes. The relay-audit pattern (#1068: a file the CLI imports later) was
    considered and not used, because the start row must be durable in the chain **before** a run.
  - `skill_script_start`, written **before** the container starts, fail closed: `{deployment, run,
    turn, tool, skill, version, script_sha256, args_hmac}`. If it cannot be written within 1 s
    (store busy or broken) the run does not start, its slots are released and the reply is
    `AUDIT_UNAVAILABLE`.
  - `skill_script_end`, written after the run, best effort: `{run, exit_code, duration_ms,
    stdout_bytes, outcome}`. At start the runner writes an `outcome: "unknown"` end row for any
    start of its deployment without one (a runner crash), so the chain never leaves a run open.
  - Never the args or output. `args_hmac` is `HMAC(audit_key, canonical args)`, not a plain hash,
    so low-entropy arguments (order ids, account numbers) cannot be recovered by guessing. Values
    are comparable only within one token generation, which is enough for "the same arguments twice
    in a turn".
- **Refusals after authentication** (`SKILL_SCRIPT_UNDECLARED`, `SKILL_ARGS_INVALID`) count
  against the same 60-per-minute limit as runs, checked **before** any audit write. The first 10
  refusals in a minute are one row each; the rest are coalesced into one `skill_runner_refusals`
  summary row per minute with counts by code. A request over the limit writes nothing and is
  counted in that summary. So one deployment adds at most about 121 rows per minute (60 runs with
  a start and an end row each, plus one summary), even with a compromised agent.
- **Unauthenticated requests are never audited one by one:** the runner counts them and writes at
  most one `skill_runner_unauthenticated` summary row per minute with the count, so a local process
  cannot flood the chain or hold the store's write lock.
- The runtime's agent event and `TOOL` span (skills spec §11) carry the same fields plus the
  stdout as span output, capped like tool output.
- `runner.log` holds start/stop and errors without args or output; it is `0600`.

## 11. Threat model

Assets: the operator's host and Docker engine; the deployment's secrets (gateway key, MCP relay
tokens, `DATABASE_URL`); other deployments; customer data in the agent's database; the audit chain.

| # | Threat | Control | Residual |
|---|---|---|---|
| T1 | A malicious script reads secrets or customer data | The sandbox has no secret files, no mounts, only the image's own environment, only skill files; the agent's data is only reachable over a network the container does not have | none identified in design; unverified until K6f |
| T2 | A malicious script exfiltrates over the network | `--network none`: loopback only, no resolver, no route | covert channels through its own stdout to the model (T4, T17) |
| T3 | A script escapes the container | Non-root uid, all capabilities dropped, `no-new-privileges`, read-only root, Docker's default seccomp, pids/memory/CPU limits, no devices, no core dumps | kernel or runtime bugs, as for any container; a microVM/gVisor runner is a later option (skills spec §9.1) |
| T4 | A script's output steers the model (calls a write tool, leaks context) | Output is an untrusted tool result; write tools only file requests a person decides; 64 KiB cap | prompt injection by a publisher whose skill the operator pinned; mitigated only by review (`registry show`, import confirm) |
| T5 | Data persists between runs or customers | New container per run, `--rm`, tmpfs, no volumes (inspected, §3), read-only image | none identified in design; unverified until K6f |
| T6 | A local process takes the runner's loopback port after the runner died (Docker Desktop) | The token never crosses the wire; requests and replies are signed with derived keys, with nonce, time window and replay cache (§6.3); the runtime rejects unsigned replies; `status`/`reconcile` detect the squatter | a signed health check precedes every run, so the squatter sees arguments only if it takes the port between that check and the run; it can drop requests until the runner is restarted |
| T7 | A local process or another container calls the runner | Linux: socket under `0700` parents, on no network. Docker Desktop: loopback TCP reachable by local processes and by containers via `host.docker.internal`; a signed request is required | the operator user and root can reach it, as they can reach Docker itself; on Docker Desktop a local process can hold the 8 connections and starve the agent (availability only) |
| T8 | A compromised agent container abuses the runner | It holds the token, so it can run declared scripts with valid args; it cannot choose images, files or commands; per-deployment concurrency, request rate (runs and refusals) and host fairness limits (§8); audit rows bounded per minute (§10) | it can keep its share of host slots busy, and by always waiting it can hold other deployments to one run at a time |
| T9 | Argument or command injection into `docker run` | Args only on stdin as canonical JSON; every argv element from the pin and validated patterns; no shell | none identified in design |
| T10 | Image substitution | Runs by image ID from the digest-covered manifest; image inspected (§3); a missing or invalid ID fails closed; base image pinned by digest | a compromised Docker engine, out of scope |
| T11 | Host resource exhaustion | Per-run limits, per-deployment caps, a cap of 4 per state directory with a best-effort engine count (§7.3), rate limit, bounded connections, `--log-driver none`, 16 MiB tmpfs | many deployments each at their share |
| T12 | Runner left running, or containers orphaned after a runner crash, or a script stopping its own deadline | Kill switch stops the runner before the agent; idle exit; the runner's kill at 11 s and a detached reaper per run (authoritative; PID 1 `timeout` only for buggy scripts, since a hostile one can stop it); deadline label reaped on every request, `reconcile` and `remove` | a run ends within 20 s while the runner or its reaper lives; if both die, at the next request, `reconcile`, `remove` or runner start |
| T13 | Audit tampering or loss | Start row before the run, fail closed; end row or an `unknown` end at the next start; summary rows for unauthenticated noise | the chain detects corruption; it does not make tampering impossible (a DB owner can recompute it); the docs say so |
| T14 | A hand-written or third-party manifest names an arbitrary local image | Image inspection (§3): label, user, entrypoint, no volumes, `Env` equal to the approved set with no interpreter-altering names | a hand-written manifest supplies both the image ID and its `env`, so the `Env` equality only constrains AgentBreeder-built manifests; the label, user, entrypoint and no-volumes checks and root-equivalent Docker access are the real bound |
| T15 | The runner runs scripts on a different engine than the agent | Docker context from the pin, Docker variables dropped (§6.1); the mount probe and health check run against the same engine | none identified in design |
| T16 | A crashing script triggers the host's core-dump helper | `--ulimit core=0` | none identified in design |
| T17 | JSON parser abuse (duplicate keys, `NaN`, huge integers, deep nesting) | Signature checked before parsing; strict parser hooks and limits (§6.4) | none identified in design |
| T18 | A tool in the agent process runs a script "owned" by another sub-agent | None: the runner cannot tell agents in one process apart, consistent with the sub-agents spec §12.2 (tool separation is not isolation) | stated in the docs |
| T19 | The sandbox image is removed by hand between inspection and run | Runs by ID; a missing image fails the run closed | a failed run, never a wrong image |
| T20 | Covert channel through stdout size or timing | none beyond the caps | accepted; stdout is untrusted model input |

The runner runs as the operator user with Docker access (equivalent to root on Linux). It is no more
privileged than `agentbreeder deploy` itself, and it never executes anything the manifest did not
declare.

## 12. Sub-slices: files, tests, acceptance

### K6a: contract

- Files: `manifest.py`, `gate.py` (`revision_digest` exclusion), `package.py`, `build.py`
  (`template`, `bind`), `deploy_ref.py`, `release.py`, the `center/` deploy path (lane B agrees),
  `operator_config.py` (`skill_scripts`), `deployment.py` (`_deploy_requirements`,
  `_kubernetes_requirements`, audit detail), `docs/manifest.md`, `docs/manifest.schema.json`,
  `docs/agent-yaml.md`, operator config docs, a status note in the skills spec §9.5,
  `changelog.d/1059.added.md`.
- Tests: manifest validation; both digest exclusions (a spec digest and a revision digest stored
  from `main` are unchanged); template/bind round trip; package v1 unchanged byte for byte, v2
  refused by a v1-only reader, and the v1/v2 field rules; `skill_sandbox.env` validation (≤ 32
  entries, `NAME=value` shape) and a deploy from a version 2 package on a host with no build record,
  which checks the image against the manifest's `env`; `_deploy_requirements` with no
  environment refuses a sandbox manifest; `SKILL_SCRIPTS_NOT_ALLOWED`; `SKILL_SCRIPTS_LOCAL_ONLY` through
  `deploy --cluster`, `promote`, and the center's deploy path; `MODEL_ACCESS_UNSUPPORTED` under
  enforcement.
- Acceptance: all of the above green; no runtime yet offers `skill_scripts`, so users see no change.

### K6b: build

- Files: `build.py` (sandbox context, generated Dockerfile, second build, modes, build record),
  `registry.py` publish check of the `parameters` subset, `docs/commands/build.md`.
- Tests: the generated Dockerfile byte for byte; a base digest that is not a digest refused; the
  context has only scripts and references with `0555`/`0444`; `SKILL_SCRIPTS_TOO_MANY`;
  `SKILL_NAME_CLASH` against registry, MCP, built-in and sub-agent tools; a build without script
  skills calls the builder once and writes no sandbox fields.
- Acceptance: a stub-builder build of a two-script agent writes the expected manifest.

### K6c: runner

- **Before K6c, in its own PR:** `hostservice.py`, the refactor of the shared mechanics out of
  `egress/boundary.py`, with the egress tests unchanged and no other change, so the verified egress
  path is reviewed on its own.
- Files: `skills_runner/host.py`,
  `skills_runner/container.py`, `skills_runner/schema.py`, `skills_runner/__main__.py`.
- Tests (fake Docker, no engine):
  - signing: missing, wrong, stale (outside ±60 s, `SKILL_CLOCK_SKEW`), earlier than the runner's
    start, and replayed requests refused before the body is parsed; the token never appears in any request bytes; every reply signed, and a reply signed
    with the request key refused by the client;
  - transport limits: missing or oversized `Content-Length`, chunked bodies, slow headers (2 s), the
    connection cap;
  - parser and validator: duplicate keys, `NaN`/`Infinity`, a 5,000-digit integer, nesting depth 5,
    `true` for an `integer`, extra keys, each schema rule, all as `SKILL_ARGS_INVALID` and never a 500;
  - undeclared tool; argv census (no `-e`/`--env`/`--env-file`, `-v`, `--mount`, `--privileged`,
    shell; `--ulimit core=0` and `--network none` present); stdin is the canonical args;
  - I/O: a script that floods stderr while writing stdout does not stall; a script that ignores
    stdin; output cap; stdout and stderr redacted; `docker run` exits 125–127 map to
    `SKILL_SANDBOX_UNAVAILABLE`;
  - stopping: kill by name at the runner deadline; past-deadline containers reaped on the next
    request; the runner lock refuses a second runner;
  - slots: per-deployment, host (two runners contending, and a live container left by a dead
    runner counted), the fairness marker lock, and the per-minute limit on runs and refusals
    checked before any audit write;
  - the timeout budget: a run at each limit ends within 18 s at the runner and before the client's
    22 s timeout;
  - no `--security-opt seccomp=` in the argv (Docker's default); a detached reaper is started
    before each run and stopped through its handle after it (a reaper that already ran is
    tolerated; a missing `docker` on `PATH` is logged); the nonce cache refuses when full and
    keeps entries for 130 s;
  - audit: start row before the run and fail closed with the slot released; end row after; an
    `unknown` end row for a start left open; unauthenticated requests produce one summary row per
    minute; refusals beyond 10 a minute coalesce; `args_hmac` uses `audit_key`;
  - image inspection: each rule of §3 refuses, and the real base image's `Env` set (with
    `PYTHON_VERSION`) recorded at build passes;
  - Docker context from the pin and Docker variables dropped; the runner's environment is the
    allow-list only;
  - idle exit; leftover cleanup requires both label and prefix; egress tests unchanged after the
    refactor.
- Acceptance: the suite green on 3.11–3.13; coverage ≥ 95% on the new package.

### K6d: lifecycle

- Files: `deployment.py` (`_run`, the `generated` secrets, failed cleanup, status, reconcile,
  disable, enable, remove), `runtime.py` (socket directory mount on Linux), `docs/commands/{deploy,status,reconcile,disable,remove}.md`, `docs/local-deployment.md`.
- Tests: each row of §9 with a fake runtime and a fake spawn; stale `skill-run` containers listed by `status` and removed by `reconcile`; including the first-generation order
  (token, pin, runner healthy, probe, then create); a restart of an existing container with a dead
  runner; a missing token file forcing recreation; and "container exists, token file present, pin
  deleted" forcing recreation with a new token; token rotation on
  recreate (old keys rejected); kill switch order (runner stopped before the agent); failed attempt
  stops the runner; `reconcile` stops runners of non-ready deployments; the sandbox image is never
  removed.
- Acceptance: green; Northwind deploy path untouched (golden manifest and an unchanged create
  argument list for agents without `skill_sandbox`).

### K6e: runtime

- The next `runtime/adk` version after RT, `capabilities` adds `skill_scripts`; one declared tool
  per script with the manifest's description and parameters; the runner client (Unix socket or TCP,
  nonce, request signing, reply-signature check, clock offset from `/healthz`, a signed health check before each run, 22 s timeout); 5 runs per turn in `AgentPlugin`; `turn["id"]` sent; spans and
  events per skills spec §11; `manifest.yaml` `environment` and `secrets` list the new variables.
- Tests: stub runner (good, bad or missing reply signature, down, busy, timeout, clock skew with one
  re-measure and retry); the cap; spans carry exit code and
  duration; the parameters a model sees equal the manifest's.

### K6f: acceptance (b), Docker

On Docker Desktop, recorded in `docs/superpowers/verification/1059-skills-b.md`:

1. An agent computes an answer with a script; the trace shows the run and its exit code; the audit
   chain has the run row.
2. Escape checks, each a script in a test skill: open a TCP socket; resolve a name; read
   `/run/secrets`, `/proc/1/environ`, `/proc/net/*`, `/sys` and any file outside `/skills` that
   would hold agent data; write to `/proc/sys`; `ptrace` PID 1; `mount`; `unshare`; crash with
   SIGSEGV (no core written); `ptrace` PID 1 or write `/proc/1/mem`, then sleep (removed anyway, §7.3); write a file and read it in a later run; fork beyond 32 processes;
   allocate past 256 MiB (reported as `SKILL_SCRIPT_KILLED`, not a timeout); print past 64 KiB; sleep 60 s; ignore SIGTERM and signal PID 1. Each fails
   as §3, §7 and §8 say.
3a. Kill the runner (SIGKILL) during a 60 s script: the reaper removes the container within 20 s,
   and the next runner start writes the `unknown` end row.
3. A request with a wrong token, an undeclared tool and invalid args, each refused and audited.
4. Kill switch during a running script: the runner stops, the container is gone within 10 s.
5. `deploy --cluster` refuses with `SKILL_SCRIPTS_LOCAL_ONLY`; an environment without
   `skill_scripts: true` refuses with `SKILL_SCRIPTS_NOT_ALLOWED`.
6. Northwind 61/61 on the K6e runtime version.

Before any claim beyond Docker Desktop, items 1–4 again on a Linux Docker Engine (Unix socket path).
Until then the docs say "verified on Docker Desktop only".

## 13. Open decisions (owner)

| # | Decision | Recommendation |
|---|---|---|
| A1 | Runner transport on Linux | **Unix socket directory mounted into the agent only**, checked by a mount probe (§6.2); TCP via the host gateway only on Docker Desktop, where sockets cannot be mounted. |
| A2 | Who may deploy a script skill | **Per-environment opt-in, default deny** (`skill_scripts: true`, §5). |
| A3 | Where the runner's allow-list comes from | **Inline in the manifest** (tool, skill, version, module, script hash, schema), at most 16 scripts per deployment (§4.1). |
| A4 | Package format | **`package_version` 2 only for packages with a sandbox image**; others stay 1 (§4.3). |
| A5 | Audit granularity | **Two chained rows per run: start before the run (fail closed), end after (best effort, `unknown` on a crash)**; unauthenticated noise as one summary row per minute (§10). |
| A6 | Concurrency cap | **4 sandbox containers per state directory** (best-effort engine count beyond it; an operator with several state directories on one engine can exceed 4) across deployments, with fairness and a per-deployment rate (§8). |
| A7 | Token on the wire | **Never sent**; signed requests and replies with keys derived from it (§6.3). |

## 14. Cross-repo

- **JSON Schema:** `docs/manifest.schema.json` gains `skill_sandbox` (K6a).
- **Cloud:** a `CLAUDE.md` note (manifest field and package v2); it has no code that reads the
  manifest or packages.
- **Website:** nothing until release R; `what-ships` may list script skills only after K6f, and only
  for the platforms it ran on.

## 15. Review log

| Round | Reviewer | Result |
|---|---|---|
| r1 | `/security` (Sonnet, read-only) | CHANGES REQUESTED. Folded in: a bearer token on the wire lets a port squatter forge signed replies (token never sent; derived request and reply keys, nonce, time window, replay cache, §6.3, A7); socket file mount pins a dead inode and modes for uid 65532 (directory mount, `0755`/`0666` under `0700` parents, §6.1–§6.2); `unix` mode on Colima/OrbStack (mount probe, §6.2); runs outlive a dead runner (PID 1 `timeout`, deadline label reaping, live-container count for host slots, §3, §7.3); audit row before the run cannot hold the results (start and end rows, summary rows for unauthenticated noise, keyed `args_hmac`, §10); parser and `bool`-as-`int` bypasses (§6.4); `--ulimit core=0` and other flags (§7.1); runner on another Docker engine and inheriting the operator's environment (§6.1); arbitrary image from a manifest (inspection, §3); host-slot fairness and connection limits (§8); I/O deadlock and `docker run` exit codes (§6.5, §7.2); runner lock (§6.1); slim base recorded (§3); threat table rewritten with T14–T20 and no "none known" before a run (§11). |
| r4 | `/architect` + `/security` (Sonnet, read-only) | **APPROVED** (no blocker or major). Minors folded in: one 20 s figure; the reaper stopped through its `Popen` handle, standard library only, no secrets in argv, `PATH` and failure logging; `env` entry shape and length limits; stale `skill-run` containers in `status`/`reconcile`; A6 wording for several state directories; T14 residual for hand-written manifests. Accepted as stated: an OOM kill after 9.5 s reads as a timeout (§6.5); layer 4 is passive if both runner and reaper die (§7.3). |
| r3 | `/security` (Sonnet, read-only) | CHANGES REQUESTED (round-2 fixes verified). Folded in: seccomp does not cover `/proc/1/mem`, so PID 1 `timeout` is a convenience only and the runner kill plus the detached reaper are the authoritative deadline; the custom seccomp profile is dropped (drift risk) (§7.3, T3, T12, K6f); the `Env` home (with `/architect`); nonce cache kept 130 s (§6.3); stale "5 s", T14, "the token is the control", per-state-directory cap, ~121 audit rows per minute, reaper process group (§6.2, §8, §10, §11); transport probe in both modes (§6.2); fairness probe with `LOCK_NB` and retry (§7.3); timeout classification by elapsed time (§6.5). |
| r3 | `/architect` (Sonnet, read-only) | CHANGES REQUESTED (round-2 fixes verified). Folded in: the approved `Env` set must travel in the manifest, package and pin, not only the build record (§3, §4.1, §4.3, §6.1); seccomp profile packaging, a vendored-profile unit test and an engine check in K6f (§7.3); the reaper as an argv-only module with a 12 s delay and a 20 s claim (§7.3, T12, K6f); the slot cap is per state directory with a best-effort engine count (§7.3, T11, A6); stale "5 s", T14 and K6c plan row. |
| r2 | `/security` (Sonnet, read-only) | CHANGES REQUESTED (round-1 fixes verified). Folded in: `PYTHON_VERSION` in the base image (§3, shared with `/architect`); a hostile script can `ptrace` PID 1 (seccomp profile without `ptrace` and related calls, detached reaper per run, §7.3, T12); the squatter still saw arguments (signed health check before every run, no keep-alive, §6.3); authenticated refusal flood (one per-minute limit for runs and refusals before any audit write, coalesced rows, §8, §10); timeouts did not nest (one budget table, runtime timeout 22 s, §8); OOM 137 (§6.5); clock offset (§6.3); replay after restart and a cache that refuses when full (§6.3); connection starvation stated (T7); fairness mechanism and per-engine counting (§7.3); separate `audit_key` (§6.3, §10); missing pin forces recreation and the `_FILE` env name and `SO_REUSEPORT` (§6.2, §6.3, §9). |
| r2 | `/architect` (Sonnet, read-only) | CHANGES REQUESTED (round-1 fixes verified). Folded in: the `PYTHON*` env rule would refuse the real base image (compare to the recorded set, refuse only interpreter-altering names, §3); first-generation order and a missing pin force recreation (§9); clock skew (offset from `/healthz`, ±60 s, `SKILL_CLOCK_SKEW`, §6.3); replay across a runner restart (§6.3); "bad MAC" wording (§8, §12); OOM exit 137 vs timeout (`SKILL_SCRIPT_KILLED`, §6.5). |
| r1 | `/architect` (Sonnet, read-only) | CHANGES REQUESTED. Folded in: the gate's policy request does not carry the manifest (§5 corrected; skills spec §9.5 status note); `gate.revision_digest` dumps the whole manifest (exclusion and test, §4.1); `DeployTemplate` and `Package` serialisation and version set (§4.2, §4.3); every `bind`/package caller (§4.3, K6a); lifecycle on every `_run`, the generated-secret map, failed cleanup, `enable`, reconcile's "never starts anything" exception, the pinned Docker Desktop port (§9); the sandbox image is kept like the agent image (§9); runner audit writes and the store lock (§10); `_deploy_requirements` without an environment (§5); tool-name regex aligned to 24 (§4.1); `hostservice.py` refactor as its own PR (§12). Its squatter finding matched `/security`'s and is covered by §6.3. |
