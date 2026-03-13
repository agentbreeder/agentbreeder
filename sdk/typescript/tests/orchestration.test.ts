import { describe, it, expect } from "vitest";
import {
  Orchestration,
  Pipeline,
  FanOut,
  Supervisor,
  KeywordRouter,
  IntentRouter,
  RoundRobinRouter,
  ClassifierRouter,
  orchestrationToYaml,
} from "../src";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRouterOrch() {
  return new Orchestration("support-pipeline", "router", { team: "eng" })
    .addAgent("triage", "agents/triage-agent")
    .addAgent("billing", "agents/billing-agent")
    .addAgent("general", "agents/general-agent")
    .withRoute("triage", "billing", "billing")
    .withRoute("triage", "default", "general");
}

// ---------------------------------------------------------------------------
// Orchestration base class
// ---------------------------------------------------------------------------

describe("Orchestration", () => {
  it("constructs with defaults", () => {
    const orch = new Orchestration("my-pipeline");
    const cfg = orch.toConfig();
    expect(cfg.name).toBe("my-pipeline");
    expect(cfg.version).toBe("1.0.0");
    expect(cfg.strategy).toBe("router");
  });

  it("accepts options", () => {
    const orch = new Orchestration("pipeline", "sequential", {
      version: "2.0.0",
      team: "ml",
      owner: "alice@co.com",
      description: "A pipeline",
    });
    const cfg = orch.toConfig();
    expect(cfg.version).toBe("2.0.0");
    expect(cfg.team).toBe("ml");
    expect(cfg.description).toBe("A pipeline");
  });

  it("addAgent adds an agent", () => {
    const orch = new Orchestration("test").addAgent("agent-a", "agents/a");
    const cfg = orch.toConfig();
    expect(cfg.agents["agent-a"].ref).toBe("agents/a");
  });

  it("withRoute appends route to agent", () => {
    const orch = makeRouterOrch();
    const routes = orch.toConfig().agents["triage"].routes ?? [];
    expect(routes).toHaveLength(2);
    expect(routes[0].condition).toBe("billing");
    expect(routes[0].target).toBe("billing");
  });

  it("withRoute throws when agent not found", () => {
    const orch = new Orchestration("test");
    expect(() => orch.withRoute("ghost", "x", "y")).toThrow("not found");
  });

  it("withSharedState sets shared_state", () => {
    const orch = new Orchestration("test")
      .addAgent("a", "agents/a")
      .withSharedState("session_context", "redis");
    expect(orch.toConfig().shared_state).toEqual({ type: "session_context", backend: "redis" });
  });

  it("withSupervisor sets supervisor_config", () => {
    const orch = new Orchestration("test", "supervisor")
      .addAgent("coord", "agents/coord")
      .withSupervisor("coord", 5);
    expect(orch.toConfig().supervisor_config?.supervisor_agent).toBe("coord");
    expect(orch.toConfig().supervisor_config?.max_iterations).toBe(5);
  });

  it("withMergeAgent sets merge_agent", () => {
    const orch = new Orchestration("test", "fan_out_fan_in")
      .addAgent("merger", "agents/merger")
      .withMergeAgent("merger");
    expect(orch.toConfig().supervisor_config?.merge_agent).toBe("merger");
  });

  it("withDeploy sets deploy config", () => {
    const orch = new Orchestration("test")
      .addAgent("a", "agents/a")
      .withDeploy("cloud-run", { cpu: "1" });
    const cfg = orch.toConfig();
    expect(cfg.deploy?.target).toBe("cloud-run");
    expect(cfg.deploy?.resources?.cpu).toBe("1");
  });

  it("tag adds tags", () => {
    const orch = new Orchestration("test")
      .addAgent("a", "agents/a")
      .tag("production", "support");
    expect(orch.toConfig().tags).toContain("production");
    expect(orch.toConfig().tags).toContain("support");
  });

  it("deploy returns pending dict", () => {
    const orch = makeRouterOrch();
    const result = orch.deploy();
    expect(result["status"]).toBe("pending");
    expect(result["orchestration"]).toBe("support-pipeline");
  });

  it("deploy target override", () => {
    const orch = makeRouterOrch();
    const result = orch.deploy("cloud-run");
    expect(result["target"]).toBe("cloud-run");
  });

  it("toConfig returns deep copy", () => {
    const orch = new Orchestration("test").addAgent("a", "agents/a");
    const cfg = orch.toConfig();
    cfg.name = "modified";
    expect(orch.toConfig().name).toBe("test");
  });
});

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

describe("Orchestration.validate", () => {
  it("returns empty for valid config", () => {
    expect(makeRouterOrch().validate()).toHaveLength(0);
  });

  it("errors on empty name", () => {
    const orch = new Orchestration("ab");
    (orch as any).config.name = "";
    orch.addAgent("a", "agents/a");
    expect(orch.validate().some((e) => e.includes("name is required"))).toBe(true);
  });

  it("errors on uppercase name", () => {
    const orch = new Orchestration("ab");
    (orch as any).config.name = "MyPipeline";
    orch.addAgent("a", "agents/a");
    expect(orch.validate().some((e) => e.includes("lowercase"))).toBe(true);
  });

  it("errors on non-semver version", () => {
    const orch = makeRouterOrch();
    (orch as any).config.version = "not-a-version";
    expect(orch.validate().some((e) => e.includes("semver"))).toBe(true);
  });

  it("errors on no agents", () => {
    const orch = new Orchestration("empty-orch");
    expect(orch.validate().some((e) => e.includes("at least one agent"))).toBe(true);
  });

  it("errors when supervisor strategy has no supervisor_config", () => {
    const orch = new Orchestration("sup", "supervisor").addAgent("a", "agents/a");
    expect(orch.validate().some((e) => e.includes("supervisor"))).toBe(true);
  });

  it("errors when fan_out_fan_in has no merge_agent", () => {
    const orch = new Orchestration("fo", "fan_out_fan_in").addAgent("w", "agents/w");
    expect(orch.validate().some((e) => e.includes("merge"))).toBe(true);
  });

  it("errors when route target is not a known agent", () => {
    const orch = new Orchestration("rt", "router")
      .addAgent("triage", "agents/triage")
      .withRoute("triage", "x", "nonexistent");
    expect(orch.validate().some((e) => e.includes("nonexistent"))).toBe(true);
  });

  it("errors when fallback is not a known agent", () => {
    const orch = new Orchestration("fb", "router").addAgent("a", "agents/a");
    orch.toConfig(); // force config access
    (orch as any).config.agents["a"].fallback = "ghost";
    expect(orch.validate().some((e) => e.includes("ghost"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Pipeline
// ---------------------------------------------------------------------------

describe("Pipeline", () => {
  it("creates sequential orchestration", () => {
    const pipeline = new Pipeline("research")
      .step("researcher", "agents/researcher")
      .step("summarizer", "agents/summarizer");
    expect(pipeline.toConfig().strategy).toBe("sequential");
    expect(Object.keys(pipeline.toConfig().agents)).toHaveLength(2);
  });

  it("errors when fewer than 2 steps", () => {
    const pipeline = new Pipeline("short").step("only", "agents/only");
    expect(pipeline.validate().some((e) => e.includes("2 steps"))).toBe(true);
  });

  it("valid with 2 steps", () => {
    const pipeline = new Pipeline("two-step")
      .step("a", "agents/a")
      .step("b", "agents/b");
    expect(pipeline.validate()).toHaveLength(0);
  });

  it("step with fallback", () => {
    const pipeline = new Pipeline("fb")
      .step("first", "agents/first")
      .step("second", "agents/second", "first");
    expect(pipeline.toConfig().agents["second"].fallback).toBe("first");
  });
});

// ---------------------------------------------------------------------------
// FanOut
// ---------------------------------------------------------------------------

describe("FanOut", () => {
  it("creates fan_out_fan_in orchestration", () => {
    const analysis = new FanOut("multi-analysis")
      .worker("sentiment", "agents/sentiment")
      .worker("topics", "agents/topics")
      .merge("agents/aggregator");
    expect(analysis.toConfig().strategy).toBe("fan_out_fan_in");
    expect(analysis.toConfig().supervisor_config?.merge_agent).toBe("merger");
  });

  it("custom merge agent name", () => {
    const analysis = new FanOut("analysis")
      .worker("w1", "agents/w1")
      .merge("agents/agg", "aggregator");
    expect(analysis.toConfig().supervisor_config?.merge_agent).toBe("aggregator");
  });

  it("withMergeStrategy sets strategy", () => {
    const analysis = new FanOut("analysis")
      .worker("w1", "agents/w1")
      .merge("agents/merger")
      .withMergeStrategy("majority_vote");
    expect((analysis as any)._mergeStrategy).toBe("majority_vote");
  });

  it("withMergeStrategy throws on invalid strategy", () => {
    expect(() => new FanOut("f").withMergeStrategy("magic" as any)).toThrow();
  });

  it("validates missing merge agent", () => {
    const analysis = new FanOut("no-merge").worker("w1", "agents/w1");
    expect(analysis.validate().some((e) => e.includes("merge"))).toBe(true);
  });

  it("valid with merge agent", () => {
    const analysis = new FanOut("valid")
      .worker("w1", "agents/w1")
      .worker("w2", "agents/w2")
      .merge("agents/merger");
    expect(analysis.validate()).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Supervisor
// ---------------------------------------------------------------------------

describe("Supervisor", () => {
  it("creates supervisor orchestration", () => {
    const workflow = new Supervisor("research-workflow")
      .withSupervisorAgent("coordinator", "agents/coordinator")
      .worker("researcher", "agents/researcher")
      .worker("writer", "agents/writer")
      .withMaxIterations(5);
    expect(workflow.toConfig().strategy).toBe("supervisor");
    expect(workflow.toConfig().supervisor_config?.supervisor_agent).toBe("coordinator");
    expect(workflow.toConfig().supervisor_config?.max_iterations).toBe(5);
  });

  it("validates missing supervisor agent", () => {
    const workflow = new Supervisor("no-sup").worker("w1", "agents/w1");
    expect(workflow.validate().some((e) => e.includes("withSupervisorAgent"))).toBe(true);
  });

  it("valid config", () => {
    const workflow = new Supervisor("valid")
      .withSupervisorAgent("coord", "agents/coord")
      .worker("w1", "agents/w1");
    expect(workflow.validate()).toHaveLength(0);
  });

  it("worker with fallback", () => {
    const workflow = new Supervisor("fb")
      .withSupervisorAgent("coord", "agents/coord")
      .worker("primary", "agents/primary")
      .worker("backup", "agents/backup");
    (workflow as any).config.agents["primary"].fallback = "backup";
    expect(workflow.validate()).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Router classes
// ---------------------------------------------------------------------------

describe("KeywordRouter", () => {
  it("matches keyword", async () => {
    const router = new KeywordRouter({ billing: "billing-agent", broken: "tech" }, "general");
    expect(await router.route("I have a billing question", {})).toBe("billing-agent");
  });

  it("returns default when no match", async () => {
    const router = new KeywordRouter({ billing: "billing" }, "general");
    expect(await router.route("Hello world", {})).toBe("general");
  });

  it("case-insensitive by default", async () => {
    const router = new KeywordRouter({ BILLING: "billing" }, "general");
    expect(await router.route("billing problem", {})).toBe("billing");
  });

  it("case-sensitive mode", async () => {
    const router = new KeywordRouter({ Billing: "billing" }, "general", true);
    expect(await router.route("billing problem", {})).toBe("general");
    expect(await router.route("Billing problem", {})).toBe("billing");
  });
});

describe("IntentRouter", () => {
  it("routes on intent", async () => {
    const router = new IntentRouter({ billing_inquiry: "billing", tech: "tech" }, "general");
    expect(await router.route("...", { intent: "billing_inquiry" })).toBe("billing");
  });

  it("falls back on unknown intent", async () => {
    const router = new IntentRouter({ x: "agent-x" }, "general");
    expect(await router.route("...", { intent: "unknown" })).toBe("general");
  });

  it("falls back on missing intent", async () => {
    const router = new IntentRouter({ x: "agent-x" }, "general");
    expect(await router.route("...", {})).toBe("general");
  });
});

describe("RoundRobinRouter", () => {
  it("distributes in order", async () => {
    const router = new RoundRobinRouter(["a", "b", "c"]);
    const results: string[] = [];
    for (let i = 0; i < 6; i++) {
      results.push(await router.route("msg", {}));
    }
    expect(results).toEqual(["a", "b", "c", "a", "b", "c"]);
  });

  it("single agent always returns that agent", async () => {
    const router = new RoundRobinRouter(["only"]);
    for (let i = 0; i < 3; i++) {
      expect(await router.route("msg", {})).toBe("only");
    }
  });
});

describe("ClassifierRouter", () => {
  it("routes via classify()", async () => {
    class FixedClassifier extends ClassifierRouter {
      async classify(_msg: string) {
        return "billing";
      }
    }
    const router = new FixedClassifier({ billing: "billing-agent" }, "general");
    expect(await router.route("any", {})).toBe("billing-agent");
  });

  it("falls back on unrecognised label", async () => {
    class UnknownClassifier extends ClassifierRouter {
      async classify(_msg: string) {
        return "mystery";
      }
    }
    const router = new UnknownClassifier({}, "general");
    expect(await router.route("msg", {})).toBe("general");
  });
});

// ---------------------------------------------------------------------------
// YAML serialization
// ---------------------------------------------------------------------------

describe("orchestrationToYaml", () => {
  it("includes required fields", () => {
    const cfg = makeRouterOrch().toConfig();
    const yaml = orchestrationToYaml(cfg);
    expect(yaml).toContain("name: support-pipeline");
    expect(yaml).toContain("strategy: router");
    expect(yaml).toContain("agents:");
    expect(yaml).toContain("triage:");
  });

  it("includes shared_state when set", () => {
    const orch = new Orchestration("test")
      .addAgent("a", "agents/a")
      .withSharedState("session_context", "redis");
    const yaml = orchestrationToYaml(orch.toConfig());
    expect(yaml).toContain("shared_state:");
    expect(yaml).toContain("backend: redis");
  });

  it("includes supervisor_config when set", () => {
    const orch = new Orchestration("test", "supervisor")
      .addAgent("coord", "agents/coord")
      .withSupervisor("coord", 7);
    const yaml = orchestrationToYaml(orch.toConfig());
    expect(yaml).toContain("supervisor_config:");
    expect(yaml).toContain("supervisor_agent: coord");
    expect(yaml).toContain("max_iterations: 7");
  });

  it("includes deploy when set", () => {
    const orch = new Orchestration("test")
      .addAgent("a", "agents/a")
      .withDeploy("cloud-run");
    const yaml = orchestrationToYaml(orch.toConfig());
    expect(yaml).toContain("deploy:");
    expect(yaml).toContain("target: cloud-run");
  });

  it("builds a supervisor orchestration", () => {
    const orch = new Orchestration("research", "supervisor", { version: "1.0.0", team: "eng" })
      .addAgent("coordinator", "agents/coordinator")
      .addAgent("researcher", "agents/researcher")
      .withSupervisor("coordinator", 3);

    const config = orch.toConfig();
    expect(config.strategy).toBe("supervisor");
    expect(config.supervisor_config?.supervisor_agent).toBe("coordinator");
    expect(Object.keys(config.agents)).toHaveLength(2);
  });

  it("builds a fan_out_fan_in orchestration", () => {
    const orch = new Orchestration("analysis", "fan_out_fan_in")
      .addAgent("analyst-a", "agents/analyst-a")
      .addAgent("analyst-b", "agents/analyst-b")
      .addAgent("merger", "agents/merger")
      .withMergeAgent("merger");

    const config = orch.toConfig();
    expect(config.strategy).toBe("fan_out_fan_in");
    expect(config.supervisor_config?.merge_agent).toBe("merger");
  });
});
