/**
 * Unit tests for agent-yaml-emit.ts — round-trip and quoting correctness.
 */
import { describe, it, expect } from "vitest";
import { formDataToYaml, emptyFormData, roundTripFormData } from "./agent-yaml-emit";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeData(overrides: Partial<ReturnType<typeof emptyFormData>> = {}) {
  return { ...emptyFormData(), ...overrides };
}

// ---------------------------------------------------------------------------
// Basic round-trip
// ---------------------------------------------------------------------------

describe("round-trip", () => {
  it("round-trips a minimal form data object", () => {
    const data = makeData({ name: "my-agent", team: "engineering", owner: "alice@example.com" });
    const result = roundTripFormData(data);
    expect(result.name).toBe("my-agent");
    expect(result.team).toBe("engineering");
    expect(result.owner).toBe("alice@example.com");
  });
});

// ---------------------------------------------------------------------------
// Quote escaping in YAML emit (Fix 2)
// ---------------------------------------------------------------------------

describe("formDataToYaml — quoted scalar escaping", () => {
  it("escapes double-quotes in description", () => {
    const data = makeData({ description: 'Respond to "VIP" tickets' });
    const yaml = formDataToYaml(data);
    // The YAML line must contain the escaped form, not a bare double-quote
    expect(yaml).toContain('description: "Respond to \\"VIP\\" tickets"');
  });

  it("round-trips a description containing double-quotes", () => {
    const data = makeData({ description: 'Handle "priority" requests' });
    const result = roundTripFormData(data);
    expect(result.description).toBe('Handle "priority" requests');
  });

  it("escapes backslashes in description", () => {
    const data = makeData({ description: "Path: C:\\Users\\agent" });
    const yaml = formDataToYaml(data);
    expect(yaml).toContain('description: "Path: C:\\\\Users\\\\agent"');
  });

  it("round-trips a description containing backslashes", () => {
    const data = makeData({ description: "Path: C:\\Users\\agent" });
    const result = roundTripFormData(data);
    expect(result.description).toBe("Path: C:\\Users\\agent");
  });

  it("escapes double-quotes in prompts.system inline string", () => {
    const data = makeData({ prompts: { system: 'You are a "helpful" assistant.' } });
    const yaml = formDataToYaml(data);
    expect(yaml).toContain('  system: "You are a \\"helpful\\" assistant."');
  });

  it("round-trips a prompts.system containing double-quotes", () => {
    const data = makeData({ prompts: { system: 'You are a "helpful" assistant.' } });
    const result = roundTripFormData(data);
    expect(result.prompts.system).toBe('You are a "helpful" assistant.');
  });

  it("does not alter a plain description with no special characters", () => {
    const data = makeData({ description: "Handle support tickets" });
    const yaml = formDataToYaml(data);
    expect(yaml).toContain('description: "Handle support tickets"');
  });
});
