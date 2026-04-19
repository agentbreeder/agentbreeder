/** Validation helpers for AgentBreeder configs. */

import type { AgentConfig } from "./types";

export function validateAgent(config: AgentConfig): string[] {
  const errors: string[] = [];

  if (!config.name) errors.push("name is required");
  if (!config.version) errors.push("version is required");
  if (!config.team) errors.push("team is required");
  if (!config.framework) errors.push("framework is required");
  if (!config.model?.primary) errors.push("model.primary is required");
  if (!config.deploy?.cloud) errors.push("deploy.cloud is required");

  const semverRe = /^\d+\.\d+\.\d+/;
  if (config.version && !semverRe.test(config.version)) {
    errors.push("version must be semver (e.g. 1.0.0)");
  }

  return errors;
}
