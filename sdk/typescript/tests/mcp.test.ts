import { describe, it, expect } from "vitest";
import { MCPServe } from "../src/mcp";

describe("MCPServe", () => {
  it("creates server with default name", () => {
    const server = new MCPServe();
    expect(server.toolNames).toEqual([]);
  });

  it("creates server with custom name", () => {
    const server = new MCPServe("my-mcp-server");
    expect(server.toolNames).toEqual([]);
  });

  it("registers tools and returns their names", () => {
    const server = new MCPServe("test-server");

    function greet(args: unknown) {
      return `Hello, ${args}`;
    }

    function search(args: unknown) {
      return `Searching: ${args}`;
    }

    server
      .tool(greet as (...args: unknown[]) => unknown, {
        description: "Greet someone",
        parameters: { name: { type: "string" } },
      })
      .tool(search as (...args: unknown[]) => unknown, {
        description: "Search for something",
        parameters: { query: { type: "string" } },
      });

    expect(server.toolNames).toEqual(["greet", "search"]);
  });

  it("uses function name for tool name", () => {
    const server = new MCPServe();

    function myTool(args: unknown) {
      return args;
    }

    server.tool(myTool as (...args: unknown[]) => unknown, {
      description: "A tool",
      parameters: {},
    });

    expect(server.toolNames).toContain("myTool");
  });

  it("uses fallback name for anonymous functions", () => {
    const server = new MCPServe();

    const anon = Object.defineProperty(
      (..._args: unknown[]) => "result",
      "name",
      { value: "" }
    );

    server.tool(anon, {
      description: "Anonymous tool",
      parameters: {},
    });

    expect(server.toolNames[0]).toBe("tool_0");
  });

  it("tool() is chainable", () => {
    const server = new MCPServe();

    function toolA() { return "a"; }
    function toolB() { return "b"; }

    const result = server
      .tool(toolA as (...args: unknown[]) => unknown, { description: "A", parameters: {} })
      .tool(toolB as (...args: unknown[]) => unknown, { description: "B", parameters: {} });

    expect(result).toBe(server);
    expect(server.toolNames).toHaveLength(2);
  });
});
