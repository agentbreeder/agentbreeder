import { describe, it, expect } from "vitest";
import { Orchestration } from "../src";

describe("Orchestration", () => {
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
