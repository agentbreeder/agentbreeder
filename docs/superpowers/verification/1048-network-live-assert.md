# #1048 verification: no stack network is left after `services down` (live Docker)

Status: **verified from `main`** (3f13edb4, after #1091 merged, 2026-11-18, macOS 13.7.8 x86_64,
Docker Desktop 4.38.0, engine 27.5.1). On real Docker, from a wheel of `main`: after
up → down, and after a failed up → down, no `agentbreeder-*` network was left. The two opt-in
tests that carry the #1048 assertions, which #1091 could not run, passed against the same wheel.
Nothing was left running. The single `-agents` leftover from the issue was not reproduced here
either.

This is on `main`, not in a published preview.

## What changed (1 PR)

| PR | Merged | Content |
|---|---|---|
| #1091 | 45061d26 | `services down` removes the stack's networks that `docker compose down` leaves (by label `io.agentbreeder.stack` and by name), also after a failed `up`; an in-use network is kept and reported; manual cleanup documented in `docs/services.md` |

#1091's own description says its opt-in real-Docker assertions were written but not run. This
record is that run.

#1091's review and full-suite results are in that PR; they were not re-run for this record. This
PR also updates the sentence in `docs/services.md` that said the real-Docker assertions had not
been run.

## Wheel from main (clean sandbox)

`main` at 3f13edb4, no tracked changes. Wheel built with `python -m build --wheel`
(`agentbreeder-3.0.0a3-py3-none-any.whl`, sha256
`c5649e9952bd5ab0b74aa6f3a462f676e00e46dddc8e0cdb8d566ff5dab40536`), installed with `[eval]` into a
fresh Python 3.12 venv. Every command ran under `env -i` with a fresh `HOME` and `TMPDIR` inside
the sandbox (`$SB`, under `/tmp`), `PIP_NO_CACHE_DIR=1` and `USER=demo`. A Docker context was
created inside the sandbox `HOME`, since AgentBreeder refuses `DOCKER_HOST`. The project was
`new agent n48 --model gemini` (postgres and the LiteLLM gateway). `services up` needs the
provider key in its env file but makes no provider call; no model call was made.

Networks were listed with `docker network ls --filter name=agentbreeder-` after each step.

| Step | Command (UTC start) | Exit | Time | `agentbreeder-*` networks afterwards |
|---|---|---|---|---|
| 1 | `services up $SB/n48 --environment dev --env-file <key> --yes` (23:25:16) | 0 | 416 s (three image pulls) | 3: `agentbreeder-n48-dde9f9fd`, `…-agents`, `…-edge`, all carrying the stack label |
| 2 | `services down $SB/n48 --yes --json` | 0 | 43 s | **0**. `networks_removed: []`, `networks_left: []` (Compose removed all three itself); the volume is kept, as documented |
| 3 | a listener on the stack's recorded gateway host port, then the same `services up` (23:45:45) | 2 | 64 s | 3; postgres running, the gateway container only created. `[PORT_BUSY] port 57896 is already in use on this machine (starting service litellm); …` |
| 4 | `services down $SB/n48 --purge --yes --json` | 0 | 11 s | **0**. `networks_left: []`; the volume and the stack's state removed; no container left |

The opt-in tests, run from the `main` checkout's `tests/` against the installed wheel
(`AGENTBREEDER_DOCKER_TEST=1`, `-o pythonpath=""` so the package under test is the wheel):

| Test (23:47:46) | Result | What it asserts for #1048 |
|---|---|---|
| `tests/test_services_docker.py::test_echo_stack_lifecycle` | passed | after up → down, no network and no container with the stack's label |
| `tests/test_mcp_relay_docker.py` (1 test) | passed, no teardown error | the path that caused the leftovers: an `mcp:` entry is dropped, then `down --purge`; no network with the stack's label is left (asserted in the `project` fixture's teardown) |

`2 passed in 512.27s`. After the run `docker network ls` showed only `bridge`, `host` and `none`.

## Acceptance (from the issue)

| Check | Result |
|---|---|
| Find which code path creates `<project>-mcp-mock` and `<project>-agents` networks, and make `services down` (and the opt-in tests' teardown) remove them, including after a failed `up` | **PASS** for `-mcp-<name>` (cause and fix in #1091; live: the relay test above, and steps 3–4 for a failed up). **Partial** for `-agents`: it is removed in every run here, but the one leftover in the issue was never reproduced; #1091 keeps and reports an in-use network |
| A test asserting no `agentbreeder-*` network is left after up → down, and after a failed up | **PASS.** Unit tests in #1091 (fake Docker). Live: up → down in `test_echo_stack_lifecycle` and steps 1–2; failed up by hand in steps 3–4. There is no opt-in real-Docker *test* for the failed-up case; it was run by hand |
| Document how to clean up the networks that already exist (no automatic pruning) | **PASS** (`docs/services.md`, #1091). Not exercised: this machine has no leftover network now (see Notes) |

Flows that need a model key: none in this record.

## Browser click-through

Not run: nothing in #1048 has a browser surface.

## Leak scan and teardown

- The 7 saved outputs (the command outputs and the pytest output) were scanned for the two
  `~/.env` values copied into the sandbox key file (plain, base64 and URL-encoded) and for home
  paths: 0 hits.
- Docker was compared with the snapshot taken before step 1 (23:25 UTC: one keepalive container of
  this session, no volume, networks `bridge`, `host`, `none`). After the record: the same
  containers, volumes and networks, and the default `bridge` ID unchanged (`0ce15d58…`). Three
  base images were pulled during step 1 (python, pgvector, the LiteLLM image) and kept for the
  session's later tasks.

## Notes

- **Pre-existing leftovers:** none. The 17 networks the issue lists are gone, because Docker
  Desktop's disk was recreated on 2026-11-15 (#1077), not because anything cleaned them up.
- A keepalive container ran throughout, so Docker Desktop's Resource Saver did not stop the VM
  (#1077).
- Step 2 shows that for a stack whose compose file did not change between up and down, Compose
  removes the networks itself and #1091's sweep has nothing to do. The sweep is exercised by the
  relay test, where the compose file was re-rendered without the relay's network.
