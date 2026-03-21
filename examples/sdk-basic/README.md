# SDK Basic Example

Demonstrates the AgentBreeder Python SDK builder-pattern API.

## Usage

```bash
pip install agentbreeder-sdk  # or: pip install -e sdk/python/
python agent.py
```

## What it does

1. Defines a customer support agent using the fluent builder API
2. Validates the configuration
3. Exports to `agent.yaml` (which can be deployed with `agentbreeder deploy agent.yaml`)
