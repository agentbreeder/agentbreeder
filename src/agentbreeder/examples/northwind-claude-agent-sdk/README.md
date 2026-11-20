# Northwind support agent on the Claude Agent SDK

**Status: built and unit-tested, not run end to end, not supported** (#1089, #1083 F7). No image
has been built and no turn has run against a real CLI or gateway. The record is
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

## Run it (from a checkout; not run yet)

```
pip install -e ".[eval]"                       # at the repo root
cd src/agentbreeder/examples/northwind
agentbreeder services up --environment dev --env-file ~/.env
cd ../northwind-claude-agent-sdk
export AGENTBREEDER_REGISTRY_PATH=../northwind/registry
agentbreeder release agent.yaml --stack ../northwind --environment dev
```

The acceptance run is opt-in:

```
AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_CLAUDE=1 \
  python -m pytest -q tests/test_northwind_claude_agent_sdk_docker.py
```

`agentbreeder example` does not list this directory: it needs Northwind beside it.
