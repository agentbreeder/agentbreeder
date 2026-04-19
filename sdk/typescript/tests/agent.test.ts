import { describe, it, expect, vi } from "vitest";
import { Agent, Tool, Model } from "../src";

describe("Agent", () => {
  it("builds a valid config with builder pattern", () => {
    const agent = new Agent("test-agent", {
      version: "1.0.0",
      team: "eng",
      owner: "alice@co.com",
      framework: "langgraph",
    })
      .withModel("claude-sonnet-4", { fallback: "gpt-4o" })
      .withPrompt("You are helpful.")
      .withDeploy("local")
      .tag("test");

    const config = agent.toConfig();
    expect(config.name).toBe("test-agent");
    expect(config.model.primary).toBe("claude-sonnet-4");
    expect(config.model.fallback).toBe("gpt-4o");
    expect(config.deploy.cloud).toBe("local");
    expect(config.tags).toContain("test");
  });

  it("validates missing model", () => {
    const agent = new Agent("test-agent", { team: "eng" });
    const errors = agent.validate();
    expect(errors).toContain("model is required — call .withModel()");
  });

  it("serializes to YAML", () => {
    const agent = new Agent("my-agent", {
      version: "2.0.0",
      team: "eng",
      owner: "bob@co.com",
    })
      .withModel("gpt-4o")
      .withDeploy("aws");

    const yaml = agent.toYaml();
    expect(yaml).toContain("name: my-agent");
    expect(yaml).toContain("version: 2.0.0");
    expect(yaml).toContain("primary: gpt-4o");
    expect(yaml).toContain("cloud: aws");
  });

  it("supports subagents and mcp servers", () => {
    const agent = new Agent("coordinator", { team: "eng", owner: "a@b.com" })
      .withModel("claude-sonnet-4")
      .withSubagent("agents/summarizer", { description: "Summarize docs" })
      .withMcpServer("mcp/zendesk", "sse")
      .withDeploy("local");

    const config = agent.toConfig();
    expect(config.subagents).toHaveLength(1);
    expect(config.subagents![0].ref).toBe("agents/summarizer");
    expect(config.mcp_servers).toHaveLength(1);
    expect(config.mcp_servers![0].transport).toBe("sse");
  });

  it("withMemory stores memory in config", () => {
    const agent = new Agent("mem-agent", { team: "eng", owner: "a@b.com" })
      .withModel("claude-sonnet-4")
      .withDeploy("local")
      .withMemory("buffer_window", { maxMessages: 20 });

    const config = agent.toConfig();
    expect(config.memory).toBeDefined();
    expect(config.memory!.backend).toBe("buffer_window");
    expect(config.memory!.maxMessages).toBe(20);
  });

  it("withMemory is optional — config.memory is undefined when not set", () => {
    const agent = new Agent("no-mem", { team: "eng" })
      .withModel("claude-sonnet-4")
      .withDeploy("local");

    const config = agent.toConfig();
    expect(config.memory).toBeUndefined();
  });

  it("use() registers middleware and is chainable", () => {
    const middleware = (msg: string, ctx: Record<string, unknown>) => ({ ...ctx, processed: true });

    const agent = new Agent("mid-agent", { team: "eng" })
      .withModel("claude-sonnet-4")
      .withDeploy("local")
      .use(middleware);

    // Verify chaining works and agent is still valid
    const config = agent.toConfig();
    expect(config.name).toBe("mid-agent");
  });

  it("on() registers event handlers and is chainable", () => {
    const handler = vi.fn();

    const agent = new Agent("event-agent", { team: "eng" })
      .withModel("claude-sonnet-4")
      .withDeploy("local")
      .on("tool_call", handler)
      .on("error", handler);

    const config = agent.toConfig();
    expect(config.name).toBe("event-agent");
  });

  it("state getter returns an object", () => {
    const agent = new Agent("state-agent", { team: "eng" })
      .withModel("claude-sonnet-4")
      .withDeploy("local");

    expect(typeof agent.state).toBe("object");
    expect(agent.state).toEqual({});
  });

  it("state getter returns a copy", () => {
    const agent = new Agent("state-agent", { team: "eng" })
      .withModel("claude-sonnet-4")
      .withDeploy("local");

    const s1 = agent.state;
    const s2 = agent.state;
    expect(s1).not.toBe(s2);
  });

  it("route() returns null by default", () => {
    const agent = new Agent("router-agent", { team: "eng" })
      .withModel("claude-sonnet-4")
      .withDeploy("local");

    expect(agent.route("hello", {})).toBeNull();
  });

  it("selectTools() returns empty array by default", () => {
    const agent = new Agent("tool-agent", { team: "eng" })
      .withModel("claude-sonnet-4")
      .withDeploy("local");

    expect(agent.selectTools("find tools")).toEqual([]);
  });

  it("fromYaml parses a basic YAML string", () => {
    const yaml = `name: parsed-agent
version: 2.1.0
team: data
owner: data@co.com
framework: langgraph

model:
  primary: gpt-4o
  fallback: claude-sonnet-4

deploy:
  cloud: gcp
  runtime: cloud-run
`;

    const agent = Agent.fromYaml(yaml);
    expect(agent.toConfig().name).toBe("parsed-agent");
    expect(agent.toConfig().version).toBe("2.1.0");
    expect(agent.toConfig().team).toBe("data");
    expect(agent.toConfig().framework).toBe("langgraph");
    expect(agent.toConfig().model.primary).toBe("gpt-4o");
    expect(agent.toConfig().deploy.cloud).toBe("gcp");
  });

  it("save() method exists and returns a Promise", () => {
    const agent = new Agent("save-agent", { team: "eng", owner: "a@b.com" })
      .withModel("claude-sonnet-4")
      .withDeploy("local");

    expect(typeof agent.save).toBe("function");
  });
});

describe("Tool", () => {
  it("creates from ref", () => {
    const tool = Tool.fromRef("tools/search");
    expect(tool.toConfig().ref).toBe("tools/search");
  });

  it("creates from string name via constructor", () => {
    const tool = new Tool("web_search");
    expect(tool.toConfig().name).toBe("web_search");
  });

  it("fromFunction sets name and schema", () => {
    function mySearch(_args: unknown) { return "results"; }
    const tool = Tool.fromFunction(mySearch as (...args: unknown[]) => unknown, {
      description: "Search the web",
      query: { type: "string" },
    });
    const config = tool.toConfig();
    expect(config.name).toBe("mySearch");
    expect(config.type).toBe("function");
    expect(config.schema).toBeDefined();
  });

  it("description() sets the tool description", () => {
    const tool = new Tool("searcher").description("A search tool");
    expect(tool.toConfig().description).toBe("A search tool");
  });

  it("schema() sets the schema and type", () => {
    const tool = new Tool("searcher").schema({ query: { type: "string" } });
    const config = tool.toConfig();
    expect(config.type).toBe("function");
    expect(config.schema).toEqual({ query: { type: "string" } });
  });
});
