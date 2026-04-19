import { describe, it, expect } from "vitest";
import { Memory } from "../src/memory";

describe("Memory", () => {
  it("creates buffer_window memory with default maxMessages", () => {
    const mem = Memory.bufferWindow();
    const config = mem.toConfig();
    expect(config.backend).toBe("buffer_window");
    expect(config.maxMessages).toBe(10);
  });

  it("creates buffer_window memory with custom maxMessages", () => {
    const mem = Memory.bufferWindow(25);
    const config = mem.toConfig();
    expect(config.backend).toBe("buffer_window");
    expect(config.maxMessages).toBe(25);
  });

  it("creates buffer memory", () => {
    const mem = Memory.buffer();
    const config = mem.toConfig();
    expect(config.backend).toBe("buffer");
    expect(config.maxMessages).toBeUndefined();
  });

  it("creates postgresql memory", () => {
    const mem = Memory.postgresql({ connectionString: "postgresql://localhost/db" });
    const config = mem.toConfig();
    expect(config.backend).toBe("postgresql");
    expect(config.connectionString).toBe("postgresql://localhost/db");
  });

  it("toConfig returns a copy, not the original", () => {
    const mem = Memory.buffer();
    const config1 = mem.toConfig();
    const config2 = mem.toConfig();
    expect(config1).not.toBe(config2);
    expect(config1).toEqual(config2);
  });

  it("accepts extra opts override for bufferWindow", () => {
    const mem = Memory.bufferWindow(5, { backend: "custom_window" });
    const config = mem.toConfig();
    expect(config.backend).toBe("custom_window");
    expect(config.maxMessages).toBe(5);
  });
});
