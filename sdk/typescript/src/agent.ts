/** Agent builder — TypeScript equivalent of the Python SDK Agent class. */

import { Model } from "./model";
import { Tool } from "./tool";
import type {
  AgentConfig,
  CloudType,
  DeployConfig,
  FrameworkType,
  McpServerRef,
  PromptConfig,
  SubagentRef,
  ToolConfig,
} from "./types";
import { agentToYaml } from "./yaml";

export interface AgentOptions {
  version?: string;
  description?: string;
  team?: string;
  owner?: string;
  framework?: FrameworkType;
  tags?: string[];
}

export class Agent {
  private _name: string;
  private _version: string;
  private _description: string;
  private _team: string;
  private _owner: string;
  private _framework: FrameworkType;
  private _tags: string[];
  private _model: Model | null = null;
  private _tools: ToolConfig[] = [];
  private _subagents: SubagentRef[] = [];
  private _mcpServers: McpServerRef[] = [];
  private _prompts: PromptConfig = {};
  private _guardrails: string[] = [];
  private _deploy: DeployConfig | null = null;
  private _knowledgeBases: Array<{ ref: string }> = [];

  constructor(name: string, opts: AgentOptions = {}) {
    this._name = name;
    this._version = opts.version ?? "1.0.0";
    this._description = opts.description ?? "";
    this._team = opts.team ?? "default";
    this._owner = opts.owner ?? "";
    this._framework = opts.framework ?? "custom";
    this._tags = opts.tags ?? [];
  }

  withModel(primary: string, opts?: { fallback?: string; temperature?: number; maxTokens?: number }): this {
    const model = new Model(primary);
    if (opts?.fallback) model.fallback(opts.fallback);
    if (opts?.temperature !== undefined) model.temperature(opts.temperature);
    if (opts?.maxTokens !== undefined) model.maxTokens(opts.maxTokens);
    this._model = model;
    return this;
  }

  withTool(tool: Tool): this {
    this._tools.push(tool.toConfig());
    return this;
  }

  withSubagent(ref: string, opts?: { name?: string; description?: string }): this {
    this._subagents.push({ ref, ...opts });
    return this;
  }

  withMcpServer(ref: string, transport?: "stdio" | "sse" | "streamable_http"): this {
    this._mcpServers.push({ ref, transport });
    return this;
  }

  withPrompt(system: string): this {
    this._prompts = { system };
    return this;
  }

  withGuardrail(name: string): this {
    this._guardrails.push(name);
    return this;
  }

  withDeploy(cloud: CloudType, opts?: Partial<Omit<DeployConfig, "cloud">>): this {
    this._deploy = { cloud, ...opts };
    return this;
  }

  tag(...tags: string[]): this {
    this._tags.push(...tags);
    return this;
  }

  toConfig(): AgentConfig {
    if (!this._model) throw new Error("Model is required — call .withModel()");
    if (!this._deploy) throw new Error("Deploy config is required — call .withDeploy()");

    return {
      name: this._name,
      version: this._version,
      description: this._description,
      team: this._team,
      owner: this._owner,
      framework: this._framework,
      tags: this._tags,
      model: this._model.toConfig(),
      tools: this._tools,
      subagents: this._subagents.length > 0 ? this._subagents : undefined,
      mcp_servers: this._mcpServers.length > 0 ? this._mcpServers : undefined,
      knowledge_bases: this._knowledgeBases.length > 0 ? this._knowledgeBases : undefined,
      prompts: this._prompts,
      guardrails: this._guardrails,
      deploy: this._deploy,
    };
  }

  toYaml(): string {
    return agentToYaml(this.toConfig());
  }

  validate(): string[] {
    const errors: string[] = [];
    if (!this._name) errors.push("name is required");
    if (!this._model) errors.push("model is required — call .withModel()");
    if (!this._deploy) errors.push("deploy config is required — call .withDeploy()");
    if (!this._team) errors.push("team is required");
    return errors;
  }
}
