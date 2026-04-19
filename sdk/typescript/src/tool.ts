/** Tool definition builder. */

import type { ToolConfig } from "./types";

export class Tool {
  private config: ToolConfig;

  constructor(opts: { name?: string; ref?: string; description?: string } | string) {
    if (typeof opts === "string") {
      this.config = { name: opts };
    } else {
      this.config = { ...opts };
    }
  }

  static fromRef(ref: string): Tool {
    return new Tool({ ref });
  }

  static fromFunction(fn: (...args: unknown[]) => unknown, schema: Record<string, unknown>): Tool {
    const tool = new Tool({ name: fn.name || "unnamed_tool" });
    tool.config.type = "function";
    tool.config.schema = schema;
    if (typeof schema.description === "string") {
      tool.config.description = schema.description;
    }
    return tool;
  }

  description(desc: string): this {
    this.config.description = desc;
    return this;
  }

  schema(schema: Record<string, unknown>): this {
    this.config.schema = schema;
    this.config.type = "function";
    return this;
  }

  withSchema(schema: Record<string, unknown>): this {
    return this.schema(schema);
  }

  toConfig(): ToolConfig {
    return { ...this.config };
  }
}
