import { describe, it, expect } from "vitest";
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
});

describe("Tool", () => {
  it("creates from ref", () => {
    const tool = Tool.fromRef("tools/search");
    expect(tool.toConfig().ref).toBe("tools/search");
  });
});
