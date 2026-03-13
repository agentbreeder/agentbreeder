/**
 * Full Code Orchestration SDK for Agent Garden.
 *
 * Provides builder-pattern classes for defining multi-agent workflows:
 * - Orchestration: base class wrapping orchestration.yaml config
 * - Pipeline: sequential agent chains
 * - FanOut: parallel execution with merge strategies
 * - Supervisor: hierarchical orchestration with supervisor agent
 * - Router: custom routing logic (KeywordRouter, IntentRouter, RoundRobinRouter, ClassifierRouter)
 *
 * @example
 * ```ts
 * import { Orchestration, Pipeline, FanOut, Supervisor, KeywordRouter } from "@agent-garden/sdk";
 *
 * // Router-based
 * const support = new Orchestration("support-pipeline", "router", { team: "eng" })
 *   .addAgent("triage", "agents/triage")
 *   .addAgent("billing", "agents/billing")
 *   .withRoute("triage", "billing", "billing")
 *   .withSharedState("session_context", "redis");
 *
 * // Sequential pipeline
 * const research = new Pipeline("research", { team: "eng" })
 *   .step("researcher", "agents/researcher")
 *   .step("summarizer", "agents/summarizer");
 *
 * // Fan-out + merge
 * const analysis = new FanOut("multi-analysis")
 *   .worker("sentiment", "agents/sentiment")
 *   .worker("topics", "agents/topics")
 *   .merge("agents/aggregator");
 *
 * // Hierarchical supervisor
 * const workflow = new Supervisor("research-workflow")
 *   .withSupervisorAgent("coordinator", "agents/coordinator")
 *   .worker("researcher", "agents/researcher")
 *   .withMaxIterations(5);
 * ```
 */

export type Strategy =
  | "router"
  | "sequential"
  | "parallel"
  | "hierarchical"
  | "supervisor"
  | "fan_out_fan_in";

export type MergeStrategy = "first_wins" | "majority_vote" | "aggregate" | "custom";

export interface RouteRule {
  condition: string;
  target: string;
}

export interface OrchAgentDef {
  ref: string;
  routes?: RouteRule[];
  fallback?: string;
}

export interface SharedStateConfig {
  type: string;
  backend: string;
}

export interface SupervisorConfig {
  supervisor_agent?: string;
  merge_agent?: string;
  max_iterations?: number;
}

export interface OrchestrationDeployConfig {
  target: string;
  resources?: Record<string, string>;
}

export interface OrchestrationConfig {
  name: string;
  version: string;
  description?: string;
  team?: string;
  owner?: string;
  strategy: Strategy;
  agents: Record<string, OrchAgentDef>;
  shared_state?: SharedStateConfig;
  supervisor_config?: SupervisorConfig;
  deploy?: OrchestrationDeployConfig;
  tags?: string[];
}

// ---------------------------------------------------------------------------
// Base Orchestration class
// ---------------------------------------------------------------------------

export class Orchestration {
  protected config: OrchestrationConfig;

  constructor(
    name: string,
    strategy: Strategy = "router",
    opts?: {
      version?: string;
      team?: string;
      owner?: string;
      description?: string;
      tags?: string[];
    },
  ) {
    this.config = {
      name,
      version: opts?.version ?? "1.0.0",
      description: opts?.description,
      team: opts?.team,
      owner: opts?.owner,
      strategy,
      agents: {},
      tags: opts?.tags,
    };
  }

  addAgent(
    name: string,
    ref: string,
    opts?: { routes?: RouteRule[]; fallback?: string },
  ): this {
    this.config.agents[name] = { ref, ...opts };
    return this;
  }

  withRoute(agentName: string, condition: string, target: string): this {
    const agent = this.config.agents[agentName];
    if (!agent) {
      throw new Error(`Agent '${agentName}' not found. Call addAgent() first.`);
    }
    agent.routes = [...(agent.routes ?? []), { condition, target }];
    return this;
  }

  withSharedState(type: string, backend: string): this {
    this.config.shared_state = { type, backend };
    return this;
  }

  withSupervisor(agentName: string, maxIterations?: number): this {
    this.config.supervisor_config = {
      ...this.config.supervisor_config,
      supervisor_agent: agentName,
      ...(maxIterations !== undefined ? { max_iterations: maxIterations } : {}),
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

  withDeploy(target: string, resources?: Record<string, string>): this {
    this.config.deploy = { target, ...(resources ? { resources } : {}) };
    return this;
  }

  tag(...tags: string[]): this {
    this.config.tags = [...(this.config.tags ?? []), ...tags];
    return this;
  }

  // ------------------------------------------------------------------
  // Validation
  // ------------------------------------------------------------------

  validate(): string[] {
    const errors: string[] = [];
    const namePattern = /^[a-z0-9][a-z0-9-]*[a-z0-9]$/;

    if (!this.config.name) {
      errors.push("name is required");
    } else if (!namePattern.test(this.config.name)) {
      errors.push(
        `name must be lowercase alphanumeric with hyphens (e.g., my-pipeline), got: '${this.config.name}'`,
      );
    }

    const versionPattern = /^\d+\.\d+\.\d+$/;
    if (!versionPattern.test(this.config.version)) {
      errors.push(`version must be semver (e.g., 1.0.0), got: '${this.config.version}'`);
    }

    const validStrategies: Strategy[] = [
      "router",
      "sequential",
      "parallel",
      "hierarchical",
      "supervisor",
      "fan_out_fan_in",
    ];
    if (!validStrategies.includes(this.config.strategy)) {
      errors.push(
        `strategy must be one of ${validStrategies.join(", ")}, got: '${this.config.strategy}'`,
      );
    }

    if (Object.keys(this.config.agents).length === 0) {
      errors.push("at least one agent is required");
    }

    if (
      ["supervisor", "hierarchical"].includes(this.config.strategy) &&
      !this.config.supervisor_config?.supervisor_agent
    ) {
      errors.push(
        `strategy '${this.config.strategy}' requires withSupervisor() to be configured`,
      );
    }

    if (
      this.config.strategy === "fan_out_fan_in" &&
      !this.config.supervisor_config?.merge_agent
    ) {
      errors.push(
        "strategy 'fan_out_fan_in' requires a merge_agent — call withMergeAgent()",
      );
    }

    // Validate route targets and fallbacks reference known agents
    for (const [agentName, agentDef] of Object.entries(this.config.agents)) {
      for (const route of agentDef.routes ?? []) {
        if (route.target && !this.config.agents[route.target]) {
          errors.push(
            `Route target '${route.target}' in agent '${agentName}' is not a known agent`,
          );
        }
      }
      if (agentDef.fallback && !this.config.agents[agentDef.fallback]) {
        errors.push(
          `Fallback '${agentDef.fallback}' for agent '${agentName}' is not a known agent`,
        );
      }
    }

    return errors;
  }

  // ------------------------------------------------------------------
  // Serialization
  // ------------------------------------------------------------------

  /** Serialize to valid orchestration.yaml content (string). */
  toYaml(): string {
    return orchestrationToYaml(this.config);
  }

  /** Return a deep copy of the raw config object. */
  toConfig(): OrchestrationConfig {
    return JSON.parse(JSON.stringify(this.config)) as OrchestrationConfig;
  }

  // ------------------------------------------------------------------
  // Deployment
  // ------------------------------------------------------------------

  deploy(target?: string): Record<string, unknown> {
    const deployTarget = target ?? this.config.deploy?.target ?? "local";
    return {
      orchestration: this.config.name,
      version: this.config.version,
      strategy: this.config.strategy,
      target: deployTarget,
      status: "pending",
    };
  }
}

// ---------------------------------------------------------------------------
// Specialised subclasses
// ---------------------------------------------------------------------------

/** Sequential agent chain: each step's output feeds the next step's input. */
export class Pipeline extends Orchestration {
  private _steps: string[] = [];

  constructor(name: string, opts?: ConstructorParameters<typeof Orchestration>[2]) {
    super(name, "sequential", opts);
  }

  step(name: string, ref: string, fallback?: string): this {
    this.addAgent(name, ref, fallback ? { fallback } : undefined);
    this._steps.push(name);
    return this;
  }

  validate(): string[] {
    const errors = super.validate();
    if (this._steps.length < 2) {
      errors.push("Pipeline requires at least 2 steps");
    }
    return errors;
  }
}

/** Fan-out to multiple parallel workers, then merge results. */
export class FanOut extends Orchestration {
  private _mergeStrategy: MergeStrategy = "aggregate";

  constructor(name: string, opts?: ConstructorParameters<typeof Orchestration>[2]) {
    super(name, "fan_out_fan_in", opts);
  }

  worker(name: string, ref: string): this {
    return this.addAgent(name, ref);
  }

  merge(ref: string, name = "merger"): this {
    this.addAgent(name, ref);
    this.config.supervisor_config = {
      ...this.config.supervisor_config,
      merge_agent: name,
    };
    return this;
  }

  withMergeStrategy(strategy: MergeStrategy): this {
    const valid: MergeStrategy[] = ["first_wins", "majority_vote", "aggregate", "custom"];
    if (!valid.includes(strategy)) {
      throw new Error(`merge_strategy must be one of ${valid.join(", ")}`);
    }
    this._mergeStrategy = strategy;
    return this;
  }

  validate(): string[] {
    const errors = super.validate();
    if (!this.config.supervisor_config?.merge_agent) {
      errors.push("FanOut requires a merge agent — call .merge(ref)");
    }
    return errors;
  }
}

/** Hierarchical orchestration: supervisor agent delegates work to workers. */
export class Supervisor extends Orchestration {
  constructor(name: string, opts?: ConstructorParameters<typeof Orchestration>[2]) {
    super(name, "supervisor", opts);
  }

  withSupervisorAgent(name: string, ref: string): this {
    this.addAgent(name, ref);
    this.config.supervisor_config = {
      ...this.config.supervisor_config,
      supervisor_agent: name,
    };
    return this;
  }

  worker(name: string, ref: string, fallback?: string): this {
    return this.addAgent(name, ref, fallback ? { fallback } : undefined);
  }

  withMaxIterations(maxIterations: number): this {
    this.config.supervisor_config = {
      ...this.config.supervisor_config,
      max_iterations: maxIterations,
    };
    return this;
  }

  validate(): string[] {
    const errors = super.validate();
    if (!this.config.supervisor_config?.supervisor_agent) {
      errors.push("Supervisor requires withSupervisorAgent() to be called");
    }
    return errors;
  }
}

// ---------------------------------------------------------------------------
// Router classes
// ---------------------------------------------------------------------------

/** Base class for custom routing logic. Override route() to implement. */
export abstract class Router {
  abstract route(message: string, context: Record<string, unknown>): Promise<string>;
}

/** Routes messages to agents based on keyword presence. */
export class KeywordRouter extends Router {
  constructor(
    private rules: Record<string, string>,
    private defaultAgent: string,
    private caseSensitive = false,
  ) {
    super();
  }

  async route(message: string, _context: Record<string, unknown>): Promise<string> {
    const text = this.caseSensitive ? message : message.toLowerCase();
    for (const [keyword, target] of Object.entries(this.rules)) {
      const k = this.caseSensitive ? keyword : keyword.toLowerCase();
      if (text.includes(k)) return target;
    }
    return this.defaultAgent;
  }
}

/** Routes based on a pre-classified intent in context["intent"]. */
export class IntentRouter extends Router {
  constructor(
    private intents: Record<string, string>,
    private defaultAgent: string,
  ) {
    super();
  }

  async route(_message: string, context: Record<string, unknown>): Promise<string> {
    const intent = (context["intent"] as string) ?? "";
    return this.intents[intent] ?? this.defaultAgent;
  }
}

/** Distributes messages to agents in round-robin order. */
export class RoundRobinRouter extends Router {
  private idx = 0;

  constructor(private agents: string[]) {
    super();
  }

  async route(_message: string, _context: Record<string, unknown>): Promise<string> {
    const target = this.agents[this.idx % this.agents.length];
    this.idx++;
    return target;
  }
}

/**
 * Base class for model/ML-based classification routing.
 * Override classify() to implement LLM or classifier-based intent detection.
 */
export abstract class ClassifierRouter extends Router {
  constructor(
    protected labelToAgent: Record<string, string>,
    protected defaultAgent: string,
  ) {
    super();
  }

  abstract classify(message: string): Promise<string>;

  async route(message: string, _context: Record<string, unknown>): Promise<string> {
    const label = await this.classify(message);
    return this.labelToAgent[label] ?? this.defaultAgent;
  }
}

// ---------------------------------------------------------------------------
// YAML serialization
// ---------------------------------------------------------------------------

function indent(n: number): string {
  return "  ".repeat(n);
}

function yamlStr(value: string): string {
  if (/[:{}\[\],&*?|<>=!%@`#]/.test(value) || value.includes("\n")) {
    return `"${value.replace(/"/g, '\\"')}"`;
  }
  return value;
}

/** Serialize an OrchestrationConfig to valid orchestration.yaml content. */
export function orchestrationToYaml(config: OrchestrationConfig): string {
  const lines: string[] = [];

  lines.push(`name: ${yamlStr(config.name)}`);
  lines.push(`version: ${config.version}`);
  if (config.description) lines.push(`description: ${yamlStr(config.description)}`);
  if (config.team) lines.push(`team: ${config.team}`);
  if (config.owner) lines.push(`owner: ${config.owner}`);
  if (config.tags && config.tags.length > 0) {
    lines.push(`tags: [${config.tags.join(", ")}]`);
  }

  lines.push("");
  lines.push(`strategy: ${config.strategy}`);

  lines.push("");
  lines.push("agents:");
  for (const [name, def] of Object.entries(config.agents)) {
    lines.push(`${indent(1)}${name}:`);
    lines.push(`${indent(2)}ref: ${yamlStr(def.ref)}`);
    if (def.routes && def.routes.length > 0) {
      lines.push(`${indent(2)}routes:`);
      for (const route of def.routes) {
        lines.push(`${indent(2)}- condition: ${yamlStr(route.condition)}`);
        lines.push(`${indent(3)}target: ${route.target}`);
      }
    }
    if (def.fallback) {
      lines.push(`${indent(2)}fallback: ${def.fallback}`);
    }
  }

  if (config.shared_state) {
    lines.push("");
    lines.push("shared_state:");
    lines.push(`${indent(1)}type: ${config.shared_state.type}`);
    lines.push(`${indent(1)}backend: ${config.shared_state.backend}`);
  }

  if (config.supervisor_config) {
    const sc = config.supervisor_config;
    lines.push("");
    lines.push("supervisor_config:");
    if (sc.supervisor_agent) lines.push(`${indent(1)}supervisor_agent: ${sc.supervisor_agent}`);
    if (sc.merge_agent) lines.push(`${indent(1)}merge_agent: ${sc.merge_agent}`);
    if (sc.max_iterations !== undefined && sc.max_iterations !== 3) {
      lines.push(`${indent(1)}max_iterations: ${sc.max_iterations}`);
    }
  }

  if (config.deploy) {
    lines.push("");
    lines.push("deploy:");
    lines.push(`${indent(1)}target: ${config.deploy.target}`);
    if (config.deploy.resources) {
      lines.push(`${indent(1)}resources:`);
      for (const [k, v] of Object.entries(config.deploy.resources)) {
        lines.push(`${indent(2)}${k}: ${v}`);
      }
    }
  }

  return lines.join("\n") + "\n";
}
