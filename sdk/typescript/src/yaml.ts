/** YAML serialization for Agent configs. */

import type { AgentConfig } from "./types";

export function agentToYaml(config: AgentConfig): string {
  const lines: string[] = [];

  lines.push(`name: ${config.name}`);
  lines.push(`version: ${config.version}`);
  if (config.description) lines.push(`description: "${config.description}"`);
  lines.push(`team: ${config.team}`);
  lines.push(`owner: ${config.owner}`);
  lines.push(`framework: ${config.framework}`);

  if (config.tags && config.tags.length > 0) {
    lines.push(`tags: [${config.tags.join(", ")}]`);
  }

  lines.push("");
  lines.push("model:");
  lines.push(`  primary: ${config.model.primary}`);
  if (config.model.fallback) lines.push(`  fallback: ${config.model.fallback}`);
  if (config.model.temperature !== undefined) lines.push(`  temperature: ${config.model.temperature}`);
  if (config.model.max_tokens !== undefined) lines.push(`  max_tokens: ${config.model.max_tokens}`);

  if (config.tools && config.tools.length > 0) {
    lines.push("");
    lines.push("tools:");
    for (const tool of config.tools) {
      if (tool.ref) {
        lines.push(`  - ref: ${tool.ref}`);
      } else if (tool.name) {
        lines.push(`  - name: ${tool.name}`);
        if (tool.description) lines.push(`    description: "${tool.description}"`);
      }
    }
  }

  if (config.subagents && config.subagents.length > 0) {
    lines.push("");
    lines.push("subagents:");
    for (const sub of config.subagents) {
      lines.push(`  - ref: ${sub.ref}`);
      if (sub.name) lines.push(`    name: ${sub.name}`);
      if (sub.description) lines.push(`    description: "${sub.description}"`);
    }
  }

  if (config.mcp_servers && config.mcp_servers.length > 0) {
    lines.push("");
    lines.push("mcp_servers:");
    for (const mcp of config.mcp_servers) {
      lines.push(`  - ref: ${mcp.ref}`);
      if (mcp.transport && mcp.transport !== "stdio") lines.push(`    transport: ${mcp.transport}`);
    }
  }

  if (config.prompts?.system) {
    lines.push("");
    lines.push("prompts:");
    lines.push(`  system: "${config.prompts.system}"`);
  }

  if (config.guardrails && config.guardrails.length > 0) {
    lines.push("");
    lines.push("guardrails:");
    for (const g of config.guardrails) {
      lines.push(`  - ${g}`);
    }
  }

  lines.push("");
  lines.push("deploy:");
  lines.push(`  cloud: ${config.deploy.cloud}`);
  if (config.deploy.runtime) lines.push(`  runtime: ${config.deploy.runtime}`);
  if (config.deploy.region) lines.push(`  region: ${config.deploy.region}`);

  return lines.join("\n") + "\n";
}
