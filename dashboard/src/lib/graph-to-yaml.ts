/**
 * Graph-to-YAML converter for the Visual Agent Builder.
 * Converts ReactFlow nodes + edges into a valid agent.yaml structure.
 */

import type { Node, Edge } from "@xyflow/react";
import type {
  CanvasNodeData,
  AgentNodeData,
  ModelNodeData,
  ToolNodeData,
  McpServerNodeData,
  PromptNodeData,
  MemoryNodeData,
  RagNodeData,
  GuardrailNodeData,
} from "@/components/visual-builder/types";

// ---------------------------------------------------------------------------
// Graph → YAML string
// ---------------------------------------------------------------------------

export function graphToYaml(nodes: Node<CanvasNodeData>[], edges: Edge[]): string {
  // Find the agent node (there should be exactly one)
  const agentNode = nodes.find((n) => n.data.type === "agent");
  if (!agentNode) {
    return "# No agent node found — drag an Agent node onto the canvas";
  }

  const agentData = agentNode.data as AgentNodeData;

  // Find all nodes connected to the agent via edges.
  // Edges go from agent (source) → component (target)
  const connectedIds = new Set<string>();
  for (const edge of edges) {
    if (edge.source === agentNode.id) {
      connectedIds.add(edge.target);
    }
    if (edge.target === agentNode.id) {
      connectedIds.add(edge.source);
    }
  }

  const connectedNodes = nodes.filter((n) => connectedIds.has(n.id));

  // Categorize connected nodes
  const models = connectedNodes.filter((n) => n.data.type === "model") as Node<ModelNodeData>[];
  const tools = connectedNodes.filter((n) => n.data.type === "tool") as Node<ToolNodeData>[];
  const mcpServers = connectedNodes.filter((n) => n.data.type === "mcpServer") as Node<McpServerNodeData>[];
  const prompts = connectedNodes.filter((n) => n.data.type === "prompt") as Node<PromptNodeData>[];
  const memories = connectedNodes.filter((n) => n.data.type === "memory") as Node<MemoryNodeData>[];
  const rags = connectedNodes.filter((n) => n.data.type === "rag") as Node<RagNodeData>[];
  const guardrails = connectedNodes.filter((n) => n.data.type === "guardrail") as Node<GuardrailNodeData>[];

  const lines: string[] = [];

  // --- Identity ---
  lines.push(`name: ${agentData.name || "my-agent"}`);
  lines.push(`version: "${agentData.version || "0.1.0"}"`);
  if (agentData.description) {
    lines.push(`description: "${agentData.description}"`);
  }
  lines.push(`team: ${agentData.team || "engineering"}`);
  lines.push(`owner: ${agentData.owner || "user@example.com"}`);
  if (agentData.tags && agentData.tags.length > 0) {
    lines.push(`tags: [${agentData.tags.join(", ")}]`);
  }

  // --- Model ---
  const primaryModel = models.find((m) => m.data.role === "primary");
  const fallbackModel = models.find((m) => m.data.role === "fallback");

  lines.push("");
  lines.push("model:");
  lines.push(`  primary: ${primaryModel?.data.name || "claude-sonnet-4"}`);
  if (fallbackModel?.data.name) {
    lines.push(`  fallback: ${fallbackModel.data.name}`);
  }
  if (primaryModel) {
    lines.push(`  temperature: ${primaryModel.data.temperature}`);
    lines.push(`  max_tokens: ${primaryModel.data.maxTokens}`);
  } else {
    lines.push("  temperature: 0.7");
    lines.push("  max_tokens: 4096");
  }

  // --- Framework ---
  lines.push("");
  lines.push(`framework: ${agentData.framework || "langgraph"}`);

  // --- Tools ---
  lines.push("");
  const allTools = [...tools, ...mcpServers];
  if (allTools.length === 0) {
    lines.push("tools: []");
  } else {
    lines.push("tools:");
    for (const t of tools) {
      if (t.data.ref) {
        lines.push(`  - ref: ${t.data.ref}`);
      } else {
        lines.push(`  - name: ${t.data.name || "unnamed"}`);
        lines.push(`    type: ${t.data.toolType || "function"}`);
      }
    }
    for (const m of mcpServers) {
      lines.push(`  - name: ${m.data.name || "mcp-server"}`);
      lines.push(`    type: mcp`);
      lines.push(`    endpoint: ${m.data.endpoint || "http://localhost:3000"}`);
      lines.push(`    transport: ${m.data.transport || "stdio"}`);
    }
  }

  // --- Knowledge Bases ---
  if (rags.length > 0) {
    lines.push("");
    lines.push("knowledge_bases:");
    for (const r of rags) {
      lines.push(`  - ref: ${r.data.ref || `kb/${r.data.name || "index"}`}`);
    }
  }

  // --- Prompts ---
  const systemPrompt = prompts.find((p) => p.data.role === "system");
  lines.push("");
  lines.push("prompts:");
  if (systemPrompt) {
    if (systemPrompt.data.ref) {
      lines.push(`  system: ${systemPrompt.data.ref}`);
    } else if (systemPrompt.data.content) {
      lines.push(`  system: "${systemPrompt.data.content.replace(/"/g, '\\"')}"`);
    } else {
      lines.push('  system: ""');
    }
  } else {
    lines.push('  system: ""');
  }

  // --- Memory ---
  if (memories.length > 0) {
    lines.push("");
    lines.push("memory:");
    const mem = memories[0].data;
    lines.push(`  backend: ${mem.backendType || "redis"}`);
    lines.push(`  type: ${mem.memoryType || "conversation"}`);
    lines.push(`  max_messages: ${mem.maxMessages}`);
  }

  // --- Guardrails ---
  lines.push("");
  if (guardrails.length === 0) {
    lines.push("guardrails: []");
  } else {
    lines.push("guardrails:");
    for (const g of guardrails) {
      lines.push(`  - ${g.data.guardrailType}`);
    }
  }

  // --- Deploy ---
  lines.push("");
  lines.push("deploy:");
  lines.push(`  cloud: ${agentData.cloud || "local"}`);
  lines.push(`  runtime: ${agentData.runtime || "docker-compose"}`);
  lines.push("  scaling:");
  lines.push(`    min: ${agentData.scalingMin ?? 1}`);
  lines.push(`    max: ${agentData.scalingMax ?? 10}`);

  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// YAML string → Graph (nodes + edges)
// Parses the YAML and creates positioned nodes with edges to the agent node.
// ---------------------------------------------------------------------------

export function yamlToGraph(yaml: string): {
  nodes: Node<CanvasNodeData>[];
  edges: Edge[];
} {
  const nodes: Node<CanvasNodeData>[] = [];
  const edges: Edge[] = [];

  // Simple line-based parser (reusing the pattern from agent-builder.tsx)
  const lines = yaml.split("\n");
  let currentSection = "";

  let name = "my-agent";
  let version = "0.1.0";
  let description = "";
  let team = "engineering";
  let owner = "user@example.com";
  let framework = "langgraph";
  let tags: string[] = [];
  let cloud = "local";
  let runtime = "docker-compose";
  let scalingMin = 1;
  let scalingMax = 10;

  let modelPrimary = "claude-sonnet-4";
  let modelFallback = "";
  let temperature = 0.7;
  let maxTokens = 4096;

  const toolRefs: string[] = [];
  const guardrailTypes: string[] = [];
  let systemPrompt = "";
  const kbRefs: string[] = [];

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    if (!line.startsWith(" ") && !line.startsWith("\t")) {
      const colonIdx = trimmed.indexOf(":");
      if (colonIdx === -1) continue;
      const key = trimmed.slice(0, colonIdx).trim();
      const val = trimmed.slice(colonIdx + 1).trim().replace(/^"|"$/g, "");
      currentSection = key;

      switch (key) {
        case "name": name = val; break;
        case "version": version = val; break;
        case "description": description = val; break;
        case "team": team = val; break;
        case "owner": owner = val; break;
        case "framework": framework = val; break;
        case "tags": {
          const m = val.match(/\[([^\]]*)\]/);
          if (m) tags = m[1].split(",").map((t) => t.trim()).filter(Boolean);
          break;
        }
      }
    } else {
      const colonIdx = trimmed.indexOf(":");
      if (colonIdx === -1) {
        if (trimmed.startsWith("- ")) {
          const itemVal = trimmed.slice(2).trim();
          if (currentSection === "guardrails") {
            guardrailTypes.push(itemVal);
          }
        }
        continue;
      }

      const key = trimmed.slice(0, colonIdx).trim().replace(/^- /, "");
      const val = trimmed.slice(colonIdx + 1).trim().replace(/^"|"$/g, "");

      if (key.startsWith("- ref") && currentSection === "tools") {
        toolRefs.push(val);
        continue;
      }
      if (key.startsWith("- ref") && currentSection === "knowledge_bases") {
        kbRefs.push(val);
        continue;
      }

      if (currentSection === "model") {
        switch (key) {
          case "primary": modelPrimary = val; break;
          case "fallback": modelFallback = val; break;
          case "temperature": temperature = parseFloat(val) || 0.7; break;
          case "max_tokens": maxTokens = parseInt(val) || 4096; break;
        }
      } else if (currentSection === "prompts") {
        if (key === "system") systemPrompt = val;
      } else if (currentSection === "deploy") {
        switch (key) {
          case "cloud": cloud = val; break;
          case "runtime": runtime = val; break;
          case "min": scalingMin = parseInt(val) || 1; break;
          case "max": scalingMax = parseInt(val) || 10; break;
        }
      }
    }
  }

  // Create agent node at center
  const agentId = "agent-1";
  nodes.push({
    id: agentId,
    type: "agent",
    position: { x: 400, y: 300 },
    data: {
      type: "agent",
      name,
      version,
      description,
      team,
      owner,
      framework,
      tags,
      cloud,
      runtime,
      scalingMin,
      scalingMax,
    },
  });

  let nodeIdx = 0;
  const addNode = (
    type: string,
    data: CanvasNodeData,
    xOffset: number,
    yPosition: number
  ) => {
    const id = `${type}-${++nodeIdx}`;
    nodes.push({
      id,
      type,
      position: { x: 400 + xOffset, y: yPosition },
      data,
    });
    // Edge from agent → component
    edges.push({
      id: `e-${agentId}-${id}`,
      source: agentId,
      target: id,
      type: "smoothstep",
    });
  };

  // Model nodes (right side, top)
  let rightY = 100;
  if (modelPrimary) {
    addNode("model", {
      type: "model",
      name: modelPrimary,
      provider: "",
      temperature,
      maxTokens,
      role: "primary",
    }, 320, rightY);
    rightY += 160;
  }
  if (modelFallback) {
    addNode("model", {
      type: "model",
      name: modelFallback,
      provider: "",
      temperature,
      maxTokens,
      role: "fallback",
    }, 320, rightY);
    rightY += 160;
  }

  // Tool nodes (right side, middle)
  for (const ref of toolRefs) {
    const toolName = ref.split("/").pop() ?? ref;
    addNode("tool", {
      type: "tool",
      ref,
      name: toolName,
      toolType: "function",
    }, 320, rightY);
    rightY += 120;
  }

  // Prompt node (left side)
  let leftY = 150;
  if (systemPrompt) {
    const isRef = systemPrompt.startsWith("prompts/");
    addNode("prompt", {
      type: "prompt",
      ref: isRef ? systemPrompt : "",
      name: isRef ? systemPrompt.split("/").pop() ?? "system" : "system-prompt",
      content: isRef ? "" : systemPrompt,
      role: "system",
    }, -320, leftY);
    leftY += 140;
  }

  // Knowledge base nodes (left side, below prompt)
  for (const ref of kbRefs) {
    const kbName = ref.split("/").pop() ?? ref;
    addNode("rag", {
      type: "rag",
      ref,
      name: kbName,
      embeddingModel: "",
    }, -320, leftY);
    leftY += 120;
  }

  // Guardrail nodes (below agent)
  let bottomY = 520;
  for (const gType of guardrailTypes) {
    const gLabels: Record<string, string> = {
      pii_detection: "PII Detection",
      content_filter: "Content Filter",
      hallucination_check: "Hallucination Check",
    };
    addNode("guardrail", {
      type: "guardrail",
      name: gLabels[gType] ?? gType,
      guardrailType: gType,
    }, 0, bottomY);
    bottomY += 110;
  }

  return { nodes, edges };
}
