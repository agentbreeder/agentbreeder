// mcp_py_server.ts — MCP Python stdio proxy template
// Platform-managed. Do not edit — regenerated on each deploy.
import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { buildAgentCard, buildHealthResponse, verifyAuth } from './_shared_loader.js'

const PORT = parseInt(process.env.PORT ?? '{{PORT}}', 10)
const PYTHON_MCP_ENTRY = process.env.PYTHON_MCP_ENTRY ?? 'tools.py'

function jsonResponse(res: import('node:http').ServerResponse, status: number, data: unknown): void {
  const body = JSON.stringify(data)
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(body)
}

async function callPythonMcp(request: unknown): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const proc = spawn('python3', [PYTHON_MCP_ENTRY], {
      stdio: ['pipe', 'pipe', 'inherit'],
    })

    let output = ''
    proc.stdout.on('data', (chunk: Buffer) => { output += chunk.toString() })
    proc.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(`Python MCP process exited with code ${code}`))
        return
      }
      try {
        // Find the last complete JSON line (MCP response)
        const lines = output.trim().split('\n').filter(l => l.trim())
        const lastLine = lines[lines.length - 1]
        resolve(JSON.parse(lastLine))
      } catch {
        reject(new Error(`Failed to parse Python MCP response: ${output}`))
      }
    })
    proc.on('error', reject)

    proc.stdin.write(JSON.stringify(request) + '\n')
    proc.stdin.end()
  })
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
    if (!verifyAuth(req, res)) return
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk as Buffer)
    const request = JSON.parse(Buffer.concat(chunks).toString())

    try {
      const result = await callPythonMcp(request)
      return jsonResponse(res, 200, result)
    } catch (err) {
      return jsonResponse(res, 500, { jsonrpc: '2.0', id: request.id ?? null, error: { code: -32000, message: String(err) } })
    }
  }

  jsonResponse(res, 404, { error: 'Not found' })
})

server.listen(PORT, () => console.log(`[{{AGENT_NAME}}] MCP Python proxy server listening on :${PORT}`))
