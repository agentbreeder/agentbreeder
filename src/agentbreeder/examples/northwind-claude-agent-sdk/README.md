# Northwind support agent on the Claude Agent SDK

**Status: run end to end on macOS 13 x86_64 (2026-11-20), not supported** (#1089, #1083 F7).
Northwind 61/61 through `/v1/messages`, locally and on `kind`, on a copy of the stack pinned to
`service/litellm@1.4.0`. Conformance cannot run it yet (#1199); arm64 was not run. The record is
`docs/superpowers/verification/F7-claude-agent-sdk.md`.

`agent.yaml` is Northwind's file with two lines changed, `name` and `runtime`. The prompt, model
alias (`support-model`, through the gateway's Anthropic Messages route), tools, knowledge, memory
and eval set are Northwind's, from Northwind's registry (`tests/test_northwind_claude_agent_sdk.py`).

| What | Where |
|---|---|
| Agent definition | `agent.yaml`, `agent.lock` (Northwind's entries say `source: ../northwind/registry`) |
| Runtime | `registry/runtime/claude-agent-sdk/1.0.0/`, this project's own registry: **AgentBreeder does not ship it** (D8) |
| Prompt, knowledge, tools, eval set | `../northwind/registry/` |
| Backing services | Northwind's `stack.yaml`, which also names this agent's identity |

**Building this agent downloads the `claude-agent-sdk` wheel from PyPI, with the Claude Code CLI
inside it. Running it means accepting Anthropic's terms for Claude Code.** See
`docs/runtimes/claude-agent-sdk.md` for the two-uid deploy contract (the CLI runs in a second
container, as another uid, with no secret and no network), the controls and the limits.

## Run it (from a checkout)

The `/v1/messages` route needs a `service/litellm@1.4.0` gateway (#1187), and Northwind's shipped
stack stays on `service/litellm@1.0.0` for its other agents. Run this agent on a copy of
Northwind with the gateway pinned to 1.4.0 (the directory must be named `northwind`). Bring down
any other Northwind stack first, and on Linux use `sed -i` (no `''`):

```
WORK=$(mktemp -d)
pip install -e ".[eval]"                       # at the repo root
cp -R src/agentbreeder/examples/northwind src/agentbreeder/examples/northwind-claude-agent-sdk "$WORK"/
sed -i '' 's#service/litellm@1.0.0#service/litellm@1.4.0#' "$WORK/northwind/stack.yaml"
(cd "$WORK/northwind" && agentbreeder services up --update --environment dev --env-file ~/.env)
cd "$WORK/northwind-claude-agent-sdk"
export AGENTBREEDER_REGISTRY_PATH=../northwind/registry
agentbreeder release agent.yaml --stack ../northwind --environment dev
```

The acceptance run is opt-in, and refuses a stack whose gateway is not 1.4.0:

```
AGENTBREEDER_NORTHWIND_DIR="$WORK/northwind" AGENTBREEDER_DOCKER_TEST=1 \
  AGENTBREEDER_NORTHWIND_CLAUDE=1 python -m pytest -q tests/test_northwind_claude_agent_sdk_docker.py
```

`agentbreeder example` does not list this directory: it needs Northwind beside it.
