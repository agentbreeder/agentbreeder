# Examples have moved

The examples ship inside the package, so an installed `agentbreeder` carries them too. In a
checkout they live under [`src/agentbreeder/examples/`](../src/agentbreeder/examples/):

- [`http-agent`](../src/agentbreeder/examples/http-agent/README.md): the local deploy quickstart
  sample. It echoes JSON; it is not an AI agent.
- [`northwind`](../src/agentbreeder/examples/northwind/README.md): the Northwind support agent
  built from `agent.yaml`, with its backing services from `stack.yaml`.

`agentbreeder example list` lists them and `agentbreeder example NAME [DIR]` copies one out
(default `./NAME`). Only the shipped files are copied, never local state from a run in place.

Published registry versions inside `northwind/registry/` are hashed in its locks. Don't edit
them in place; publish a new version instead.
