// mcp_ts_server.ts — MCP TypeScript server template
// Platform-managed. Do not edit — regenerated on each deploy.
import { createServer } from 'node:http'
import { buildAgentCard, buildHealthResponse } from './_shared_loader.js'

// Developer's tools.ts exports named async tool functions
import * as tools from './tools.js'

const PORT = parseInt(process.env.PORT ?? '{{PORT}}', 10)

interface McpRequest {
  jsonrpc: '2.0'
  id: string | number
  method: string
  params?: Record<string, unknown>
}

function mcpResponse(id: string | number, result: unknown) {
  return { jsonrpc: '2.0', id, result }
}

function mcpError(id: string | number, code: number, message: string) {
  return { jsonrpc: '2.0', id, error: { code, message } }
}

function jsonResponse(res: import('node:http').ServerResponse, status: number, data: unknown): void {
  const body = JSON.stringify(data)
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(body)
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://localhost:${PORT}`)

  if (req.method === 'GET' && url.pathname === '/health') {
    return jsonResponse(res, 200, buildHealthResponse())
  }

  if (req.method === 'GET' && url.pathname === '/.well-known/agent.json') {
    return jsonResponse(res, 200, { ...buildAgentCard(), type: 'mcp-server' })
  }

  if (req.method === 'POST' && url.pathname === '/mcp') {
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk as Buffer)
    const rpc: McpRequest = JSON.parse(Buffer.concat(chunks).toString())

    if (rpc.method === 'initialize') {
      return jsonResponse(res, 200, mcpResponse(rpc.id, {
        protocolVersion: '2024-11-05',
        capabilities: { tools: {} },
        serverInfo: { name: '{{AGENT_NAME}}', version: '{{AGENT_VERSION}}' },
      }))
    }

    if (rpc.method === 'tools/list') {
      const toolList = Object.keys(tools).map(name => ({
        name,
        description: `Tool: ${name}`,
        inputSchema: { type: 'object', properties: {}, additionalProperties: true },
      }))
      return jsonResponse(res, 200, mcpResponse(rpc.id, { tools: toolList }))
    }

    if (rpc.method === 'tools/call') {
      const toolName = rpc.params?.name as string
      const toolArgs = (rpc.params?.arguments ?? {}) as Record<string, unknown>
      const fn = (tools as Record<string, unknown>)[toolName]
      if (typeof fn !== 'function') {
        return jsonResponse(res, 200, mcpError(rpc.id, -32601, `Tool not found: ${toolName}`))
      }
      try {
        const result = await fn(toolArgs)
        return jsonResponse(res, 200, mcpResponse(rpc.id, {
          content: [{ type: 'text', text: typeof result === 'string' ? result : JSON.stringify(result) }],
        }))
      } catch (err) {
        return jsonResponse(res, 200, mcpError(rpc.id, -32000, String(err)))
      }
    }

    return jsonResponse(res, 200, mcpError(rpc.id, -32601, `Method not found: ${rpc.method}`))
  }

  jsonResponse(res, 404, { error: 'Not found' })
})

server.listen(PORT, () => console.log(`[{{AGENT_NAME}}] MCP TypeScript server listening on :${PORT}`))
