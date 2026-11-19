# `agentbreeder build`

Build an agent file into an image and a deploy manifest (never deploys).

Implemented: `build` (see [`agent-yaml.md`](../agent-yaml.md)). On `main` (not in a published preview), `build` checks `subagents:` and refuses it on a runtime without the `subagents` capability (`RUNTIME_NO_SUBAGENTS`); see "Sub-agents" in [`agent-yaml.md`](../agent-yaml.md#sub-agents-on-main-not-in-a-published-preview).

The deploy manifest (`deploy/<name>.deploy.yaml`, plus `<name>-candidate.deploy.yaml` when the
environment has a candidate identity) is made in two steps. The template holds what comes from
the agent and its runtime only: port, health path, model aliases, budget, rpm and MCP tools. It is
checked before `docker build` runs. Binding then adds what comes from the operator config for the
environment (identity, secret bindings and the model gateway) and the image ID. A template never
carries an identity, a secret, a policy or a target.

On `main` (not in a published preview, #829): `build` fetches the refs an agent takes from git sources declared under `registries:`, writes `agent.lock` version 2 with their pins, and prints one `REF <- SOURCE @PIN` line per remote ref; `--offline` never uses the network (remote versions come from the cache only). See [Remote registry sources](../registry.md#remote-registry-sources-git-on-main-not-in-a-published-preview).

Flags: `agentbreeder build --help`.
