// _shared_loader.ts — shared APS client + server helpers + registry resolvers
// Platform-managed. Do not edit — regenerated on each deploy.
import { readFileSync, existsSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { join, resolve as resolvePath } from 'node:path'
import { APSClient } from './aps-client.js'

export const aps = new APSClient()

const PROMPT_REF_PREFIX = 'prompts/'
const TOOL_REF_PREFIX = 'tools/'

/**
 * Resolve a `prompts/<name>` ref or inline string to the prompt body.
 * TS counterpart of `engine.prompt_resolver.resolve_prompt`.
 *
 * Resolution order:
 *   1. Inline literal: returned as-is when value does not start with "prompts/"
 *   2. Local file: <projectRoot>/prompts/<name>.md
 *   3. Registry API: GET ${AGENTBREEDER_REGISTRY_URL}/api/v1/registry/prompts
 *      (filter by name, optional version after "@")
 */
export async function resolvePrompt(
  value: string,
  projectRoot: string = process.cwd(),
): Promise<string> {
  if (!value.startsWith(PROMPT_REF_PREFIX)) return value

  const ref = value.slice(PROMPT_REF_PREFIX.length)
  const [name, version] = ref.includes('@') ? ref.split('@', 2) : [ref, undefined]

  // 1. local file
  const local = resolvePath(projectRoot, 'prompts', `${name}.md`)
  if (existsSync(local)) {
    return readFileSync(local, 'utf-8')
  }

  // 2. registry API
  const baseUrl = (process.env.AGENTBREEDER_REGISTRY_URL ?? '').replace(/\/+$/, '')
  if (baseUrl) {
    const token = (process.env.AGENTBREEDER_REGISTRY_TOKEN ?? '').trim()
    const params = new URLSearchParams({ name })
    if (version) params.set('version', version)
    const headers: Record<string, string> = {}
    if (token) headers.Authorization = `Bearer ${token}`
    try {
      const resp = await fetch(`${baseUrl}/api/v1/registry/prompts?${params}`, { headers })
      if (resp.ok) {
        const payload = (await resp.json()) as { data?: Array<{ name: string; version: string; content: string }> }
        const matches = (payload.data ?? []).filter((p) => p.name === name && (!version || p.version === version))
        matches.sort((a, b) => b.version.localeCompare(a.version))
        if (matches[0]?.content) return matches[0].content
      }
    } catch {
      /* swallow — fall through to error */
    }
  }

  throw new Error(
    `Prompt ref '${value}' not found. Looked at ${local} and ${baseUrl || '<registry not configured>'}`,
  )
}

/**
 * Resolve a `tools/<kebab-name>` ref to a callable. TS counterpart of
 * `engine.tool_resolver.resolve_tool`.
 *
 * Resolution order:
 *   1. Local override: <projectRoot>/tools/<snake_name>.{ts,js}
 *      The module must export a function with the same snake_case name.
 *   2. Registry API: returns metadata only (caller must wrap into a callable).
 *
 * Note: TS has no "standard library" of tools yet — that lives in
 * engine.tools.standard.* in Python. Until a TS port exists, agents reference
 * tools via local files or HTTP/MCP endpoints.
 */
export async function resolveTool<TArgs extends unknown[] = unknown[], TReturn = unknown>(
  ref: string,
  projectRoot: string = process.cwd(),
): Promise<(...args: TArgs) => TReturn | Promise<TReturn>> {
  if (!ref.startsWith(TOOL_REF_PREFIX)) {
    throw new Error(`'${ref}' is not a tool reference (must start with 'tools/')`)
  }
  const kebab = ref.slice(TOOL_REF_PREFIX.length)
  const snake = kebab.replace(/-/g, '_')

  // 1. local override (.ts then .js)
  for (const ext of ['ts', 'js', 'mjs']) {
    const local = resolvePath(projectRoot, 'tools', `${snake}.${ext}`)
    if (existsSync(local)) {
      const mod = (await import(local)) as Record<string, unknown>
      const fn = mod[snake]
      if (typeof fn === 'function') {
        return fn as (...args: TArgs) => TReturn | Promise<TReturn>
      }
      throw new Error(`${local} does not export function '${snake}'`)
    }
  }

  // 2. registry metadata only
  const baseUrl = (process.env.AGENTBREEDER_REGISTRY_URL ?? '').replace(/\/+$/, '')
  if (baseUrl) {
    const token = (process.env.AGENTBREEDER_REGISTRY_TOKEN ?? '').trim()
    const headers: Record<string, string> = {}
    if (token) headers.Authorization = `Bearer ${token}`
    try {
      const resp = await fetch(`${baseUrl}/api/v1/registry/tools`, { headers })
      if (resp.ok) {
        const payload = (await resp.json()) as { data?: Array<{ name: string; endpoint?: string }> }
        const match = (payload.data ?? []).find((t) => t.name === kebab)
        if (match) {
          throw new Error(
            `Tool '${ref}' was found in the registry as metadata only ` +
              `(endpoint=${match.endpoint}). Wrap it into a callable yourself, ` +
              `or install the matching package.`,
          )
        }
      }
    } catch (e) {
      if ((e as Error).message?.includes('metadata only')) throw e
    }
  }

  throw new Error(
    `Tool ref '${ref}' not found. Expected ${join(projectRoot, 'tools', snake + '.{ts,js,mjs}')} or registry entry.`,
  )
}

/**
 * Bearer-token auth gate.
 *
 * Returns `true` if the request is allowed through. Returns `false` after
 * writing a 401/403 JSON response (the caller must `return` immediately).
 *
 * Disabled (always returns `true`) when AGENT_AUTH_TOKEN env var is unset/empty
 * so local dev works without ceremony. /health and /.well-known/agent.json are
 * intentionally NOT gated so Cloud Run / k8s probes can hit them without creds.
 */
export function verifyAuth(req: IncomingMessage, res: ServerResponse): boolean {
  const expected = (process.env.AGENT_AUTH_TOKEN ?? '').trim()
  if (!expected) return true

  const header = req.headers['authorization']
  if (typeof header !== 'string' || !header.startsWith('Bearer ')) {
    res.writeHead(401, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ detail: 'Missing bearer token' }))
    return false
  }
  const presented = header.slice('Bearer '.length).trim()
  if (presented !== expected) {
    res.writeHead(403, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ detail: 'Invalid bearer token' }))
    return false
  }
  return true
}

export function buildHealthResponse() {
  return {
    status: 'ok',
    agent: '{{AGENT_NAME}}',
    version: '{{AGENT_VERSION}}',
    framework: '{{AGENT_FRAMEWORK}}',
  }
}

export function buildAgentCard() {
  return {
    name: '{{AGENT_NAME}}',
    version: '{{AGENT_VERSION}}',
    framework: '{{AGENT_FRAMEWORK}}',
    endpoints: {
      invoke: '/invoke',
      stream: '/stream',
      health: '/health',
    },
    protocol: 'a2a-v1',
  }
}
