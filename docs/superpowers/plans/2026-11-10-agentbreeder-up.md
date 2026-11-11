# Plan: `agentbreeder up` (#783)

Spec: `docs/superpowers/specs/2026-11-10-agentbreeder-up-command-center-design.md`.

Order note: slice 1 was written before this plan, while the spec was in review. The plan records
what was built and how each slice was verified.

| Slice | Issue | Files | Verification |
|---|---|---|---|
| 1 Kubernetes runtime | #771, #773 (local scope) | `kubernetes.py`, `deployment.py`, `store.py`, `operator_config.py`, `runtime.py`, `cli.py` | `tests/test_kubernetes.py`; live on kind |
| 2 Cluster and grant | #796 | `cluster.py`, `services/engine.py`, `services/operator.py`, `services/plan.py` | `tests/test_cluster.py`, `tests/test_services_engine.py`; live |
| 3 Promote | #797 | `promote.py`, `release.py` | `tests/test_promote.py`; live, 61/61 on the cluster |
| 4 Server and jobs | #798 | `center/server.py`, `center/jobs.py`, `pyproject.toml` | `tests/test_center.py`; live over HTTP |
| 5 UI | #799 | `center/static/` | Headless Chrome through the sign-in file; XSS and CSP tests |
| 6 Live run and docs | #800 | `docs/command-center.md`, README, ARCHITECTURE, ROADMAP, website | Spec §9 run on 2026-11-10 |

## Entry gate

The §15 spike ran first: a pod reached a service on an `internal: true` Docker network by name
through the kind node. No CoreDNS fallback was needed.

## Sequence

1. Slices 1-3, engine first, so the UI has real commands to run.
2. Slices 4-5.
3. Live Northwind run from the command center API, acceptance checks, teardown.
4. Different-model code review, findings fixed and re-reviewed until converged.
5. Docs and website only for what ran. Quality gate. PR, owner approval, auto-merge.

## Cross-repo

Website: `what-ships` and a docs page. Cloud: no code; `CLAUDE.md` records that
`Environment.kubernetes` exists and that the center's API is private.

## Not in this epic

A published release with `up` (needs the release workflow and the owner), Linux and clicked-through
browser verification, Helm (#772), real clusters and imported agents (#774).
