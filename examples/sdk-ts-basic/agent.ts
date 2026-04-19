import { Agent, Tool } from "@agentbreeder/sdk";

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

console.log(agent.toYaml());
