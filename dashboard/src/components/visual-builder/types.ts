/**
 * Shared types for the Visual Agent Builder (ReactFlow canvas).
 */

export type CanvasNodeType =
  | "agent"
  | "model"
  | "tool"
  | "mcpServer"
  | "prompt"
  | "memory"
  | "rag"
  | "guardrail";

/** Data stored on each ReactFlow node, keyed by node type. */

export interface AgentNodeData extends Record<string, unknown> {
  type: "agent";
  name: string;
  version: string;
  description: string;
  team: string;
  owner: string;
  framework: string;
  tags: string[];
  cloud: string;
  runtime: string;
  scalingMin: number;
  scalingMax: number;
}

export interface ModelNodeData extends Record<string, unknown> {
  type: "model";
  name: string;
  provider: string;
  temperature: number;
  maxTokens: number;
  role: "primary" | "fallback";
}

export interface ToolNodeData extends Record<string, unknown> {
  type: "tool";
  ref: string;
  name: string;
  toolType: string;
}

export interface McpServerNodeData extends Record<string, unknown> {
  type: "mcpServer";
  name: string;
  endpoint: string;
  transport: "stdio" | "sse" | "streamable_http";
}

export interface PromptNodeData extends Record<string, unknown> {
  type: "prompt";
  ref: string;
  name: string;
  content: string;
  role: "system" | "user";
}

export interface MemoryNodeData extends Record<string, unknown> {
  type: "memory";
  name: string;
  backendType: string;
  memoryType: string;
  maxMessages: number;
}

export interface RagNodeData extends Record<string, unknown> {
  type: "rag";
  ref: string;
  name: string;
  embeddingModel: string;
}

export interface GuardrailNodeData extends Record<string, unknown> {
  type: "guardrail";
  name: string;
  guardrailType: string;
}

export type CanvasNodeData =
  | AgentNodeData
  | ModelNodeData
  | ToolNodeData
  | McpServerNodeData
  | PromptNodeData
  | MemoryNodeData
  | RagNodeData
  | GuardrailNodeData;

/** Configuration for each palette item. */
export interface PaletteItem {
  type: CanvasNodeType;
  label: string;
  icon: string;
  color: string;
  bgColor: string;
  borderColor: string;
  defaultData: CanvasNodeData;
}

/** Node style configuration per type. */
export const NODE_STYLES: Record<
  CanvasNodeType,
  { color: string; bgColor: string; borderColor: string; icon: string; label: string }
> = {
  agent: {
    color: "text-blue-700 dark:text-blue-300",
    bgColor: "bg-blue-50 dark:bg-blue-950/40",
    borderColor: "border-blue-300 dark:border-blue-700",
    icon: "Bot",
    label: "Agent",
  },
  model: {
    color: "text-purple-700 dark:text-purple-300",
    bgColor: "bg-purple-50 dark:bg-purple-950/40",
    borderColor: "border-purple-300 dark:border-purple-700",
    icon: "Cpu",
    label: "Model",
  },
  tool: {
    color: "text-green-700 dark:text-green-300",
    bgColor: "bg-green-50 dark:bg-green-950/40",
    borderColor: "border-green-300 dark:border-green-700",
    icon: "Wrench",
    label: "Tool",
  },
  mcpServer: {
    color: "text-teal-700 dark:text-teal-300",
    bgColor: "bg-teal-50 dark:bg-teal-950/40",
    borderColor: "border-teal-300 dark:border-teal-700",
    icon: "Server",
    label: "MCP Server",
  },
  prompt: {
    color: "text-amber-700 dark:text-amber-300",
    bgColor: "bg-amber-50 dark:bg-amber-950/40",
    borderColor: "border-amber-300 dark:border-amber-700",
    icon: "FileText",
    label: "Prompt",
  },
  memory: {
    color: "text-pink-700 dark:text-pink-300",
    bgColor: "bg-pink-50 dark:bg-pink-950/40",
    borderColor: "border-pink-300 dark:border-pink-700",
    icon: "Brain",
    label: "Memory",
  },
  rag: {
    color: "text-indigo-700 dark:text-indigo-300",
    bgColor: "bg-indigo-50 dark:bg-indigo-950/40",
    borderColor: "border-indigo-300 dark:border-indigo-700",
    icon: "Database",
    label: "RAG Index",
  },
  guardrail: {
    color: "text-red-700 dark:text-red-300",
    bgColor: "bg-red-50 dark:bg-red-950/40",
    borderColor: "border-red-300 dark:border-red-700",
    icon: "Shield",
    label: "Guardrail",
  },
};
