// deepagent_server.ts — DeepAgent server template
// Platform-managed. Do not edit — regenerated on each deploy.
import { createServer } from 'node:http'
import { aps, buildAgentCard, buildHealthResponse, verifyAuth } from './_shared_loader.js'
import { agent } from './agent.js'

const PORT = parseInt(process.env.PORT ?? '{{PORT}}', 10)

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
    return jsonResponse(res, 200, buildAgentCard())
  }

  if (req.method === 'POST' && url.pathname === '/invoke') {
    if (!verifyAuth(req, res)) return
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk as Buffer)
    const body = JSON.parse(Buffer.concat(chunks).toString())
    const input: string = body.input ?? (body.messages?.[body.messages.length - 1]?.content ?? '')

    const result = await agent.run(input)
    aps.cost.record({ agentName: '{{AGENT_NAME}}', model: '{{AGENT_FRAMEWORK}}', inputTokens: 0, outputTokens: 0 })
    return jsonResponse(res, 200, { output: typeof result === 'string' ? result : JSON.stringify(result) })
  }

  if (req.method === 'POST' && url.pathname === '/stream') {
    if (!verifyAuth(req, res)) return
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk as Buffer)
    const body = JSON.parse(Buffer.concat(chunks).toString())
    const input: string = body.input ?? (body.messages?.[body.messages.length - 1]?.content ?? '')

    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' })
    const result = await agent.run(input)
    const text = typeof result === 'string' ? result : JSON.stringify(result)
    res.write(`data: ${JSON.stringify({ delta: text })}\n\n`)
    res.write('data: [DONE]\n\n')
    res.end()
    return
  }

  jsonResponse(res, 404, { error: 'Not found' })
})

server.listen(PORT, () => console.log(`[{{AGENT_NAME}}] DeepAgent server listening on :${PORT}`))
