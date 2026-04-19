/** Core types for AgentBreeder TypeScript SDK. */

import type { MemoryConfig } from "./memory";

export type FrameworkType =
  | "langgraph"
  | "crewai"
  | "claude_sdk"
  | "openai_agents"
  | "google_adk"
  | "custom";

export type CloudType = "aws" | "gcp" | "kubernetes" | "local";

export type Visibility = "public" | "team" | "private";

export interface ModelConfig {
  primary: string;
  fallback?: string;
  gateway?: string;
  temperature?: number;
  max_tokens?: number;
}

export interface ToolConfig {
  ref?: string;
  name?: string;
  type?: string;
  description?: string;
  schema?: Record<string, unknown>;
}

export interface SubagentRef {
  ref: string;
  name?: string;
  description?: string;
}

export interface McpServerRef {
  ref: string;
  transport?: "stdio" | "sse" | "streamable_http";
}

export interface PromptConfig {
  system?: string;
}

export interface ScalingConfig {
  min?: number;
  max?: number;
  target_cpu?: number;
}

export interface ResourceConfig {
  cpu?: string;
  memory?: string;
}

export interface DeployConfig {
  cloud: CloudType;
  runtime?: string;
  region?: string;
  scaling?: ScalingConfig;
  resources?: ResourceConfig;
  env_vars?: Record<string, string>;
  secrets?: string[];
}

export interface AccessConfig {
  visibility?: Visibility;
  allowed_callers?: string[];
  require_approval?: boolean;
}

export interface AgentConfig {
  name: string;
  version: string;
  description?: string;
  team: string;
  owner: string;
  framework: FrameworkType;
  tags?: string[];
  model: ModelConfig;
  tools?: ToolConfig[];
  subagents?: SubagentRef[];
  mcp_servers?: McpServerRef[];
  knowledge_bases?: Array<{ ref: string }>;
  prompts?: PromptConfig;
  guardrails?: string[];
  deploy: DeployConfig;
  access?: AccessConfig;
  memory?: MemoryConfig;
}

export type { MemoryConfig };

export interface OrchestrationStrategy {
  type: "router" | "sequential" | "parallel" | "hierarchical" | "supervisor" | "fan_out_fan_in";
}
