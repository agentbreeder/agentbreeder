/** Model configuration builder. */

import type { ModelConfig } from "./types";

export class Model {
  private config: ModelConfig;

  constructor(primary: string) {
    this.config = { primary };
  }

  fallback(model: string): this {
    this.config.fallback = model;
    return this;
  }

  temperature(temp: number): this {
    this.config.temperature = temp;
    return this;
  }

  maxTokens(tokens: number): this {
    this.config.max_tokens = tokens;
    return this;
  }

  gateway(gw: string): this {
    this.config.gateway = gw;
    return this;
  }

  toConfig(): ModelConfig {
    return { ...this.config };
  }
}
