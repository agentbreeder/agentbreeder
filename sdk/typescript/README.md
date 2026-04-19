# @agentbreeder/sdk

TypeScript SDK for [AgentBreeder](https://agent-breeder.com) — Define Once. Deploy Anywhere.

Build, configure, and deploy AI agents to AWS, GCP, or any supported cloud with a fluent TypeScript API.

## Installation

```bash
npm install @agentbreeder/sdk
```

## Quick Start

```typescript
import { Agent, Tool, validateAgent } from "@agentbreeder/sdk";

const searchTool = new Tool("web_search")
  .description("Search the web for information")
  .schema({ query: { type: "string", description: "Search query" } });

const agent = new Agent("research-assistant", {
  version: "1.0.0",
  description: "A helpful research assistant",
  team: "engineering",
  owner: "team@company.com",
  framework: "claude_sdk",
})
  .withModel("claude-sonnet-4-6", { temperature: 0.7 })
  .withTool(searchTool)
  .withGuardrail("pii_detection")
  .withDeploy("aws", { runtime: "ecs-fargate" });

// Validate before deploying
const errors = validateAgent(agent.toConfig());
if (errors.length > 0) {
  console.error("Validation errors:", errors);
  process.exit(1);
}

// Serialize to agent.yaml
console.log(agent.toYaml());

// Deploy
const result = await agent.deploy("aws");
console.log("Deployed:", result);
```

## API Reference

### `Agent`

The main builder class for defining an agent.

```typescript
const agent = new Agent(name: string, opts?: AgentOptions)
```

**AgentOptions:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | `string` | `"1.0.0"` | SemVer version |
| `description` | `string` | `""` | Human-readable description |
| `team` | `string` | `"default"` | Team owning this agent |
| `owner` | `string` | `""` | Owner email |
| `framework` | `FrameworkType` | `"custom"` | Runtime framework |
| `tags` | `string[]` | `[]` | Discovery tags |

**Builder methods (all return `this` for chaining):**

| Method | Description |
|--------|-------------|
| `.withModel(primary, opts?)` | Set primary model and optional fallback/temperature/maxTokens |
| `.withTool(tool)` | Add a Tool to the agent |
| `.withSubagent(ref, opts?)` | Reference another agent as a subagent |
| `.withMcpServer(ref, transport?)` | Add an MCP server reference |
| `.withPrompt(system)` | Set the system prompt |
| `.withGuardrail(name)` | Add a guardrail (e.g. `"pii_detection"`) |
| `.withDeploy(cloud, opts?)` | Set deployment target and options |
| `.withMemory(backend, opts?)` | Configure conversation memory |
| `.tag(...tags)` | Add discovery tags |
| `.use(middleware)` | Register a middleware function |
| `.on(event, handler)` | Register an event handler |

**Other methods:**

| Method | Description |
|--------|-------------|
| `.toConfig()` | Returns the `AgentConfig` object |
| `.toYaml()` | Serializes to `agent.yaml` string |
| `.validate()` | Returns array of validation error strings |
| `.route(message, context)` | Custom routing logic (returns `null` by default — override in subclass) |
| `.selectTools(message)` | Select tools for a message (returns `[]` by default — override in subclass) |
| `.save(path)` | Write `agent.yaml` to disk |
| `.deploy(target?)` | Deploy the agent via the AgentBreeder CLI/API |
| `Agent.fromYaml(yaml)` | Parse an agent from a YAML string |
| `Agent.fromFile(path)` | Load an agent from a YAML file (async) |

**Events registered via `.on()`:**

| Event | Args | Description |
|-------|------|-------------|
| `"tool_call"` | `(toolName, args)` | Fired when a tool is invoked |
| `"turn_start"` | `(message)` | Fired at the start of a turn |
| `"turn_end"` | `(result)` | Fired at the end of a turn |
| `"error"` | `(error)` | Fired on errors |

**Memory backends (used with `.withMemory()`):**

| Backend | Description |
|---------|-------------|
| `"buffer_window"` | Fixed-size sliding window of recent messages |
| `"buffer"` | Unbounded in-memory buffer |
| `"postgresql"` | Persistent PostgreSQL-backed memory |

---

### `Tool`

Defines a tool that the agent can call.

```typescript
// From a registry reference
const tool = Tool.fromRef("tools/zendesk-mcp");

// From a string name (fluent builder)
const tool = new Tool("web_search")
  .description("Search the web")
  .schema({ query: { type: "string" } });

// From a TypeScript function
function mySearch(args: { query: string }) { return fetch(`/search?q=${args.query}`); }
const tool = Tool.fromFunction(mySearch, {
  description: "Search the web",
  query: { type: "string" },
});
```

---

### `Memory`

Configure conversation memory backends.

```typescript
import { Memory } from "@agentbreeder/sdk";

// Sliding window — keeps last N messages (default: 10)
const mem = Memory.bufferWindow(20);

// Unbounded in-memory buffer
const mem = Memory.buffer();

// PostgreSQL-backed persistent memory
const mem = Memory.postgresql({ connectionString: "postgresql://localhost/mydb" });

// Inspect config
mem.toConfig(); // { backend: "buffer_window", maxMessages: 20 }
```

Use with an Agent via `.withMemory()`:

```typescript
agent.withMemory("buffer_window", { maxMessages: 20 });
```

---

### `MCPServe`

Build and run an MCP (Model Context Protocol) server from TypeScript functions.

Requires the optional peer dependency:

```bash
npm install @modelcontextprotocol/sdk
```

```typescript
import { MCPServe } from "@agentbreeder/sdk";

const server = new MCPServe("my-tools-server");

server
  .tool(
    function search(args) { return fetchResults(args.query); },
    { description: "Search for information", parameters: { query: { type: "string" } } }
  )
  .tool(
    function sum(args) { return Number(args.a) + Number(args.b); },
    { description: "Add two numbers", parameters: { a: { type: "number" }, b: { type: "number" } } }
  );

console.log(server.toolNames); // ["search", "sum"]

// Starts a stdio MCP server — connect via Claude Desktop or any MCP client
await server.run();
```

---

### `validateAgent`

Validate an `AgentConfig` before deploying.

```typescript
import { validateAgent } from "@agentbreeder/sdk";

const errors = validateAgent(agent.toConfig());
// Returns string[] — empty array if valid

if (errors.length > 0) {
  console.error("Invalid agent config:", errors);
  process.exit(1);
}
```

**Checks performed:**
- `name` is required
- `version` is required and must be semver (e.g. `1.0.0`)
- `team` is required
- `framework` is required
- `model.primary` is required
- `deploy.cloud` is required

---

### Types

All public types are re-exported from the package root:

```typescript
import type {
  AgentConfig,
  CloudType,       // "aws" | "gcp" | "kubernetes" | "local"
  DeployConfig,
  FrameworkType,   // "langgraph" | "crewai" | "claude_sdk" | "openai_agents" | "google_adk" | "custom"
  MemoryConfig,
  McpToolSchema,
  ModelConfig,
  ToolConfig,
  Visibility,      // "public" | "team" | "private"
} from "@agentbreeder/sdk";
```

---

## Advanced Usage

### Middleware

Middleware functions run before each agent turn, allowing you to enrich context:

```typescript
agent
  .use((message, ctx) => ({ ...ctx, userId: getCurrentUser() }))
  .use((message, ctx) => ({ ...ctx, timestamp: Date.now() }));
```

### Event Handlers

```typescript
agent
  .on("tool_call", (toolName, args) => {
    metrics.increment("tool.call", { tool: String(toolName) });
  })
  .on("error", (err) => {
    logger.error("Agent error", { error: err });
  });
```

### Load from YAML

```typescript
// From a string
const agent = Agent.fromYaml(yamlString);

// From a file
const agent = await Agent.fromFile("./agent.yaml");
```

### Save to YAML

```typescript
await agent.save("./agent.yaml");
```

---

## Examples

- [Basic example](../../examples/sdk-ts-basic/) — simple agent with a tool, deployed to AWS
- [Advanced example](../../examples/sdk-ts-advanced/) — memory, middleware, event handlers, validation

---

## License

Apache-2.0
