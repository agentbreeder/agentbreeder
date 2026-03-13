/** Tool definition builder. */

import type { ToolConfig } from "./types";

export class Tool {
  private config: ToolConfig;

  constructor(opts: { name?: string; ref?: string; description?: string }) {
    this.config = { ...opts };
  }

  static fromRef(ref: string): Tool {
    return new Tool({ ref });
  }

  withSchema(schema: Record<string, unknown>): this {
    this.config.schema = schema;
    this.config.type = "function";
    return this;
  }

  toConfig(): ToolConfig {
    return { ...this.config };
  }
}
