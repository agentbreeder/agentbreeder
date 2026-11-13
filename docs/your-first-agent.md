# Your first agent

From an empty directory to a released agent of your own that answers a message. This is on
`main`, not in a published preview: `3.0.0a2` has no `new agent`, `next` or `invoke`. Steps 1
to 7 ran once end to end with this starter from a checkout on macOS with Docker Desktop and a
Gemini key (2026-11-12: `release` 2/2, then an `invoke` reply). Step 8 has not been run with the
starter (Northwind passed it on local `kind`, see the console page, `command-center.md`).
Nothing here has been run from a published package or on Linux.

The steps are the ones `agentbreeder next` checks, in the same order and with the same commands
(`src/agentbreeder/guide.py` and `guide_checks.py` define them;
`tests/test_your_first_agent_doc.py` fails when this page and the guide drift apart). `next DIR`
shows the checklist (✓ done, → next, ✗ blocked, · not yet) and the one command to run next. It
only reads, and its text output exits 1 while steps remain (`next.md` describes `next --json`).
`new agent`, `services up` and `build` also end with a `Next:` line; after `release`, run
`next`.

Commands are shown as `agentbreeder …`, run in the project directory from step 3 on. With
`npx agentbreeder@<version>`, `uvx --from 'agentbreeder[eval]==<version>' agentbreeder` or
`python -m agentbreeder`, printed commands use that form instead, and `next`, `new agent` and
`services up` print absolute paths.

## 1. Prerequisites

Docker with Compose v2, running; with `npx` or `uvx`, also `uv`. Step 8 also needs `kind` and
`kubectl`.

```sh
agentbreeder doctor
```

What you see: one line per check. When a required check fails, `next` shows this step as
blocked with that check's message and points back at `doctor`.

## 2. Project

```sh
agentbreeder new agent hello --model gemini
cd hello
```

What you see: the files written (`agent.yaml`, `stack.yaml`, `stack/litellm/config.yaml`,
`src/prompt/`, `src/evals/`), "Published prompt/hello-prompt@0.1.0 and eval/hello-evals@0.1.0
into registry/ and wrote agent.lock", and `Next: … next …`. `--model gemini` is the only model
offered, because it is the only provider run end to end. `agentbreeder quickstart` ends with the
same `new agent` command. For a directory you already made and left empty, `next DIR` suggests
writing the starter into it (with the directory's absolute path, and its name as the agent's
name when that is a valid one); run inside that empty directory, that is:

```sh
agentbreeder new agent hello --model gemini --dir .
```

Instead of the starter, you can copy the Northwind example (it also needs `TAVILY_API_KEY` and
runs 61 evals); run this in place of `new agent`, not inside `hello`:

```sh
agentbreeder example northwind
```

When `agent.yaml` or `stack.yaml` does not validate, `next` shows this step as blocked and
points at:

```sh
agentbreeder validate agent.yaml
```

When a registry ref does not resolve, it names the ref and says to publish it or fix the ref.

## 3. Model keys

Get a Gemini key at <https://aistudio.google.com/apikey>. Put it in a file only you can read, as
one line `GEMINI_API_KEY=<your key>`, written with an editor so the key stays out of your shell
history:

```sh
(umask 077; touch ~/hello-keys.env) && chmod 600 ~/hello-keys.env
"${EDITOR:-vi}" ~/hello-keys.env
```

Then hand it to the stack's model gateway as the services start (this is also step 4):

```sh
agentbreeder services up . --environment dev --env-file ~/hello-keys.env --yes
```

`--yes` accepts the stack's grant, which `services up` prints (step 4 lists what is in it). To
read it before accepting, leave out `--yes`: on a terminal it asks, otherwise it refuses.

What you see from `next` before this: "→ Model keys: Set GEMINI_API_KEY for the model gateway",
with the link above. The step is done once the key is exported in your environment or stored by
`services up` (under `~/.agentbreeder/stacks/`, mode 0600). `next` prints key names, never
values. The `--env-file` must belong to you and not be writable by others; its readability is
not checked, so keep it 0600.

## 4. Services

With the key stored by step 3's command, or exported, this is the same command without
`--env-file`:

```sh
agentbreeder services up . --environment dev --yes
```

What you see on the first run: the stack's grant (Postgres and LiteLLM, the wiring, the
gateway's `chat` and `judge` models and their key, the demo identities with their `review_by`
date), then "Starting postgres", "Starting litellm", the generated operator config's path, and
`Next: … release …`. A later run with an unchanged grant does not print it again. When a service
is stopped or unhealthy, `next` points at the same `services up` command;
`agentbreeder services status .` and `agentbreeder services logs . NAME` show why (logs are
redacted for secrets only, best effort, and can contain messages and model output). When
`docker compose ps` itself fails, the step is blocked and `next` points at:

```sh
agentbreeder doctor
```

## 5. Build

`release` builds with `--locked` itself, which is why `services up` points straight at it. To
build without releasing:

```sh
agentbreeder build agent.yaml --stack . --environment dev
```

What you see: "Built hello: sha256:…", the production and candidate manifests, "Lock: …
agent.lock (unchanged)" and `Next: … release …`. When `agent.yaml` refers to a source by
`path:` (`release` needs a published version), `next` points at publishing that directory and
changing the ref to the version it printed:

```sh
agentbreeder registry publish src/prompt
```

## 6. Release (eval gate)

```sh
agentbreeder release agent.yaml --stack . --environment dev
```

What you see: "Deploying candidate hello-candidate ...", "Running 2 evals against …",
"Released sha256:…: 2/2 evals passed", the production endpoint (`http://127.0.0.1:<port>`) and
its deployment id, and the eval report's path. Production is deployed only when the gate passes.

When the gate fails, `release` lists each failed case. When it saved the failed candidate's log
tail, `next` points at it (otherwise at `release` again):

```sh
agentbreeder logs --failed hello --stack . --environment dev
```

Redaction there is best effort and covers secrets only; the tail can still contain messages and
model output, so read it before sharing it. To change the agent, edit `src/prompt/prompt.md` or
`src/evals/golden.jsonl`, bump `version:` in that directory's `manifest.yaml`, publish it
(`agentbreeder registry publish src/prompt` or `agentbreeder registry publish src/evals`),
change the ref in `agent.yaml`, run `build` (it updates `agent.lock`), then `release` again.

Two other states block this step. When local state is from an older version, `next` asks for:

```sh
agentbreeder migrate --stack . --environment dev
```

When the kill switch disabled production, `next` prints the command to re-enable it, with the
deployment id and the stack's state directory filled in:

```sh
agentbreeder enable DEPLOYMENT_ID --environment dev --state-dir STATE_DIR
```

`enable` needs a verified human operator that policy allows (`agentbreeder login` against an
issuer in the operator config). The demo config `services up` writes has none, so there it is
refused. This is a check in the CLI, not protection against someone who can edit the local
state. A deploy waiting for a second person's approval is shown as blocked with how to approve
it.

## 7. Try it

```sh
agentbreeder invoke hello "When is support open?" --stack . --environment dev
```

What you see: the session id ("continue with --session …") and the agent's reply. `next` now
ends with `Done. Try it: … invoke hello Hello …`. `invoke` calls only the local loopback
endpoint, follows no redirects and waits at most 180 s. `--json` prints the whole answer; `-`
instead of the message reads it from stdin, which keeps it out of `ps`.

The endpoint has no authentication (#780). It listens on 127.0.0.1 only, but any process or user
on this machine can call it, and `--customer` and `--session` are labels, not verified
identities. Use it on a single-user machine and do not expose or forward the port.

## 8. Kubernetes

Optional, and not yet run with this starter. This step appears in `next` once `cluster up` has
attached the stack to the local `kind` cluster; if the cluster is later deleted, `next` offers
`cluster up` again. Only local `kind` is supported and no production cluster has been tested
(the console page, `command-center.md`).

```sh
agentbreeder cluster up --yes --stack . --environment dev
agentbreeder promote agent.yaml --to kubernetes --stack . --environment dev
```

What you see: `cluster up` prints the changed grant (the cluster added to it), which `--yes`
accepts as it now stands, including any `stack.yaml` change since your last `services up`. To
review it first, run `cluster up` without `--yes`, then
`agentbreeder services plan . --environment dev`, then `services up` without `--yes`. Until a
grant with the cluster is accepted, the operator config is unchanged and `promote` would fail
with `KUBERNETES_UNCONFIGURED`, so `next` says the cluster is not in the accepted grant
(`GRANT_PENDING` in `--json`) and points at this command, which, like `cluster up --yes`, accepts
the grant as it now stands:

```sh
agentbreeder services up . --environment dev --yes
```

`promote` deploys the released image to the cluster after running the
evals there (`--skip-cluster-evals` skips them). The step is done when the cluster runs the same
image as production. `agentbreeder forward DEPLOYMENT_ID --stack . --environment dev` gives it
a local port; the same caveat as step 7 applies.

## Clean up

If you did step 8, remove the cluster deployment and delete the cluster first, while the
stack's state still exists (deployment ids come from `agentbreeder list --stack . --environment
dev`, below):

```sh
agentbreeder remove DEPLOYMENT_ID --cluster --stack . --environment dev
agentbreeder cluster down --stack .
```

Then:

```sh
agentbreeder list --stack . --environment dev
agentbreeder remove DEPLOYMENT_ID --local --stack . --environment dev
agentbreeder services down . --purge --yes
```

`list` prints each deployment's id; remove each one first, because `services down` refuses
while any is active. `services down --purge` deletes everything `services up` made: volumes,
built service images, the operator config, the history and the stack's secrets, including the
stored copy of your key. Delete `~/hello-keys.env`, and revoke the key in AI Studio if it was
only for this walkthrough.
