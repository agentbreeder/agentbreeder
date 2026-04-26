// _shared_loader.ts — shared APS client + server helpers
// Platform-managed. Do not edit — regenerated on each deploy.
import { APSClient } from './aps-client.js'

export const aps = new APSClient()

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
