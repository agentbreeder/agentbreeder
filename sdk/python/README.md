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
    .with_model(primary="claude-sonnet-4", fallback="gpt-4o")
    .with_prompt(system="You are a helpful customer support agent.")
    .with_tool(ref="tools/zendesk-mcp")
    .with_tool(ref="tools/order-lookup")
    .with_deploy(cloud="aws", region="us-east-1")
)

# Validate and export to agent.yaml
agent.validate()
agent.to_yaml("agent.yaml")
```

## Multi-Agent Orchestration

```python
from agenthub import Orchestration, KeywordRouter

pipeline = (
    Orchestration("support-router", strategy="router", team="customer-success")
    .add_agent("triage",  ref="agents/triage-agent")
    .add_agent("billing", ref="agents/billing-agent")
    .add_agent("returns", ref="agents/returns-agent")
    .with_route("triage", condition="billing",  target="billing")
    .with_route("triage", condition="return",   target="returns")
)

pipeline.to_yaml("orchestration.yaml")
```

## Key Classes

| Class | Description |
|-------|-------------|
| `Agent` | Define an individual AI agent |
| `Tool` | Define or reference a tool |
| `Model` | Configure a model (primary + fallback) |
| `Memory` | Configure agent memory |
| `Orchestration` | Define multi-agent orchestration |
| `Pipeline` | Sequential agent pipeline |
| `FanOut` | Parallel fan-out orchestration |
| `Supervisor` | Supervisor + worker orchestration |

All classes serialize to the same `agent.yaml` / `orchestration.yaml` format consumed by `agentbreeder deploy`.

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
