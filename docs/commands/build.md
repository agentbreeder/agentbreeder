# `agentbreeder build`

Build an agent file into an image and a deploy manifest (never deploys).

Implemented: `build` (see [`agent-yaml.md`](../agent-yaml.md)).

The deploy manifest (`deploy/<name>.deploy.yaml`, plus `<name>-candidate.deploy.yaml` when the
environment has a candidate identity) is made in two steps. The template holds what comes from
the agent and its runtime only: port, health path, model aliases, budget, rpm and MCP tools. It is
checked before `docker build` runs. Binding then adds what comes from the operator config for the
environment (identity, secret bindings and the model gateway) and the image ID. A template never
carries an identity, a secret, a policy or a target.

Flags: `agentbreeder build --help`.
