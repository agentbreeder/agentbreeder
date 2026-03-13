/** Types for the orchestration visual builder. */

export type OrchestrationStrategy =
  | "router"
  | "sequential"
  | "parallel"
  | "hierarchical"
  | "supervisor"
  | "fan_out_fan_in";

export interface OrchNodeData {
  label: string;
  type: "agent" | "router" | "supervisor" | "merge";
  ref?: string;
  routes?: { condition: string; target: string }[];
  fallback?: string;
  description?: string;
}

export interface OrchNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: OrchNodeData;
}

export interface OrchEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  animated?: boolean;
}

export interface OrchestrationGraph {
  nodes: OrchNode[];
  edges: OrchEdge[];
  strategy: OrchestrationStrategy;
  name: string;
  version: string;
  description: string;
}
