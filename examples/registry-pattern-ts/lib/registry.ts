/**
 * Local copy of the AgentBreeder registry resolvers for the TS example.
 *
 * In production, these helpers come bundled with the deploy pipeline at
 * engine/runtimes/templates/node/_shared_loader.ts. This file mirrors that
 * source so the example is self-contained and runnable without the full
 * AgentBreeder repo on the import path.
 *
 * Keep this in sync with `engine/runtimes/templates/node/_shared_loader.ts`.
 */
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve as resolvePath } from 'node:path'

const PROMPT_REF_PREFIX = 'prompts/'
const TOOL_REF_PREFIX = 'tools/'

export async function resolvePrompt(
  value: string,
  projectRoot: string = process.cwd(),
): Promise<string> {
  if (!value.startsWith(PROMPT_REF_PREFIX)) return value

  const ref = value.slice(PROMPT_REF_PREFIX.length)
  const [name, version] = ref.includes('@') ? ref.split('@', 2) : [ref, undefined]

  const local = resolvePath(projectRoot, 'prompts', `${name}.md`)
  if (existsSync(local)) {
    return readFileSync(local, 'utf-8')
  }

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
        const payload = (await resp.json()) as {
          data?: Array<{ name: string; version: string; content: string }>
        }
        const matches = (payload.data ?? []).filter(
          (p) => p.name === name && (!version || p.version === version),
        )
        matches.sort((a, b) => b.version.localeCompare(a.version))
        if (matches[0]?.content) return matches[0].content
      }
    } catch {
      /* fall through */
    }
  }

  throw new Error(
    `Prompt ref '${value}' not found. Looked at ${local} and ${baseUrl || '<registry not configured>'}`,
  )
}

export async function resolveTool<TArgs extends unknown[] = unknown[], TReturn = unknown>(
  ref: string,
  projectRoot: string = process.cwd(),
): Promise<(...args: TArgs) => TReturn | Promise<TReturn>> {
  if (!ref.startsWith(TOOL_REF_PREFIX)) {
    throw new Error(`'${ref}' is not a tool reference (must start with 'tools/')`)
  }
  const kebab = ref.slice(TOOL_REF_PREFIX.length)
  const snake = kebab.replace(/-/g, '_')

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

  throw new Error(
    `Tool ref '${ref}' not found at ${join(projectRoot, 'tools', snake + '.{ts,js,mjs}')}`,
  )
}
