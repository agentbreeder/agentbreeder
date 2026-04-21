# AgentBreeder Architecture

## Core Deploy Pipeline

AgentBreeder executes every deployment through a fixed, ordered pipeline:

1. **Parse & Validate YAML** — the agent.yaml is loaded and validated against a JSON Schema
2. **RBAC Check** — permissions are validated before any infrastructure work begins
3. **Dependency Resolution** — all refs (tools, prompts, knowledge bases) are fetched from the registry
4. **Container Build** — a framework-specific Dockerfile is generated and built
5. **Infrastructure Provision** — cloud resources are provisioned via Pulumi
6. **Deploy & Health Check** — the container is deployed and health-checked
7. **Auto-Register in Registry** — the agent is registered in the org-wide registry
8. **Return Endpoint URL** — the live URL is returned to the caller

Every step is atomic. If any step fails, the entire deploy rolls back.

## Governance

Every `agentbreeder deploy` automatically:
- Validates RBAC before any action
- Registers the agent in the registry after success
- Attributes cost to the deploying team
- Writes an audit log entry

There is no "quick deploy" that skips governance. This is intentional.

## Three-Tier Builder Model

AgentBreeder supports three ways to build agents:

- **No Code** — Visual drag-and-drop UI with ReactFlow canvas
- **Low Code** — YAML config (agent.yaml, orchestration.yaml) in any IDE
- **Full Code** — Python/TypeScript SDK with programmatic control

All three compile to the same internal representation and use the same deploy pipeline. Users can "eject" from No Code to YAML, or from YAML to Full Code with the `agentbreeder eject` command.

## Framework Agnosticism

The engine/runtimes/ layer abstracts all framework differences. Supported frameworks:
- LangGraph
- CrewAI
- Claude SDK (Anthropic)
- OpenAI Agents
- Google ADK
- Custom (bring your own)

## Multi-Cloud Deploy Targets

AgentBreeder deploys to: AWS ECS Fargate, AWS App Runner, AWS EKS, GCP Cloud Run, GCP GKE, Azure Container Apps, Kubernetes (self-hosted), and local Docker Compose.
