# MCP Servers and Agent-to-Agent (A2A)

## MCP (Model Context Protocol)

MCP servers expose tools to agents. AgentBreeder has a built-in MCP registry and can auto-discover running MCP servers.

### Quickstart MCP servers

The quickstart stack starts two MCP servers:

| Server | Port | Tools it provides |
|--------|------|-------------------|
| mcp-filesystem | 3100 | read_file, write_file, list_directory, create_directory |
| mcp-memory | 3101 | store_memory, retrieve_memory, list_memories, delete_memory |

### Registering an MCP server

```bash
# Via CLI
agentbreeder scan                   # auto-discovers running MCP servers
agentbreeder provider add ollama    # add providers too

# Via agent.yaml
tools:
  - ref: tools/mcp-filesystem       # reference by registry name
  - name: my-custom-mcp
    type: mcp
    command: npx my-mcp-server
    transport: stdio
```

### Using MCP tools in the search-agent

```bash
agentbreeder chat search-agent
# Ask: "List files in the workspace"
# Ask: "Remember that the project deadline is next Friday"
# Ask: "What did I ask you to remember?"
```

### Packaging your own MCP server

```yaml
# agent.yaml
tools:
  - name: my-mcp
    type: mcp
    command: python server.py
    transport: stdio
    args: [--port, "3200"]
```

The deployer automatically packages the MCP server as a sidecar container injected alongside the agent.

## Agent-to-Agent (A2A) Communication

AgentBreeder keeps an A2A registry of agent endpoints so agents and services can call each other by name.

### Registering and invoking agents

- `GET /api/v1/a2a/agents` — list registered agents
- `POST /api/v1/a2a/agents` — register an agent endpoint
- `POST /api/v1/a2a/invoke?agent_name=<name>` — look the agent up and POST the message to its `/invoke` endpoint

### Calling external A2A servers

Agents deployed with the sidecar can forward JSON-RPC 2.0 requests to configured A2A peers through
`http://127.0.0.1:9090/a2a/<peer>`.

### Multi-agent systems

Routing between agents (supervisor, router, pipeline) lives in your agent code — for example a LangGraph
graph or a CrewAI crew that calls other agents through the invoke API.
