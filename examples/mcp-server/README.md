# Example MCP Server

A minimal MCP server that demonstrates how to build tools for Agent Garden agents.

## Tools

| Tool | Description |
|------|-------------|
| `calculate` | Safely evaluate math expressions (no `eval`) |
| `get_weather` | Return mock weather data for a city |
| `search_docs` | Return mock search results from Agent Garden docs |

## Install

```bash
cd examples/mcp-server
pip install -r requirements.txt
```

## Run

```bash
python server.py
```

The server communicates over **stdio** (stdin/stdout) using the MCP protocol. It is designed to be launched by an MCP client (such as an Agent Garden agent), not called directly via HTTP.

## Test Each Tool

You can test the server interactively using the MCP inspector:

```bash
npx @modelcontextprotocol/inspector python server.py
```

Or use the `mcp` CLI directly:

```bash
# List available tools
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python server.py

# Call the calculate tool
echo '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"calculate","arguments":{"expression":"2 + 3 * 4"}}}' | python server.py

# Call the get_weather tool
echo '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_weather","arguments":{"city":"San Francisco"}}}' | python server.py

# Call the search_docs tool
echo '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"search_docs","arguments":{"query":"deploy aws"}}}' | python server.py
```

## Register with Agent Garden

Reference this MCP server in your `agent.yaml`:

```yaml
tools:
  - name: example-tools
    type: mcp_server
    command: python examples/mcp-server/server.py
    transport: stdio
```

Or register it in the tool registry:

```bash
garden register tool \
  --name example-tools \
  --type mcp_server \
  --config examples/mcp-server/mcp.yaml
```

## Using the SDK Helper

You can also build MCP servers using the Agent Garden SDK helper for an even more concise pattern. See `sdk/python/agenthub/mcp.py` for the decorator-based API.
