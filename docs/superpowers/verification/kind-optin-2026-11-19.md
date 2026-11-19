# kind opt-in runs: the agent artifact (#763 slice 4a) and kit egress (#1083 F1b), 2026-11-19

Status: **verified from `main`** (dabd828c, 2026-11-19 UTC, macOS 13.7.8 x86_64, Docker Desktop
engine 27.5.1, `kind` v0.33.0, `kubectl` client v1.31.4). Two opt-in test files that were written
and merged without a cluster run were run once on a local `kind` cluster made by `agentbreeder
cluster up`:
- `tests/test_package_artifact_kind.py` (#763 slice 4a, merged in #1146): **2 passed**.
- `tests/test_kit_egress_docker.py -k kind` (#1083 F1b, merged in #1147): **1 passed**.

No product bug was found, so no issue was filed. Neither pass closes an issue by itself: they
are the opt-in real-engine evidence that #763 slice 4a (merged in #1146) and #1083 F1b (merged in
#1147) left as "written, not run". The cluster, its registry, the stack and the
`kind` Docker network were removed at the end.

This is on `main`, not in a published preview. It is a record only: no code changed.

## Setup

- **CLI:** a wheel of `main` (dabd828c, `agentbreeder-3.0.0a3-py3-none-any.whl`, sha256
  `b253c1bb7a3c2eb65f1c1b7e87e2b0225e2419e8f7e2034d91ed3fead4252e8f`) in a fresh Python 3.12.8
  venv. It is the same wheel and sandbox as the #1106 live record (#1164, a separate PR).
- **Tests:** a checkout of the same commit with its own Python 3.12.8 venv (`uv sync
  --all-extras`).
- **Sandbox:** every command, the two pytest runs included, ran under `env -i` with a fresh
  `HOME` and `TMPDIR` inside the sandbox (`$SB`, under `/tmp`) and `USER=demo`. A Docker context
  was created inside the sandbox `HOME`. `kind` was put on the sandbox `PATH` through a symlink,
  because `cluster up` refused without it (`CLUSTER_TOOL_MISSING`). So the cluster's kubeconfig
  was written under the sandbox `HOME`, not `~/.kube`.
- **Stack:** `agentbreeder new agent kitstack --model gemini` and `services up --env-file`. The
  starter has Postgres and the built-in LiteLLM gateway, with the Gemini key in a 0600 file. No model
  was called.
- **Cluster:** no `kind` cluster existed before the run (`kind get clusters`).

**What was saved.** Saved to files: the two pytest logs, and the outputs of `new agent`,
`services up`, `cluster up`, `cluster down` and `services down`. Everything else was read in the
session and not saved. That covers the version strings in the status line, the venv and sandbox
details above, exit codes, wall-clock start times, `kind get clusters`, the `kubectl` check, the
operator config's CIDR values (the config was deleted with the stack) and the Docker cleanup
below.

| Check (in the order run, UTC) | Result | Time |
|---|---|---|
| `services up . --environment dev --env-file <key file> --yes` (before the cluster) | the starter's Postgres and LiteLLM started (saved output) | — |
| `cluster up --stack . --environment dev --yes` (about 16:47 UTC, unsaved) | Saved output: it started the local registry, created the cluster and recorded it in the grant (`context kind-agentbreeder`, images pushed to `localhost:5001/agentbreeder`). Unsaved: exit 0, and the operator config's `agents_cidrs: [172.20.0.0/16]` and `blocked_cidrs` | about 2 min (unsaved) |
| `AGENTBREEDER_DOCKER_TEST=1 python -m pytest tests/test_package_artifact_kind.py -q -s` | **2 passed**. Printed: `subject kept by the registry: True`; `referrers API: HTTP 404, lists the artifact: False` | 0.9 s |
| `AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_KIT_KIND_OPERATOR=<the stack's operator.yaml> AGENTBREEDER_KIT_KIND_ENVIRONMENT=dev pytest tests/test_kit_egress_docker.py -k kind` | **1 passed, 3 deselected** | 95 s |
| `kubectl -n agentbreeder get deploy,pods,networkpolicy` | `No resources found` (unsaved), consistent with the test's `finally` sweep of both deployments | — |
| `cluster down --stack .` | saved: `removed: [agentbreeder, agentbreeder-registry]` | — |
| `services down . --purge --yes` | saved: the stack's volume and state were removed | — |

## What the tests assert

- **`test_package_artifact_kind.py`** (spec §8.2 and the slice-0 question). It pushes an agent
  package artifact (`application/vnd.agentbreeder.agent.v1`) to kind's `registry:2` at
  `127.0.0.1:5001/m13-artifact-test`. It then checks:
  - a second push gives the same reference;
  - the manifest found by tag has the pushed digest, and its layer extracts to the same package
    tree (equal `copy_and_hash` tree hashes).
  The second test pushes an artifact with a `subject` and asserts it is found by tag. It prints
  `subject kept by the registry: True`, read from the push result (`pushed.subject`); the stored
  manifest was not fetched and compared here. The referrers API (`/v2/<name>/referrers/<digest>`)
  answered **404**, so the pinned `registry:2` (pinned by digest in `cluster.py`) does not serve
  referrers. §8.2 allows either answer, because the artifact is found by tag.
- **`test_real_kit_egress_on_kind`**. It builds the sample agent image, pushes it to the
  cluster's registry and deploys two pods. One has `runtime.profile: kit`; the control has no
  `runtime`. From inside each pod it tries TCP to the stack's gateway (the operator config's
  `agent_url`), to `1.1.1.1:443` and to `1.1.1.1:80`. It asserts:
  - the kit pod reached the gateway and **neither public address**;
  - the control pod reached the gateway **and** `1.1.1.1:443`, so the kit pod's failure comes
    from its NetworkPolicy and not from the network.
  The pass means both held. The test prints nothing else.

## Not run

- Linux, arm64 and any cluster other than local `kind`.
- The other three tests in `test_kit_egress_docker.py` (local Docker kit egress) were deselected
  by `-k kind`, as the brief asked.
- No agent was deployed through `deploy --cluster` or `promote`. The kit test creates its pods
  through the `Kubernetes` deployer class directly.

## Leak scan and teardown

- **Leak scan.** The saved outputs (the two pytest logs and the `cluster up`, `cluster down`,
  `new agent` and `services` outputs) and this record were scanned for the values in the
  sandbox key file (each over 12 characters), plain, base64 and URL-encoded, and for a
  home-directory path: 0 hits.
- **Teardown.** Only the `cluster down` and `services down` outputs were saved; the rest of
  this section was read in the session and not saved.
  - `cluster down` removed the cluster and the registry container (saved). `kind get clusters`
    then printed `No kind clusters found`.
  - `services down --purge` removed the stack's containers, volume and state.
  - One thing was left: kind's Docker network `kind`. It was created at 16:48:06 UTC, after the
    lane's snapshot (16:04 UTC), and had no containers, so it was removed by name. Networks then
    matched the snapshot (`bridge`, `host`, `none`).
  - **Images.** At the end of the lane, every image that was not in the 16:04 UTC snapshot was
    removed by ID, without `-f`. That was 29 images:
    - the base images pulled during the lane (`kindest/node`, `registry`, the LiteLLM, pgvector,
      Phoenix and python images);
    - the images the lane built, all created after the snapshot. These include
      `kit-egress-test`, the Northwind agent and service images of the #1106 live run (#1164),
      and the test images of the #1153 Vertex Docker gates.
  - **After cleanup.** Image IDs, volumes (none) and networks equalled the snapshot. The default
    `bridge` ID `0ce15d58…` was unchanged. The lane's keepalive container was removed last. Build
    cache was not pruned.

## Notes

- Kit egress uses `model_access.gateway: litellm-local` (a constant in the test). The starter
  stack's gateway is named `litellm`. The test reads the gateway address from the operator
  config itself, and it passed with that mismatch. Whether a real `deploy --cluster` would refuse
  the name was not checked.
