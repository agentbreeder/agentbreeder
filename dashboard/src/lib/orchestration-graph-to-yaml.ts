/** Convert orchestration canvas graph to YAML. */

import type { OrchestrationGraph } from "@/components/orchestration-builder/types";

export function orchestrationGraphToYaml(graph: OrchestrationGraph): string {
  const lines: string[] = [];

  lines.push(`name: ${graph.name}`);
  lines.push(`version: ${graph.version}`);
  if (graph.description) lines.push(`description: "${graph.description}"`);
  lines.push(`strategy: ${graph.strategy}`);
  lines.push("");

  // Agents
  const agentNodes = graph.nodes.filter((n) => n.data.type === "agent");
  const supervisorNodes = graph.nodes.filter((n) => n.data.type === "supervisor");
  const mergeNodes = graph.nodes.filter((n) => n.data.type === "merge");

  // Supervisor config
  if (graph.strategy === "supervisor" && supervisorNodes.length > 0) {
    lines.push("supervisor_config:");
    lines.push(`  supervisor_agent: ${supervisorNodes[0].data.label}`);
  } else if (graph.strategy === "fan_out_fan_in" && mergeNodes.length > 0) {
    lines.push("supervisor_config:");
    lines.push(`  merge_agent: ${mergeNodes[0].data.label}`);
  }

  lines.push("");
  lines.push("agents:");

  const allAgentLike = [...supervisorNodes, ...agentNodes, ...mergeNodes];
  for (const node of allAgentLike) {
    lines.push(`  ${node.data.label}:`);
    lines.push(`    ref: ${node.data.ref || `agents/${node.data.label}`}`);
    if (node.data.routes && node.data.routes.length > 0) {
      lines.push("    routes:");
      for (const rule of node.data.routes) {
        lines.push(`      - condition: "${rule.condition}"`);
        lines.push(`        target: ${rule.target}`);
      }
    }
    if (node.data.fallback) {
      lines.push(`    fallback: ${node.data.fallback}`);
    }
  }

  lines.push("");
  lines.push("shared_state:");
  lines.push("  type: dict");
  lines.push("  backend: in_memory");
  lines.push("");
  lines.push("deploy:");
  lines.push("  target: local");
  lines.push("  resources:");
  lines.push('    cpu: "1"');
  lines.push('    memory: "2Gi"');

  return lines.join("\n") + "\n";
}
