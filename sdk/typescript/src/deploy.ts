/** Deployment helpers. */

import type { AgentConfig } from "./types";

export interface DeployResult {
  agent: string;
  version: string;
  target: string;
  status: string;
}

export async function deploy(config: AgentConfig, target = "local"): Promise<DeployResult> {
  // Placeholder — real implementation would call the garden CLI or API
  return {
    agent: config.name,
    version: config.version,
    target,
    status: "pending",
  };
}
