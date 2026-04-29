// vercel_ai_server.ts — Vercel AI SDK server template
// Platform-managed. Do not edit — regenerated on each deploy.
import { streamText } from 'ai'
import { createServer } from 'node:http'
import { aps, buildAgentCard, buildHealthResponse, verifyAuth } from './_shared_loader.js'

// Developer's agent.ts exports: model, systemPrompt, tools
import { model, systemPrompt, tools as agentTools } from './agent.js'

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
    const messages: Array<{ role: 'user' | 'assistant'; content: string }> = body.messages ?? [
      { role: 'user', content: body.input ?? '' },
    ]

    const threadId: string | undefined = body.thread_id
    if (threadId) {
      const history = await aps.memory.load(threadId)
      messages.unshift(...history.map(m => ({ role: m.role as 'user' | 'assistant', content: m.content })))
    }

    const { text } = await streamText({
      model,
      system: systemPrompt,
      messages,
      tools: agentTools,
    })

    if (threadId) {
      await aps.memory.save(threadId, [
        ...messages,
        { role: 'assistant', content: text },
      ])
    }

    aps.cost.record({
      agentName: '{{AGENT_NAME}}',
      model: '{{AGENT_NAME}}',
      inputTokens: 0,
      outputTokens: 0,
    })

    return jsonResponse(res, 200, { output: text })
  }

  if (req.method === 'POST' && url.pathname === '/stream') {
    if (!verifyAuth(req, res)) return
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk as Buffer)
    const body = JSON.parse(Buffer.concat(chunks).toString())
    const messages: Array<{ role: 'user' | 'assistant'; content: string }> = body.messages ?? [
      { role: 'user', content: body.input ?? '' },
    ]

    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    })

    const { textStream } = streamText({ model, system: systemPrompt, messages, tools: agentTools })
    for await (const delta of textStream) {
      res.write(`data: ${JSON.stringify({ delta })}\n\n`)
    }
    res.write('data: [DONE]\n\n')
    res.end()
    return
  }

  jsonResponse(res, 404, { error: 'Not found' })
})

server.listen(PORT, () => {
  console.log(`[{{AGENT_NAME}}] Vercel AI server listening on :${PORT}`)
})
