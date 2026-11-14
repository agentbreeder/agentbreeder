# Focused rebuild reference

MCP servers run behind a stack-hosted relay, one per `stack.yaml` `mcp:` entry (local-only preview); see [MCP servers through a relay](services.md#mcp-servers-through-a-relay). An agent's tool grant can be edited with [`agentbreeder mcp grant|revoke`](commands/mcp.md) (on `main`, not in a published preview). Agents may also bring their own tools inside their container.
