/** Orchestration builder for multi-agent workflows. */

export type Strategy =
  | "router"
  | "sequential"
  | "parallel"
  | "hierarchical"
  | "supervisor"
  | "fan_out_fan_in";

export interface OrchAgentDef {
  ref: string;
  routes?: Array<{ condition: string; target: string }>;
  fallback?: string;
}

export interface OrchestrationConfig {
  name: string;
  version: string;
  description?: string;
  team?: string;
  owner?: string;
  strategy: Strategy;
  agents: Record<string, OrchAgentDef>;
  supervisor_config?: {
    supervisor_agent?: string;
    merge_agent?: string;
    max_iterations?: number;
  };
}

export class Orchestration {
  private config: OrchestrationConfig;

  constructor(name: string, strategy: Strategy, opts?: { version?: string; team?: string; owner?: string; description?: string }) {
    this.config = {
      name,
      version: opts?.version ?? "1.0.0",
      description: opts?.description,
      team: opts?.team,
      owner: opts?.owner,
      strategy,
      agents: {},
    };
  }

  addAgent(name: string, ref: string, opts?: { routes?: OrchAgentDef["routes"]; fallback?: string }): this {
    this.config.agents[name] = { ref, ...opts };
    return this;
  }

  withSupervisor(agentName: string, maxIterations?: number): this {
    this.config.supervisor_config = {
      ...this.config.supervisor_config,
      supervisor_agent: agentName,
      max_iterations: maxIterations,
    };
    return this;
  }

  withMergeAgent(agentName: string): this {
    this.config.supervisor_config = {
      ...this.config.supervisor_config,
      merge_agent: agentName,
    };
    return this;
  }

  toConfig(): OrchestrationConfig {
    return { ...this.config };
  }
}
