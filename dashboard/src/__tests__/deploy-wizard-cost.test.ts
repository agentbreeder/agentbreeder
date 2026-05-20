import { describe, expect, it } from "vitest";
import { estimateMonthly, COST_TABLE } from "@/lib/deploy-wizard-cost";

describe("estimateMonthly", () => {
  it("gcp us-central1 with memory + public", () => {
    const r = estimateMonthly("gcp", "us-central1", { hasMemory: true, isPublic: true });
    expect(r.low).toBeGreaterThan(0);
    expect(r.high).toBeGreaterThanOrEqual(r.low);
    expect(r.lines.some((l) => l.resource.includes("Cloud SQL"))).toBe(true);
  });

  it("aws us-east-1 minimal (no memory, no public)", () => {
    const r = estimateMonthly("aws", "us-east-1", { hasMemory: false, isPublic: false });
    expect(r.lines.some((l) => l.resource.includes("ALB"))).toBe(false);
    expect(r.lines.some((l) => l.resource.includes("RDS"))).toBe(false);
  });

  it("azure westeurope minimal", () => {
    const r = estimateMonthly("azure", "westeurope", { hasMemory: false, isPublic: false });
    expect(r.low).toBeGreaterThanOrEqual(0);
  });

  it("unknown region returns unsupported status", () => {
    const r = estimateMonthly("aws", "ap-mars-1", { hasMemory: false, isPublic: false });
    expect(r.status).toBe("unsupported");
    expect(r.lines).toEqual([]);
  });

  it("matrix is dense for the supported regions", () => {
    expect(COST_TABLE.aws["us-east-1"]).toBeDefined();
    expect(COST_TABLE.aws["us-west-2"]).toBeDefined();
    expect(COST_TABLE.aws["eu-west-1"]).toBeDefined();
    expect(COST_TABLE.gcp["us-central1"]).toBeDefined();
    expect(COST_TABLE.gcp["us-east1"]).toBeDefined();
    expect(COST_TABLE.gcp["europe-west1"]).toBeDefined();
    expect(COST_TABLE.azure["eastus"]).toBeDefined();
    expect(COST_TABLE.azure["westus2"]).toBeDefined();
    expect(COST_TABLE.azure["westeurope"]).toBeDefined();
  });

  it("estimate range adds tolerance padding (high > low)", () => {
    const r = estimateMonthly("aws", "us-east-1", { hasMemory: true, isPublic: true });
    expect(r.high).toBeGreaterThan(r.low);
  });
});
