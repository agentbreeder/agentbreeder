// openai_agents_ts_server.ts — OpenAI Agents TS server template
// Platform-managed. Do not edit — regenerated on each deploy.
import { Runner } from '@openai/agents'
import { createServer } from 'node:http'
import { aps, buildAgentCard, buildHealthResponse, verifyAuth } from './_shared_loader.js'
import { agent } from './agent.js'

const PORT = parseInt(process.env.PORT ?? '{{PORT}}', 10)

function jsonResponse(res: import('node:http').ServerResponse, status: number, data: unknown): void {
  const body = JSON.stringify(data)
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(body)
}

interface ToolCall {
  name: string
  args: Record<string, unknown>
  result: string
  duration_ms: number
  started_at: string
}

function extractToolHistory(newItems: unknown[]): ToolCall[] {
  const byId = new Map<string, ToolCall>()
  const order: string[] = []
  const nowIso = new Date().toISOString()

  for (const item of newItems) {
    const raw = (item as { rawItem?: unknown }).rawItem ?? (item as { raw_item?: unknown }).raw_item
    const rawAny = (raw ?? item) as Record<string, unknown>
    const type = (rawAny.type ?? (item as { type?: string }).type) as string | undefined
    const callId =
      (rawAny.callId as string | undefined) ??
      (rawAny.call_id as string | undefined) ??
      (rawAny.id as string | undefined) ??
      `_anon_${order.length}`

    if (type === 'function_call' || type === 'tool_call') {
      const name = (rawAny.name as string | undefined) ?? 'unknown'
      let args: Record<string, unknown> = {}
      const rawArgs = (rawAny.arguments as unknown) ?? (rawAny.args as unknown)
      if (typeof rawArgs === 'string') {
        try {
          const parsed = JSON.parse(rawArgs)
          if (parsed && typeof parsed === 'object') args = parsed as Record<string, unknown>
          else args = { raw: rawArgs }
        } catch {
          args = { raw: rawArgs }
        }
      } else if (rawArgs && typeof rawArgs === 'object') {
        args = rawArgs as Record<string, unknown>
      }
      const existing = byId.get(callId) ?? { name, args, result: '', duration_ms: 0, started_at: nowIso }
      existing.name = name
      existing.args = args
      byId.set(callId, existing)
      if (!order.includes(callId)) order.push(callId)
    } else if (type === 'function_call_output' || type === 'tool_call_output') {
      const out = (rawAny.output as unknown) ?? (rawAny.result as unknown) ?? ''
      const resultStr =
        typeof out === 'string' ? out : (() => { try { return JSON.stringify(out) } catch { return String(out) } })()
      const existing = byId.get(callId) ?? { name: 'unknown', args: {}, result: '', duration_ms: 0, started_at: nowIso }
      existing.result = resultStr
      byId.set(callId, existing)
      if (!order.includes(callId)) order.push(callId)
    }
  }

  return order.map((id) => byId.get(id)!).filter(Boolean)
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

    const result = await Runner.run(agent, input)
    aps.cost.record({ agentName: '{{AGENT_NAME}}', model: '{{AGENT_FRAMEWORK}}', inputTokens: 0, outputTokens: 0 })
    // Structured tool-call timeline (#215). The OpenAI Agents JS SDK emits
    // newItems with raw_item.type of "function_call" / "function_call_output";
    // pair them up by call_id so the dashboard playground can render a tool-
    // call timeline without regex-scraping the message body.
    const history = extractToolHistory((result as { newItems?: unknown[] }).newItems ?? [])
    return jsonResponse(res, 200, { output: result.finalOutput ?? result, history })
  }

  if (req.method === 'POST' && url.pathname === '/stream') {
    if (!verifyAuth(req, res)) return
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk as Buffer)
    const body = JSON.parse(Buffer.concat(chunks).toString())
    const input: string = body.input ?? (body.messages?.[body.messages.length - 1]?.content ?? '')

    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' })
    const result = await Runner.run(agent, input)
    const text = String(result.finalOutput ?? result)
    res.write(`data: ${JSON.stringify({ delta: text })}\n\n`)
    res.write('data: [DONE]\n\n')
    res.end()
    return
  }

  jsonResponse(res, 404, { error: 'Not found' })
})

server.listen(PORT, () => console.log(`[{{AGENT_NAME}}] OpenAI Agents TS server listening on :${PORT}`))
