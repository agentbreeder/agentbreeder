/**
 * Minimal TypeScript agent demonstrating the AgentBreeder registry pattern.
 *
 * Uses the same registry refs as the Python agents:
 *   - prompts/ts-greeter-system   -> ./prompts/ts-greeter-system.md
 *   - tools/get-utc-time          -> ./tools/get_utc_time.ts
 *
 * The resolvers (resolvePrompt, resolveTool) live in
 * engine/runtimes/templates/node/_shared_loader.ts and are mirror-images of
 * the Python engine.prompt_resolver / engine.tool_resolver.
 *
 * This file is intentionally framework-agnostic — it shows the pattern, not
 * any one TS agent SDK. Wire the resolved prompt + tool into your runtime
 * (Vercel AI SDK, OpenAI Agents TS, Mastra, etc.) however you prefer.
 *
 * Run:
 *   npm install
 *   AGENTBREEDER_REGISTRY_URL=http://localhost:8000 \
 *   AGENTBREEDER_REGISTRY_TOKEN=<jwt> \
 *   npx tsx agent.ts
 */
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { resolvePrompt, resolveTool } from './lib/registry.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const PROJECT_ROOT = resolve(__dirname)

async function main(): Promise<void> {
  const instruction = await resolvePrompt('prompts/ts-greeter-system', PROJECT_ROOT)
  const getUtcTime = await resolveTool<[], { utc_time: string }>(
    'tools/get-utc-time',
    PROJECT_ROOT,
  )

  console.log('--- resolved prompt ---')
  console.log(instruction)
  console.log()
  console.log('--- tool call result ---')
  console.log(getUtcTime())
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
