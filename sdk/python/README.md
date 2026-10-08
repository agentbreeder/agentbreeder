# agentbreeder-sdk

The official Python SDK for [AgentBreeder](https://github.com/agentbreeder/agentbreeder) — define, validate, and deploy AI agents programmatically.

## Installation

```bash
pip install agentbreeder-sdk
```

For MCP server authoring support:

```bash
pip install "agentbreeder-sdk[mcp]"
```

## Quick Start

```python
from agenthub import Agent, Tool, Model, Memory

# Define an agent
agent = (
    Agent("customer-support", version="1.0.0", team="customer-success")
    .with_model(primary="claude-sonnet-4-6", fallback="gpt-4o")
    .with_prompt(system="You are a helpful customer support agent.")
    .with_tool(Tool.from_ref("tools/zendesk-mcp"))
    .with_tool(Tool.from_ref("tools/order-lookup"))
    .with_deploy(cloud="aws", region="us-east-1")
)

# Validate and export to agent.yaml
agent.validate()
agent.to_yaml("agent.yaml")
```

## Key Classes

| Class | Description |
|-------|-------------|
| `Agent` | Define an individual AI agent |
| `Tool` | Define or reference a tool |
| `Model` | Configure a model (primary + fallback) |
| `Memory` | Configure agent memory |

All classes serialize to the same `agent.yaml` format consumed by `agentbreeder deploy`.

## Tier Mobility

The SDK is the **Full Code** tier of AgentBreeder. You can eject from a YAML config to SDK code at any time:

```bash
agentbreeder eject agent.yaml --output agent_sdk.py
```

## TypeScript SDK

Looking for TypeScript / JavaScript? Install the official TypeScript SDK:

```bash
npm install @agentbreeder/sdk
```

See [`sdk/typescript/`](../../sdk/typescript/README.md) for full documentation.

## Links

- [Documentation](https://www.agentbreeder.io)
- [GitHub](https://github.com/agentbreeder/agentbreeder)
- [agent.yaml reference](https://www.agentbreeder.io/agent-yaml)
- [TypeScript SDK on npm](https://www.npmjs.com/package/@agentbreeder/sdk)
