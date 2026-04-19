/** MCPServe — build MCP servers from TypeScript functions. */

export interface McpToolSchema {
  description: string;
  parameters: Record<string, unknown>;
}

export class MCPServe {
  private _name: string;
  private _tools: Array<{ fn: (...args: unknown[]) => unknown; name: string; schema: McpToolSchema }> = [];

  constructor(name?: string) {
    this._name = name ?? "agentbreeder-mcp-server";
  }

  tool(fn: (...args: unknown[]) => unknown, schema: McpToolSchema): this {
    this._tools.push({ fn, name: fn.name || `tool_${this._tools.length}`, schema });
    return this;
  }

  get toolNames(): string[] {
    return this._tools.map((t) => t.name);
  }

  async run(): Promise<void> {
    // Starts a stdio MCP server using @modelcontextprotocol/sdk if available,
    // otherwise throws a helpful error message.
    // Dynamic imports are intentional: the MCP SDK is an optional peer dependency.
    let Server: new (info: unknown, opts: unknown) => {
      setRequestHandler: (schema: unknown, handler: (req: unknown) => Promise<unknown>) => void;
      connect: (transport: unknown) => Promise<void>;
    };
    let StdioServerTransport: new () => unknown;
    let ListToolsRequestSchema: unknown;
    let CallToolRequestSchema: unknown;

    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const serverMod = await (Function('return import("@modelcontextprotocol/sdk/server/index.js")')() as Promise<any>);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const stdioMod = await (Function('return import("@modelcontextprotocol/sdk/server/stdio.js")')() as Promise<any>);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const typesMod = await (Function('return import("@modelcontextprotocol/sdk/types.js")')() as Promise<any>);

      Server = serverMod.Server;
      StdioServerTransport = stdioMod.StdioServerTransport;
      ListToolsRequestSchema = typesMod.ListToolsRequestSchema;
      CallToolRequestSchema = typesMod.CallToolRequestSchema;
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      if (message.includes("Cannot find module") || message.includes("ERR_MODULE_NOT_FOUND")) {
        throw new Error(
          "MCPServe requires @modelcontextprotocol/sdk. Install it: npm install @modelcontextprotocol/sdk"
        );
      }
      throw err;
    }

    const tools = this._tools;
    const serverName = this._name;

    const server = new Server(
      { name: serverName, version: "1.0.0" },
      { capabilities: { tools: {} } }
    );

    server.setRequestHandler(ListToolsRequestSchema, async () => ({
      tools: tools.map((t) => ({
        name: t.name,
        description: t.schema.description,
        inputSchema: { type: "object", ...t.schema.parameters },
      })),
    }));

    server.setRequestHandler(CallToolRequestSchema, async (req: unknown) => {
      const request = req as { params: { name: string; arguments?: Record<string, unknown> } };
      const tool = tools.find((t) => t.name === request.params.name);
      if (!tool) throw new Error(`Tool not found: ${request.params.name}`);
      const result = await tool.fn(request.params.arguments ?? {});
      return { content: [{ type: "text", text: String(result) }] };
    });

    const transport = new StdioServerTransport();
    await server.connect(transport);
  }
}
