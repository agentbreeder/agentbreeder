/**
 * Local override for the `get-utc-time` tool ref.
 *
 * Resolved by `resolveTool('tools/get-utc-time')` from agent.ts. Function name
 * must match the kebab-case ref converted to snake_case (`get_utc_time`).
 */
export const SCHEMA = {
  type: 'object',
  properties: {},
  required: [],
} as const

export function get_utc_time(): { utc_time: string } {
  return { utc_time: new Date().toISOString() }
}
