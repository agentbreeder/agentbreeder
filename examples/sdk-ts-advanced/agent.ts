import { Agent, Tool, Memory, validateAgent } from "@agentbreeder/sdk";

const agent = new Agent("data-analyst", {
  version: "2.0.0",
  team: "data",
  owner: "data@company.com",
  framework: "langgraph",
})
  .withModel("claude-sonnet-4-6", { maxTokens: 4096 })
  .withMemory("buffer_window", { maxMessages: 20 })
  .withDeploy("gcp", { runtime: "cloud-run" })
  .use((msg, ctx) => ({ ...ctx, processed: true }))
  .on("tool_call", (toolName, args) => console.log(`Tool called: ${String(toolName)}`))
  .on("error", (err) => console.error("Agent error:", err));

const errors = validateAgent(agent.toConfig());
if (errors.length > 0) {
  console.error("Validation failed:", errors);
  process.exit(1);
}

console.log(agent.toYaml());
