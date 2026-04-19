import { describe, it, expect } from "vitest";
import { validateAgent } from "../src/validation";
import type { AgentConfig } from "../src/types";

function makeValidConfig(overrides?: Partial<AgentConfig>): AgentConfig {
  return {
    name: "test-agent",
    version: "1.0.0",
    team: "engineering",
    owner: "owner@company.com",
    framework: "langgraph",
    model: { primary: "claude-sonnet-4-6" },
    deploy: { cloud: "aws" },
    ...overrides,
  };
}

describe("validateAgent", () => {
  it("returns no errors for a valid config", () => {
    const errors = validateAgent(makeValidConfig());
    expect(errors).toEqual([]);
  });

  it("requires name", () => {
    const errors = validateAgent(makeValidConfig({ name: "" }));
    expect(errors).toContain("name is required");
  });

  it("requires version", () => {
    const errors = validateAgent(makeValidConfig({ version: "" }));
    expect(errors).toContain("version is required");
  });

  it("requires team", () => {
    const errors = validateAgent(makeValidConfig({ team: "" }));
    expect(errors).toContain("team is required");
  });

  it("requires framework", () => {
    const errors = validateAgent(makeValidConfig({ framework: "" as "custom" }));
    expect(errors).toContain("framework is required");
  });

  it("requires model.primary", () => {
    const errors = validateAgent(makeValidConfig({ model: { primary: "" } }));
    expect(errors).toContain("model.primary is required");
  });

  it("requires deploy.cloud", () => {
    const errors = validateAgent(makeValidConfig({ deploy: { cloud: "" as "aws" } }));
    expect(errors).toContain("deploy.cloud is required");
  });

  it("rejects non-semver version", () => {
    const errors = validateAgent(makeValidConfig({ version: "v1" }));
    expect(errors).toContain("version must be semver (e.g. 1.0.0)");
  });

  it("accepts versions with pre-release identifiers", () => {
    const errors = validateAgent(makeValidConfig({ version: "1.2.3-alpha.1" }));
    expect(errors).not.toContain("version must be semver (e.g. 1.0.0)");
  });

  it("collects multiple errors", () => {
    const errors = validateAgent(makeValidConfig({ name: "", team: "" }));
    expect(errors.length).toBeGreaterThan(1);
  });
});
