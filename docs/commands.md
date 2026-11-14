# Command reference

To use the published preview, install `"agentbreeder[eval]==3.0.0a2"` from PyPI (pip or uvx) or run `npx agentbreeder@3.0.0-alpha.2`; a bare `pip install agentbreeder` installs 2.x. `npx` and `uvx` leave no `agentbreeder` command on `PATH`; `uv tool install "agentbreeder[eval]==3.0.0a2"` adds one. See the root README.

For development, use a fresh virtual environment in a checkout and install `python -m pip install -e ".[dev]"`. Run `agentbreeder --help`, `agentbreeder validate src/agentbreeder/examples/http-agent/agent.yaml`, `agentbreeder doctor`, `python -m pytest`, `ruff check src tests`, and `python -m build`.

One page per command is in [`commands/`](commands/); the index is below.

## Common to several commands

`--stack DIR` on `build`, `deploy`, `eval`, `release`, `status`, `remove` and `reconcile` uses a stack's demo operator config written by `services up` (see [`services`](commands/services.md)). `--stack DIR` no longer creates a stack directory for a stack that was never brought up (on `main`, not in a published preview).

Commands that end a step print a `Next:` line in the form you launched with (`agentbreeder`, `npx agentbreeder@<ver>`, `uvx --from 'agentbreeder[eval]==<ver>' agentbreeder` or `python -m agentbreeder`), with the `--stack` and `--environment` they used; `build` points at `release`, never at a production `deploy` (on `main`, not in a published preview).

When the terminal's Ctrl-C or a SIGTERM/SIGHUP for the whole process group reaches the docker or kubectl child of `logs --follow`, `services logs --follow`, `watch` or `services usage --follow` first, the child's exit (killed by the signal, or 130, 143 or 129 when it caught it) ends the command like the signal itself: the end record's reason is `interrupt` or `signal`, exit 0. On Docker 27.5, `docker compose logs -f` exits 130 after SIGINT and SIGTERM, while `docker logs -f` and `docker events` exit 1, which still reads as a failure in that race; kubectl was not checked. The CLI's own handler usually records the stop first (on `main`, not in a published preview).

Run `agentbreeder COMMAND --help` for flags; see the root README for the demo.

## Commands

<!-- commands-index:start -->
- [`agent`](commands/agent.md): Change the project's agent.yaml.
- [`approve`](commands/approve.md): Approve a pending approval as a second person.
- [`approve-call`](commands/approve-call.md): Approve one held MCP tool call as a second person.
- [`audit`](commands/audit.md): Inspect and verify the local audit chain.
- [`build`](commands/build.md): Build an agent file into an image and a deploy manifest (never deploys).
- [`cluster`](commands/cluster.md): A local kind cluster for one stack (preview).
- [`cost`](commands/cost.md): Show LLM cost per deployment or per model from the gateway's spend logs (read-only).
- [`deploy`](commands/deploy.md): Gate and deploy a manifest.
- [`disable`](commands/disable.md): Persist a principal blocker and stop its workloads.
- [`doctor`](commands/doctor.md): Check local Docker prerequisites without changes.
- [`enable`](commands/enable.md): Clear a principal blocker (starts nothing).
- [`eval`](commands/eval.md): Run an agent's eval set against a running endpoint.
- [`events`](commands/events.md): List stored events (failed jobs, eval gate failures, containers that exited).
- [`example`](commands/example.md): Copy a shipped example project (`example list` shows them).
- [`forward`](commands/forward.md): Open a loopback port to a Kubernetes deployment.
- [`invoke`](commands/invoke.md): Send one message to a local deployment and print the reply.
- [`list`](commands/list.md): List deployments that are not removed (read-only, no IdP calls).
- [`login`](commands/login.md): Sign in with the operator's OIDC device flow.
- [`logs`](commands/logs.md): Show a local deployment's logs (redacted), or the saved tail of a failed candidate.
- [`migrate`](commands/migrate.md): Upgrade the local state to this version's schema (no IdP calls).
- [`new`](commands/new.md): Write a starter project (`new agent NAME`).
- [`next`](commands/next.md): Show where this project is on the guided path and the command to run next.
- [`promote`](commands/promote.md): Deploy the released image to Kubernetes, without rebuilding it.
- [`quickstart`](commands/quickstart.md): Build, gate, deploy, invoke and remove the sample (needs Docker).
- [`reconcile`](commands/reconcile.md): Re-verify principals and retry blocked cleanup.
- [`registry`](commands/registry.md): Author and inspect registry versions.
- [`reject`](commands/reject.md): Reject a pending approval as a second person.
- [`reject-call`](commands/reject-call.md): Reject one held MCP tool call as a second person.
- [`release`](commands/release.md): Build, deploy a candidate, run the eval gate, promote the exact image if it passes.
- [`remove`](commands/remove.md): Remove a deployment.
- [`secret`](commands/secret.md): Manage local secret files (secret:// refs).
- [`services`](commands/services.md): Local backing services from stack.yaml (local-only preview).
- [`status`](commands/status.md): Show a deployment and re-verify its principal.
- [`traces`](commands/traces.md): Show recent agent traces per deployment from the stack's Phoenix (read-only).
- [`up`](commands/up.md): Start the local console on 127.0.0.1 (single user, preview).
- [`validate`](commands/validate.md): Validate a deploy manifest or an agent file (kind: agent).
- [`watch`](commands/watch.md): Report one stack's container events (died, unhealthy, restarted) until stopped.
<!-- commands-index:end -->
