/** Memory configuration for AgentBreeder agents. */

export interface MemoryConfig {
  backend: string;
  maxMessages?: number;
  connectionString?: string;
}

export class Memory {
  private _config: MemoryConfig;

  private constructor(config: MemoryConfig) {
    this._config = config;
  }

  static bufferWindow(maxMessages?: number, opts?: Partial<MemoryConfig>): Memory {
    return new Memory({ backend: "buffer_window", maxMessages: maxMessages ?? 10, ...opts });
  }

  static buffer(opts?: Partial<MemoryConfig>): Memory {
    return new Memory({ backend: "buffer", ...opts });
  }

  static postgresql(opts?: Partial<MemoryConfig>): Memory {
    return new Memory({ backend: "postgresql", ...opts });
  }

  static fromConfig(config: MemoryConfig): Memory {
    return new Memory({ ...config });
  }

  toConfig(): MemoryConfig {
    return { ...this._config };
  }
}
